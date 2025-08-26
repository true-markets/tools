import asyncio
import atexit
import os
import queue
import random
import signal
import sys
import threading
import time
from datetime import datetime

# our modules
from market_maker.local_data import truex
from market_maker.settings import settings
from market_maker.utils import constants, log, math
from market_maker.utils.tick import GetQuoteTick

# Enhanced features (optional imports)
try:
    from market_maker.external_data.base import ExternalDataManager
    from market_maker.external_data.coinbase import (
        CoinbaseProvider,
        CoinbaseRESTProvider,
    )
    from market_maker.pricing import (
        ConsensusModel,
        LocalMarketAwareModel,
        MomentumModel,
        PricingModelManager,
        SimpleSpreadModel,
    )

    ENHANCED_FEATURES_AVAILABLE = True
except ImportError:
    ENHANCED_FEATURES_AVAILABLE = False

# Order adjustment system (always available)
try:
    from market_maker.order_adjustment import OrderAdjustmentTracker

    ORDER_ADJUSTMENT_AVAILABLE = True
except ImportError:
    ORDER_ADJUSTMENT_AVAILABLE = False

#
# Helpers
#
watched_files_mtimes = [(f, os.path.getmtime(f)) for f in settings.WATCHED_FILES]
logger = log.setup_custom_logger("market_maker")

# global node counter when called should increment and return previous value
node_counter = -1


def node():
    global node_counter
    node_counter += 1
    return node_counter


class MarketInterface:
    def __init__(self, symbol, dry_run=False, queue=None):
        self.dry_run = dry_run
        self.symbol = symbol
        self.truex = truex.TrueX(
            rest_url=settings.BASE_REST_URL,
            ws_url=settings.BASE_WS_URL,
            symbol=self.symbol,
            apiKey=settings.API_KEY,
            apiSecret=settings.API_SECRET,
            orderIDPrefix=settings.ORDERID_PREFIX,
            orderNode=node(),
            postOnly=settings.POST_ONLY,
            timeout=settings.TIMEOUT,
            queue=queue,
        )
        self.client_id = None
        self.instrument_id = None
        self.quoting = False

    def get_market(self):
        logger.info("Subscribing to market data for symbol: %s" % self.symbol)
        self.truex.Market(self.symbol)

    def get_instrument(self):
        logger.info("Subscribing to instrument data for symbol: %s" % self.symbol)
        self.truex.Instrument(self.symbol)

    def get_instrument_id(self):
        if not self.instrument_id:
            self.instrument_id = self.truex.InstrumentData(self.symbol)[0]["id"]
        return self.instrument_id

    def get_client(self):
        if self.client_id:
            return self.client_id
        # return JSON object of client data
        response = self.truex.Client()
        for entry in response:
            if entry["info"]["mnemonic"] == settings.API_USER:
                logger.info("Found matching client ID: %s" % entry["id"])
                self.client_id = entry["id"]
                return self.client_id

        logger.error("No matching ID found for user: %s." % settings.TRUEX_USER)
        raise Exception("No matching ID found for user: %s." % settings.TRUEX_USER)

    def get_delta(self):
        return self.get_position()["qty"]

    def get_position(self):
        return self.truex.Position(self.symbol)

    def get_base_balance(self):
        return self.truex.BaseBalance()

    def get_quote_balance(self):
        return self.truex.QuoteBalance()

    def get_orders(self):
        if self.dry_run:
            return []
        orders = self.truex.OpenOrders()
        return [
            o for o in orders if o["order_info"]["instrument_id"] == self.instrument_id
        ]

    def get_highest_buy(self):
        buys = [o for o in self.get_orders() if o["order_info"]["side"] == "Buy"]
        if not len(buys):
            return {"price": -(2**32)}
        highest_buy = max(buys or [], key=lambda o: o["order_info"]["price"])
        return highest_buy if highest_buy else {"price": -(2**32)}

    def get_lowest_sell(self):
        sells = [o for o in self.get_orders() if o["order_info"]["side"] == "Sell"]
        if not len(sells):
            return {"price": 2**32}
        lowest_sell = min(sells or [], key=lambda o: o["order_info"]["price"])
        return (
            lowest_sell if lowest_sell else {"price": 2**32}
        )  # ought to be enough for anyone

    def get_ticker(self):
        return self.truex.Ticker(self.symbol)

    def create_orders(self, orders):
        if self.dry_run:
            return orders
        return self.truex.CreateOrders(orders)

    def amend_orders(self, orders):
        if self.dry_run:
            return orders
        return self.truex.AmendOrders(orders)

    def cancel_orders(self, orders):
        for order in orders:
            logger.info("Cancelling order %s" % order["external_id"])
            self.truex.CancelOrder(order["id"])

    def cancel_order(self, order_id):
        """Cancel a single order by ID."""
        if self.dry_run:
            return True
        try:
            logger.info("Cancelling order %s" % order_id)
            result = self.truex.CancelOrder(order_id)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self):
        if self.dry_run:
            return
        orders = self.get_orders()
        logger.info("Cancelling all open orders.")
        for order in orders:
            logger.info("Cancelling order %s" % order["external_id"])
            self.truex.CancelOrder(order["id"])

    # Checks
    def check_market(self):
        # Check if the market is open
        if not self.truex.IsMarketOpen(self.symbol):
            logger.error("Market is closed right now. Please try again later.")
            # raise Exception("Market is closed right now. Please try again later.")
        elif not self.quoting:
            logger.info("Market is open. Starting market maker.")
            self.quoting = True
            self.get_market()

    def check_orderbook(self):
        # Check if the orderbook is sane
        return True

    def exit(self):
        self.truex.Exit()


class OrderManager:
    def __init__(
        self,
        enable_external_data=None,
        enable_pricing_models=None,
        auto_start_enhanced=False,
    ):
        """Initialize OrderManager with optional enhanced features.

        Args:
            enable_external_data: Enable external data integration (default: from settings)
            enable_pricing_models: Enable pricing models (default: from settings)
            auto_start_enhanced: Automatically start enhanced features like external data collection
        """
        self.queue = queue.Queue(4096)
        if settings.DRY_RUN:
            logger.info(
                "DRY RUN -- Orders printed below represent what would be posted to the markets"
            )
        else:
            logger.info("LIVE RUN -- Orders will be posted to the markets")

        self.markets = {}
        self.pricing = {}
        self.start_position_buy = {}
        self.start_position_sell = {}
        for symbol in settings.SYMBOLS:
            self.markets[symbol] = MarketInterface(symbol, settings.DRY_RUN, self.queue)
            self.markets[symbol].get_client()
            self.markets[symbol].get_instrument()
            self.markets[symbol].get_instrument_id()

        self.running = True
        self.start_time = datetime.now()
        self.auto_start_enhanced = auto_start_enhanced
        self.external_thread = None
        self.external_event_loop = None
        self.external_main_task = None  # Track the main external data task
        self.startup_grace_period = getattr(settings, "STARTUP_GRACE_PERIOD", 60)
        self.sanity_check_failures = 0
        self.max_sanity_failures = getattr(settings, "MAX_SANITY_FAILURES", 3)

        self.order_adjustment_tracker = None
        # Order adjustment system initialization (before enhanced features for logging)
        self._init_order_adjustment()

        # Initialize adjustment tracking
        self._adjustments_made_this_cycle = set()

        # Initialize price monitoring for reactive adjustments
        self._last_pricing_snapshot = {}  # symbol -> pricing_result
        self._last_price_check = time.time()

        # Enhanced features initialization
        self._init_enhanced_features(enable_external_data, enable_pricing_models)

        # register exit handler that will always cancel orders on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)
        logger.info(f"Order Manager initializing @ {self.start_time}")
        if settings.CANCEL_ORDERS_ON_START:
            for symbol in settings.SYMBOLS:
                self.markets[symbol].cancel_all_orders()

    def _init_enhanced_features(
        self, enable_external_data=None, enable_pricing_models=None
    ):
        """Initialize enhanced features (external data and pricing models)."""
        # Initialize enhanced features as None by default
        self.external_data_manager = None
        self.pricing_manager = None
        self._pricing_cache = {}
        self._last_cache_clear = 0

        # Determine if enhanced features should be enabled
        if not ENHANCED_FEATURES_AVAILABLE:
            logger.info("📊 Enhanced features not available (missing dependencies)")
            return

        use_external_data = enable_external_data
        if use_external_data is None:
            use_external_data = getattr(settings, "USE_EXTERNAL_DATA", False)

        use_pricing_models = enable_pricing_models
        if use_pricing_models is None:
            use_pricing_models = getattr(settings, "USE_PRICING_MODELS", False)

        if not use_external_data and not use_pricing_models:
            logger.info("📊 Enhanced features disabled by configuration")
            return

        # Initialize external data manager if enabled
        if use_external_data:
            self.external_data_manager = self._setup_external_data()

        # Initialize pricing models if enabled
        if use_pricing_models and self.external_data_manager:
            logger.info("📊 setting up pricing manager")
            self.pricing_manager = self._setup_pricing_models()

        if self.external_data_manager or self.pricing_manager:
            logger.info("📊 Enhanced features initialized successfully")

        # Auto-start external data collection if requested
        if self.auto_start_enhanced and self.external_data_manager:
            self._start_external_data_collection()

        # Log enhanced status if auto-start is enabled
        if self.auto_start_enhanced:
            self._log_enhanced_status()

    def _setup_external_data(self):
        """Setup external data providers."""
        try:
            external_data_manager = ExternalDataManager()

            # Add configured providers
            providers_config = getattr(settings, "EXTERNAL_DATA_PROVIDERS", {})

            for _provider_name, config in providers_config.items():
                if not config.get("enabled", False):
                    continue

                provider_type = config.get("type")

                if provider_type == "coinbase":
                    provider = CoinbaseProvider()
                    external_data_manager.add_provider(provider)
                    logger.info(f"Added Coinbase external data provider")
                elif provider_type == "coinbase_rest":
                    provider = CoinbaseRESTProvider()
                    external_data_manager.add_provider(provider)
                    logger.info(f"Added Coinbase REST external data provider")
                else:
                    logger.warning(f"Unknown provider type: {provider_type}")

            # Allow user customization
            custom_manager = self.setup_custom_external_data_provider(
                external_data_manager
            )
            if custom_manager:
                external_data_manager = custom_manager

            if external_data_manager.providers:
                logger.info(
                    f"📡 External data manager initialized with {len(external_data_manager.providers)} providers"
                )
                return external_data_manager
            else:
                logger.warning("📡 No external data providers configured")
                return None

        except Exception as e:
            logger.error(f"Failed to setup external data: {e}")
            return None

    def _setup_pricing_models(self):
        """Setup pricing models."""
        try:
            pricing_manager = PricingModelManager(self.external_data_manager)

            # Add configured models based on settings
            models_config = getattr(settings, "PRICING_MODELS", {})

            for model_name, config in models_config.items():
                if not config.get("enabled", False):
                    continue

                priority = config.get("priority", 100)

                if model_name == "simple_spread":
                    model = SimpleSpreadModel(
                        spread_bps=config.get("spread_bps", 50),
                        reference_provider=config.get("reference_provider", "coinbase"),
                    )
                elif model_name == "consensus":
                    model = ConsensusModel(
                        min_providers=config.get("min_providers", 2),
                        outlier_threshold=config.get("outlier_threshold", 0.02),
                        base_spread_bps=config.get("base_spread_bps", 30),
                    )
                elif model_name == "local_aware":
                    model = LocalMarketAwareModel(
                        external_weight=config.get("external_weight", 0.7),
                        local_weight=config.get("local_weight", 0.3),
                        base_spread_bps=config.get("base_spread_bps", 40),
                    )
                elif model_name == "momentum":
                    model = MomentumModel(
                        lookback_minutes=config.get("lookback_minutes", 15),
                        momentum_factor=config.get("momentum_factor", 0.1),
                        base_spread_bps=config.get("base_spread_bps", 35),
                    )
                else:
                    logger.warning(f"Unknown pricing model: {model_name}")
                    continue

                pricing_manager.add_model(model, priority)
                logger.info(f"Added {model_name} pricing model (priority: {priority})")

            # Allow user customization
            custom_manager = self.setup_custom_pricing_model(pricing_manager)
            if custom_manager:
                pricing_manager = custom_manager

            if pricing_manager.models:
                logger.info(
                    f"💰 Pricing manager initialized with {len(pricing_manager.models)} models"
                )
                return pricing_manager
            else:
                logger.warning("💰 No pricing models configured")
                return None

        except Exception as e:
            logger.error(f"Failed to setup pricing models: {e}")
            return None

    def _init_order_adjustment(self):
        """Initialize the order adjustment system."""
        try:
            self.order_adjustment_tracker = OrderAdjustmentTracker(settings)

            threshold = getattr(settings, "PRICE_MOVE_THRESHOLD", 0.002)
            cooldown = getattr(settings, "ORDER_ADJUSTMENT_COOLDOWN", 30)
            logger.info(f"🎯 Order adjustment system initialized")
            logger.info(
                f"   📊 Price move threshold: {threshold:.5f} ({threshold*100:.3f}%)"
            )
            logger.info(f"   ⏱️ Adjustment cooldown: {cooldown}s")
            logger.info(
                f"   🎛️ Max order age: {getattr(settings, 'MAX_ORDER_AGE_SECONDS', 300)}s"
            )

        except Exception as e:
            self.order_adjustment_tracker = None
            logger.warning(
                "⚠️ Order adjustment system not available - PRICE_MOVE_THRESHOLD will not be active"
            )

    def restart(self):
        pass

    def reset(self):
        for symbol in settings.SYMBOLS:
            self.markets[symbol].cancel_all_orders()
        self.check_sanity()
        self.print_status()

        # Create orders and converge.
        self.place_orders()

    def exit(self, signum=None, frame=None):
        if self is None or not self.running:
            return

        # Use the graceful shutdown method for all exits
        self._graceful_shutdown()

    def _graceful_shutdown(self):
        """Perform graceful shutdown with external data cleanup and retry."""
        logger.info("🛑 Beginning graceful shutdown sequence...")

        # Mark as shutting down first
        self.running = False

        try:
            # Step 1: Signal shutdown and disconnect providers in their own event loop
            if (
                hasattr(self, "external_thread")
                and self.external_thread
                and self.external_thread.is_alive()
            ):
                logger.info("📡 Initiating external data shutdown...")

                if hasattr(self, "external_event_loop") and self.external_event_loop:
                    try:
                        import asyncio

                        # Step 1: Cancel the main external data task first
                        if (
                            hasattr(self, "external_main_task")
                            and self.external_main_task
                        ):
                            logger.info("📡 Cancelling main external data task...")

                            def cancel_main_task():
                                if (
                                    self.external_main_task
                                    and not self.external_main_task.cancelled()
                                ):
                                    self.external_main_task.cancel()
                                    logger.info("📡 Main external data task cancelled")

                            self.external_event_loop.call_soon_threadsafe(
                                cancel_main_task
                            )

                            # Give it a moment to cancel
                            time.sleep(0.5)

                        # Step 2: Schedule the disconnect in the external event loop
                        if (
                            hasattr(self, "external_data_manager")
                            and self.external_data_manager
                        ):
                            future = asyncio.run_coroutine_threadsafe(
                                self.external_data_manager.disconnect_all(),
                                self.external_event_loop,
                            )
                            # Give it time to complete
                            try:
                                future.result(timeout=5)
                                logger.info("📡 External data providers disconnected")
                            except Exception as e:
                                logger.warning(
                                    f"Provider disconnect timeout/error: {e}"
                                )
                    except Exception as e:
                        logger.warning(f"Error during provider disconnect: {e}")

                        # Fallback: Mark providers as disconnected manually
                        if (
                            hasattr(self, "external_data_manager")
                            and self.external_data_manager
                        ):
                            try:
                                for (
                                    provider
                                ) in self.external_data_manager.providers.values():
                                    provider.connected = False
                                logger.info(
                                    "📡 Marked all providers as disconnected (fallback)"
                                )
                            except Exception as fallback_error:
                                logger.warning(
                                    f"Fallback disconnect failed: {fallback_error}"
                                )

                    try:
                        # Step 3: Now stop the event loop
                        self.external_event_loop.call_soon_threadsafe(
                            self.external_event_loop.stop
                        )
                    except Exception as e:
                        logger.warning(f"Error stopping external event loop: {e}")

                # Wait for thread to finish
                logger.info("📡 Waiting for external data worker thread to stop...")
                self.external_thread.join(
                    timeout=10
                )  # Longer timeout for graceful shutdown

                if self.external_thread.is_alive():
                    logger.warning(
                        "📡 External data thread did not stop gracefully within timeout"
                    )
                else:
                    logger.info("📡 External data worker thread stopped")

            # Step 3: Cancel all orders if configured
            if settings.CANCEL_ORDERS_ON_EXIT:
                logger.info("📝 Cancelling all open orders...")
                for symbol, market in self.markets.items():
                    try:
                        orders = market.get_orders()
                        if orders:
                            logger.info(f"Cancelling {len(orders)} orders for {symbol}")
                            market.cancel_all_orders()
                    except Exception as e:
                        logger.error(f"Error cancelling orders for {symbol}: {e}")

            # Step 4: Close market connections
            logger.info("🔌 Closing market connections...")
            for symbol, market in self.markets.items():
                try:
                    market.exit()
                except Exception as e:
                    logger.error(f"Error closing connection for {symbol}: {e}")

        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
        finally:
            logger.info("🔚 Graceful shutdown complete")
            sys.exit(0)

    def get_ticker(self, symbol):
        # Get local ticker data (always needed as fallback)
        local_ticker = self.markets[symbol].get_ticker()

        # Check if we should use external data first
        external_first = getattr(settings, "EXTERNAL_DATA_FIRST", False)

        # If external first mode and we're in startup period, wait a bit for external data
        if external_first and self.external_data_manager:
            time_since_startup = (datetime.now() - self.start_time).total_seconds()
            startup_wait = getattr(
                settings, "EXTERNAL_DATA_STARTUP_WAIT", 10
            )  # Wait up to 10s for external data

            if (
                time_since_startup < startup_wait
                and not self.external_data_manager.get_all_data(symbol)
            ):
                logger.info(
                    f"⏳ Waiting for external data for {symbol} (startup: {time_since_startup:.1f}s/{startup_wait}s)"
                )
                # Brief wait to allow external data to populate
                time.sleep(1)
                # Check again after the wait
                if not self.external_data_manager.get_all_data(symbol):
                    logger.info(
                        f"⚠️ Still no external data for {symbol} after wait, proceeding with available data"
                    )

        if external_first and self.external_data_manager:
            # Try external data first
            external_ticker = self._get_external_ticker(symbol, local_ticker)
            if external_ticker:
                ticker = external_ticker
                data_source = "external"

                # Log the adjustment for visibility
                local_mid = local_ticker.get("mid", 0)
                ext_mid = external_ticker.get("mid", 0)
                if local_mid > 0 and ext_mid > 0:
                    deviation = abs(ext_mid - local_mid) / local_mid * 100
                    logger.info(
                        f"🎯 Adjusting to external market: {symbol} local={local_mid:.2f} → external={ext_mid:.2f} (Δ{deviation:.6f}%)"
                    )
            else:
                ticker = local_ticker
                data_source = "local (external unavailable)"
                # Log why external data wasn't used
                logger.debug(
                    f"⚠️ External data not available for {symbol}, using local data"
                )
        else:
            # Use local ticker (traditional approach)
            ticker = local_ticker
            data_source = "local"

        # Set up our buy & sell positions based on the chosen ticker
        # Start with smallest possible unit above and below the current spread
        self.start_position_buy[symbol] = ticker["buy"] + GetQuoteTick(ticker["buy"])
        self.start_position_sell[symbol] = ticker["sell"] - GetQuoteTick(ticker["sell"])

        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if settings.MAINTAIN_SPREADS:
            if ticker["buy"] == self.markets[symbol].get_highest_buy()["price"]:
                self.start_position_buy[symbol] = ticker["buy"]
            if ticker["sell"] == self.markets[symbol].get_lowest_sell()["price"]:
                self.start_position_sell[symbol] = ticker["sell"]

        # Back off if our spread is too small.
        if (
            self.start_position_buy[symbol] * (1.00 + settings.MIN_SPREAD)
            > self.start_position_sell[symbol]
        ):
            self.start_position_buy[symbol] *= 1.00 - (settings.MIN_SPREAD / 2)
            self.start_position_buy[symbol] = math.toNearest(
                self.start_position_buy[symbol],
                GetQuoteTick(self.start_position_buy[symbol]),
            )
            self.start_position_sell[symbol] *= 1.00 + (settings.MIN_SPREAD / 2)

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]

        logger.info(
            "📊 Start Positions (%s): Buy: %f, Sell: %f, Mid: %f"
            % (
                data_source,
                self.start_position_buy[symbol],
                self.start_position_sell[symbol],
                self.start_position_mid,
            )
        )

        # Store the data source for status reporting
        if not hasattr(self, "_data_sources"):
            self._data_sources = {}
        self._data_sources[symbol] = data_source

        # Return the local ticker for compatibility (other code may expect local market data)
        return ticker

    def get_price_offset(self, symbol, index):
        """Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
        Negative is a buy, positive is a sell.

        Enhanced version: Uses pricing models when available, falls back to original logic.
        """
        if not symbol in self.pricing or self.pricing[symbol] == None:
            return self._get_price_offset(symbol, index)
        prices = self.pricing[symbol]

        if index < 0:
            return prices.buy_levels[index * -1].price
        return prices.sell_levels[index].price

    def _get_price_offset(self, symbol, index):
        """Get the price offset calculation (without enhanced pricing)."""
        if settings.MAINTAIN_SPREADS:
            start_position = (
                self.start_position_buy[symbol]
                if index < 0
                else self.start_position_sell[symbol]
            )
            index = index + 1 if index < 0 else index - 1
        else:
            start_position = (
                self.start_position_buy[symbol]
                if index < 0
                else self.start_position_sell[symbol]
            )

            if index > 0 and start_position < self.start_position_buy[symbol]:
                start_position = self.start_position_sell[symbol]
            if index < 0 and start_position > self.start_position_sell[symbol]:
                start_position = self.start_position_buy[symbol]

        price = start_position * (1 + settings.INTERVAL) ** index
        tickSize = GetQuoteTick(price)
        return math.toNearest(price, tickSize)

    def _is_pricing_valid(self, symbol: str, pricing_result, local_ticker) -> bool:
        """Check if pricing result is valid for use."""
        min_confidence = getattr(settings, "MIN_PRICING_CONFIDENCE", 0.5)

        if pricing_result.confidence < min_confidence:
            logger.debug(
                f"❌ {symbol} pricing confidence too low: {pricing_result.confidence:.2f} < {min_confidence}"
            )
            return False

        # Check for excessive deviation from local price
        max_deviation = getattr(settings, "MAX_EXTERNAL_DEVIATION", 0.10)
        bootstrap_mode = getattr(settings, "BOOTSTRAP_TO_EXTERNAL", False)
        local_mid = local_ticker.get("mid", pricing_result.fair_value)

        if local_mid > 0:
            deviation = abs(pricing_result.fair_value - local_mid) / local_mid

            if deviation > max_deviation:
                if bootstrap_mode:
                    logger.debug(
                        f"🚀 {symbol} BOOTSTRAP MODE: Using external despite {deviation:.2%} deviation"
                    )
                    return True
                else:
                    # Only log occasionally to avoid spam
                    current_time = time.time()
                    last_tip = getattr(self, f"_last_deviation_tip_{symbol}", 0)
                    if current_time - last_tip > 60:  # Once per minute
                        logger.info(
                            f"💡 {symbol}: Large price deviation {deviation:.2%}. Set BOOTSTRAP_TO_EXTERNAL = True to override."
                        )
                        setattr(self, f"_last_deviation_tip_{symbol}", current_time)
                    return False
            else:
                logger.debug(
                    f"✅ {symbol} external pricing accepted (deviation: {deviation:.2%})"
                )

        return True

    def _calculate_offset_from_pricing(self, pricing_result, index: int) -> float:
        """Calculate order price offset from pricing result."""
        if index < 0:  # Buy side
            base_price = pricing_result.bid_price
        else:  # Sell side
            base_price = pricing_result.ask_price

        # Apply interval adjustments like original logic
        # Adjust index for positioning (first positions start at base price)
        adjusted_index = index + 1 if index < 0 else index - 1

        # Apply interval spacing
        price = base_price * (1 + settings.INTERVAL) ** adjusted_index

        # Ensure proper tick size
        tick_size = GetQuoteTick(price)
        return math.toNearest(price, tick_size)

    ###
    # Extension Points for User Customization
    ###

    def setup_custom_external_data_provider(self, external_data_manager):
        """Extension point: Override to add custom external data providers.

        Args:
            external_data_manager: The ExternalDataManager instance

        Returns:
            Modified external_data_manager or None to use default setup
        """
        return None

    def setup_custom_pricing_model(self, pricing_manager):
        """Extension point: Override to add custom pricing models.

        Args:
            pricing_manager: The PricingModelManager instance

        Returns:
            Modified pricing_manager or None to use default setup
        """
        return None

    def validate_pricing_result(
        self, symbol: str, pricing_result, local_ticker
    ) -> bool:
        """Extension point: Override to add custom pricing validation.

        Args:
            symbol: Trading symbol
            pricing_result: PricingResult from pricing model
            local_ticker: Local market ticker data

        Returns:
            True if pricing should be used, False otherwise
        """
        return self._is_pricing_valid(symbol, pricing_result, local_ticker)

    def calculate_order_price(
        self, symbol: str, index: int, pricing_result=None
    ) -> float:
        """Extension point: Override to customize order price calculation.

        Args:
            symbol: Trading symbol
            index: Order index (negative for buy, positive for sell)
            pricing_result: Optional pricing model result

        Returns:
            Calculated price for the order
        """
        if pricing_result and self.validate_pricing_result(
            symbol, pricing_result, self.markets[symbol].get_ticker()
        ):
            return self._calculate_offset_from_pricing(pricing_result, index)
        else:
            return self._get_original_price_offset(symbol, index)

    def should_place_order(self, symbol: str, order_data: dict) -> bool:
        """Extension point: Override to add custom order placement logic.

        Args:
            symbol: Trading symbol
            order_data: Dictionary containing order details

        Returns:
            True if order should be placed, False otherwise
        """
        # Default: always place orders (original behavior)
        return True

    def on_order_placed(self, symbol: str, order_result, order_data: dict):
        """Extension point: Called after an order is placed.

        Args:
            symbol: Trading symbol
            order_result: Result from order placement API
            order_data: Dictionary containing order details
        """
        pass

    def on_order_amended(self, symbol: str, amend_result, amend_data: dict):
        """Extension point: Called after an order is amended.

        Args:
            symbol: Trading symbol
            amend_result: Result from order amendment API
            amend_data: Dictionary containing amendment details
        """
        pass

    def on_order_cancelled(self, symbol: str, cancel_result, order_id: str):
        """Extension point: Called after an order is cancelled.

        Args:
            symbol: Trading symbol
            cancel_result: Result from order cancellation API
            order_id: ID of cancelled order
        """
        pass

    ###
    # Enhanced Market Maker Features
    ###

    def _start_external_data_collection(self):
        """Start external data collection in background thread."""
        logger.info("📡 Starting external data collection...")

        # Start external data collection in background thread
        self.external_thread = threading.Thread(
            target=self._external_data_worker, daemon=True
        )
        self.external_thread.start()
        logger.info("📡 External data collection started")

    def _external_data_worker(self):
        """Worker thread for external data collection."""
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.external_event_loop = loop

            # Start all providers
            async def start_data_collection():
                try:
                    # Connect all providers first
                    logger.info("Connecting to external data providers...")
                    connect_results = await self.external_data_manager.connect_all()

                    connected_providers = [
                        name for name, result in connect_results.items() if result
                    ]
                    if connected_providers:
                        logger.info(f"Connected providers: {connected_providers}")
                    else:
                        logger.warning("No providers connected successfully")
                        return

                    # Subscribe to all symbols on all providers
                    for symbol in settings.SYMBOLS:
                        if not self.running:  # Check for shutdown signal
                            logger.info("Shutdown requested during subscription setup")
                            return

                        logger.info(f"Subscribing to {symbol} on all providers...")
                        subscribe_results = (
                            await self.external_data_manager.subscribe_all(symbol)
                        )

                        successful_subs = [
                            name for name, result in subscribe_results.items() if result
                        ]
                        if successful_subs:
                            logger.info(
                                f"Successfully subscribed to {symbol} on: {successful_subs}"
                            )
                        else:
                            logger.warning(
                                f"Failed to subscribe to {symbol} on any provider"
                            )

                    # Keep the loop alive while running
                    while self.running:
                        await asyncio.sleep(1)  # Check running status every second

                except asyncio.CancelledError:
                    logger.info("📡 External data collection cancelled")
                    return
                except Exception as e:
                    logger.error(f"Error in external data collection setup: {e}")
                    return

            # Start the data collection and track the task
            self.external_main_task = loop.create_task(start_data_collection())

            # Run the event loop
            loop.run_forever()

        except Exception as e:
            logger.error(f"Error in external data worker: {e}")
            import traceback

            traceback.print_exc()
        finally:
            if hasattr(self, "external_event_loop") and self.external_event_loop:
                try:
                    # Cancel any remaining tasks
                    if hasattr(self, "external_main_task") and self.external_main_task:
                        if not self.external_main_task.cancelled():
                            self.external_main_task.cancel()
                            try:
                                self.external_event_loop.run_until_complete(
                                    self.external_main_task
                                )
                            except asyncio.CancelledError:
                                logger.debug(
                                    "Main external task cancelled during cleanup"
                                )

                    # Close the loop
                    self.external_event_loop.close()
                    logger.debug("External event loop closed")
                except Exception as e:
                    logger.warning(f"Error during external event loop cleanup: {e}")

    def _log_enhanced_status(self):
        """Log the enhanced configuration status."""
        logger.info("🚀 === Enhanced Market Maker Configuration ===")

        # Data source priority
        external_first = getattr(settings, "EXTERNAL_DATA_FIRST", False)
        if external_first:
            logger.info("🌐 Data Priority: ✅ External First (with local fallback)")
            logger.info(
                f"   📊 Max External Age: {getattr(settings, 'MAX_EXTERNAL_DATA_AGE', 30)}s"
            )
            logger.info(
                f"   📊 Min Providers: {getattr(settings, 'EXTERNAL_DATA_MIN_PROVIDERS', 1)}"
            )
            logger.info(
                f"   📊 Max Spread: {getattr(settings, 'EXTERNAL_DATA_MAX_SPREAD_PCT', 2.0)}%"
            )
        else:
            logger.info("🏠 Data Priority: Local First (with external enhancement)")

        # External data status
        if self.external_data_manager:
            provider_count = len(self.external_data_manager.providers)
            logger.info(f"📡 External Data: ✅ Enabled ({provider_count} providers)")
            for name, provider in self.external_data_manager.providers.items():
                logger.info(f"   📊 {name}: {type(provider).__name__}")
        else:
            logger.info("📡 External Data: ❌ Disabled")

        # Pricing models status
        if self.pricing_manager:
            model_count = len(self.pricing_manager.models)
            logger.info(f"💰 Pricing Models: ✅ Enabled ({model_count} models)")
            for name, model in self.pricing_manager.models.items():
                enabled_status = "✅" if model.enabled else "❌"
                logger.info(f"   🎯 {name}: {enabled_status}")
        else:
            logger.info("💰 Pricing Models: ❌ Disabled")

        # Bootstrap settings
        bootstrap_enabled = getattr(settings, "BOOTSTRAP_TO_EXTERNAL", False)
        logger.info(
            f"🚀 Bootstrap Mode: {'✅ Enabled' if bootstrap_enabled else '❌ Disabled'}"
        )

        # Order adjustment system status
        if self.order_adjustment_tracker:
            threshold = getattr(settings, "PRICE_MOVE_THRESHOLD", 0.002)
            cooldown = getattr(settings, "ORDER_ADJUSTMENT_COOLDOWN", 30)
            logger.info(f"🎯 Order Adjustments: ✅ Enabled")
            logger.info(
                f"   📊 Price threshold: {threshold:.5f} ({threshold*100:.3f}%)"
            )
            logger.info(f"   ⏱️ Cooldown: {cooldown}s")
        else:
            logger.info(f"🎯 Order Adjustments: ❌ Disabled")

        logger.info("🚀 === Configuration Complete ===")

    def print_enhanced_status(self, symbols=None):
        """Print enhanced status including external data and pricing info."""
        if symbols is None:
            symbols = settings.SYMBOLS

        for symbol in symbols:
            logger.info(f"📊 === Enhanced Status for {symbol} ===")

            # Show current data source being used for orders
            data_source = getattr(self, "_data_sources", {}).get(symbol, "unknown")
            logger.info(f"🎯 Current Order Data Source: {data_source}")

            # Print local market data
            try:
                ticker = self.markets[symbol].get_ticker()
                orders = self.markets[symbol].get_orders()
                logger.info(
                    f"Local Market: {ticker['buy']:.4f} / {ticker['sell']:.4f} (mid: {ticker['mid']:.4f})"
                )
                logger.info(f"Active Orders: {len(orders)}")
            except Exception as e:
                logger.error(f"Error getting local ticker for {symbol}: {e}")

            # Print external data status
            if self.external_data_manager:
                try:
                    external_data = self.external_data_manager.get_all_data(symbol)
                    if external_data:
                        for provider, data in external_data.items():
                            age = time.time() - data.timestamp
                            logger.info(
                                f"External ({provider}): {data.bid:.4f} / {data.ask:.4f} (mid: {data.mid:.4f}) [age: {age:.1f}s]"
                            )

                        best_external = self.external_data_manager.get_best_bid_ask(
                            symbol
                        )
                        if best_external:
                            logger.info(
                                f"Best External: {best_external['bid']:.4f} / {best_external['ask']:.4f}"
                            )
                    else:
                        # Check if providers are connected and subscribed
                        provider_status = []
                        for (
                            name,
                            provider,
                        ) in self.external_data_manager.providers.items():
                            connected = provider.connected
                            provider_status.append(
                                f"{name}: {'✅' if connected else '❌'}"
                            )

                        if provider_status:
                            logger.info(
                                f"📡 Provider status: {', '.join(provider_status)}"
                            )
                            if any(
                                p.connected
                                for p in self.external_data_manager.providers.values()
                            ):
                                logger.info(
                                    f"⏳ Waiting for external data for {symbol} (providers connected but no data yet)"
                                )
                            else:
                                logger.warning(
                                    f"❌ No providers connected for {symbol}"
                                )
                        else:
                            logger.warning(
                                f"❌ No external data providers configured for {symbol}"
                            )

                except Exception as e:
                    logger.error(f"Error getting external data for {symbol}: {e}")
            else:
                logger.warning("❌ External data manager not available")

            # Print pricing model results
            if self.pricing_manager:
                try:
                    ticker = self.markets[symbol].get_ticker()
                    best_price = self.pricing_manager.get_best_price(symbol, ticker)

                    if best_price:
                        logger.info(f"🎯 ACTIVE PRICING MODEL: {best_price.model_name}")
                        logger.info(f"   Fair Value: ${best_price.fair_value:.4f}")
                        logger.info(
                            f"   Bid/Ask: ${best_price.bid_price:.4f} / ${best_price.ask_price:.4f}"
                        )
                        logger.info(f"   Confidence: {best_price.confidence:.2f}")
                        logger.info(f"   Spread: {best_price.spread_bps:.1f}bps")

                        # Check if pricing is being used
                        is_valid = self.validate_pricing_result(
                            symbol, best_price, ticker
                        )
                        if is_valid:
                            logger.info("   ✅ Pricing is being used for orders")
                        else:
                            logger.warning("   ❌ Pricing not used - validation failed")
                    else:
                        logger.warning(f"❌ No pricing model results for {symbol}")

                except Exception as e:
                    logger.error(f"Error getting pricing models for {symbol}: {e}")
            else:
                logger.warning("❌ Pricing manager not available")

            # Print order adjustment status
            if self.order_adjustment_tracker:
                try:
                    # Check if we're in cooldown
                    in_cooldown = self.order_adjustment_tracker._is_in_cooldown(symbol)
                    last_adjustment = self.order_adjustment_tracker.last_adjustment.get(
                        symbol, 0
                    )

                    if last_adjustment > 0:
                        time_since_last = time.time() - last_adjustment
                        cooldown_status = "⏳ Cooldown" if in_cooldown else "✅ Ready"
                        logger.info(
                            f"🎯 Order Adjustments: {cooldown_status} (last: {time_since_last:.0f}s ago)"
                        )
                    else:
                        logger.info(
                            "🎯 Order Adjustments: ✅ Ready (no previous adjustments)"
                        )

                except Exception as e:
                    logger.debug(f"Error getting order adjustment status: {e}")

    def _attempt_external_bootstrap(self, symbol: str, local_ticker: dict) -> bool:
        """Attempt to bootstrap pricing from external market data when local market is inconsistent.

        Returns True if bootstrap was attempted, False otherwise.
        """
        if not self.external_data_manager:
            return False

        # Check if bootstrap mode is enabled
        bootstrap_enabled = getattr(settings, "BOOTSTRAP_TO_EXTERNAL", False)
        if not bootstrap_enabled:
            logger.debug(
                f"Bootstrap not attempted for {symbol}: BOOTSTRAP_TO_EXTERNAL is disabled"
            )
            return False

        try:
            # Get external market data
            external_data = self.external_data_manager.get_all_data(symbol)
            if not external_data:
                logger.debug(
                    f"Bootstrap not attempted for {symbol}: no external data available"
                )
                return False

            # Get best external bid/ask
            best_external = self.external_data_manager.get_best_bid_ask(symbol)
            if not best_external:
                logger.debug(
                    f"Bootstrap not attempted for {symbol}: no best external prices available"
                )
                return False

            ext_bid = best_external["bid"]
            ext_ask = best_external["ask"]
            ext_mid = best_external["mid"]

            if ext_bid <= 0 or ext_ask <= 0:
                logger.debug(
                    f"Bootstrap not attempted for {symbol}: invalid external prices (bid={ext_bid}, ask={ext_ask})"
                )
                return False

            # Calculate adjustment based on external data
            local_mid = local_ticker.get("mid", 0)
            if local_mid > 0:
                price_deviation = abs(ext_mid - local_mid) / local_mid
                logger.info(
                    f"🚀 BOOTSTRAP: {symbol} external mid={ext_mid:.2f} vs local mid={local_mid:.2f} "
                    f"(deviation: {price_deviation:.2%})"
                )

            # Adjust start positions to external market with some spread buffer
            spread_buffer = getattr(
                settings, "BOOTSTRAP_SPREAD_BUFFER", 0.001
            )  # 0.1% buffer

            # Set new start positions based on external market
            self.start_position_buy[symbol] = ext_bid * (1 - spread_buffer)
            self.start_position_sell[symbol] = ext_ask * (1 + spread_buffer)
            self.start_position_mid = ext_mid

            # Ensure minimum spread is maintained
            if (
                self.start_position_buy[symbol] * (1.00 + settings.MIN_SPREAD)
                > self.start_position_sell[symbol]
            ):

                # Widen the spread to meet minimum requirements
                mid_point = (
                    self.start_position_buy[symbol] + self.start_position_sell[symbol]
                ) / 2
                half_min_spread = settings.MIN_SPREAD / 2

                self.start_position_buy[symbol] = mid_point * (1 - half_min_spread)
                self.start_position_sell[symbol] = mid_point * (1 + half_min_spread)

            logger.info(
                f"🚀 BOOTSTRAP: Adjusted {symbol} positions to external market: "
                f"buy={self.start_position_buy[symbol]:.2f}, sell={self.start_position_sell[symbol]:.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Error during external bootstrap for {symbol}: {e}")
            return False

    def _get_external_ticker(self, symbol: str, local_ticker: dict) -> dict:
        """Get external market data formatted as a ticker.

        Args:
            symbol: Trading symbol
            local_ticker: Local ticker data for fallback values

        Returns:
            External ticker dict or None if external data not suitable
        """
        try:
            if not self.external_data_manager:
                return None

            # Get fresh external data
            external_data = self.external_data_manager.get_all_data(symbol)
            if not external_data:
                logger.info(
                    f"🚫 No external data available for {symbol} yet (providers may still be connecting)"
                )
                return None
            else:
                logger.debug(
                    f"📊 Found external data for {symbol} from {len(external_data)} providers: {list(external_data.keys())}"
                )

            # Filter for fresh data
            max_age = getattr(settings, "MAX_EXTERNAL_DATA_AGE", 30)
            min_providers = getattr(settings, "EXTERNAL_DATA_MIN_PROVIDERS", 1)
            max_spread_pct = getattr(settings, "EXTERNAL_DATA_MAX_SPREAD_PCT", 2.0)

            fresh_data = []
            for provider_name, data in external_data.items():
                try:
                    provider = self.external_data_manager.providers[provider_name]
                    if provider.is_data_fresh(symbol, max_age):
                        fresh_data.append(data)
                except Exception as e:
                    logger.debug(f"Error checking freshness for {provider_name}: {e}")
                    continue

            if len(fresh_data) < min_providers:
                logger.info(
                    f"🕒 Insufficient fresh external data for {symbol}: {len(fresh_data)} < {min_providers} required"
                )

                # Show status of each provider for debugging
                for provider_name, data in external_data.items():
                    try:
                        provider = self.external_data_manager.providers[provider_name]
                        is_fresh = provider.is_data_fresh(symbol, max_age)
                        age = (
                            time.time() - data.timestamp
                            if hasattr(data, "timestamp")
                            else 0
                        )
                        logger.info(
                            f"   📊 {provider_name}: {'✅ Fresh' if is_fresh else '❌ Stale'} (age: {age:.1f}s)"
                        )
                    except Exception as e:
                        logger.debug(f"   📊 {provider_name}: Error checking - {e}")

                return None

            # Get best bid/ask from external sources
            best_external = self.external_data_manager.get_best_bid_ask(symbol)
            if not best_external:
                logger.debug(f"No best external bid/ask for {symbol}")
                return None

            ext_bid = best_external["bid"]
            ext_ask = best_external["ask"]
            ext_mid = best_external["mid"]
            ext_spread = best_external["spread"]

            # Validate external data quality
            if ext_bid <= 0 or ext_ask <= 0 or ext_bid >= ext_ask:
                logger.warning(
                    f"Invalid external prices for {symbol}: bid={ext_bid}, ask={ext_ask}"
                )
                return None

            # Check if spread is reasonable
            spread_pct = (ext_spread / ext_mid) * 100 if ext_mid > 0 else 999
            if spread_pct > max_spread_pct:
                logger.warning(
                    f"❌ External spread too wide for {symbol}: {spread_pct:.2f}% > {max_spread_pct}% limit"
                )
                logger.info(
                    f"   💡 Consider increasing EXTERNAL_DATA_MAX_SPREAD_PCT if this spread is acceptable"
                )
                return None
            else:
                logger.debug(
                    f"✅ External spread acceptable for {symbol}: {spread_pct:.2f}% ≤ {max_spread_pct}%"
                )

            # Create external ticker compatible with existing code
            external_ticker = {
                "buy": ext_bid,
                "sell": ext_ask,
                "mid": ext_mid,
                "last": ext_mid,  # Use mid as last price approximation
                "volume": local_ticker.get("volume", 0),  # Use local volume as fallback
                "high": max(
                    ext_ask, local_ticker.get("high", ext_ask)
                ),  # Conservative high
                "low": min(
                    ext_bid, local_ticker.get("low", ext_bid)
                ),  # Conservative low
            }

            # Log the external data usage
            deviation = 0
            if local_ticker.get("mid", 0) > 0:
                deviation = (
                    abs(ext_mid - local_ticker["mid"]) / local_ticker["mid"] * 100
                )

            logger.info(
                f"🌐 Using external data for {symbol}: "
                f"{ext_bid:.4f}/{ext_ask:.4f} (mid: {ext_mid:.4f}, "
                f"spread: {spread_pct:.2f}%, deviation: {deviation:.2f}%)"
            )

            return external_ticker

        except Exception as e:
            logger.error(f"Error getting external ticker for {symbol}: {e}")
            return None

    def _get_order_pricing_results_for_symbol(self, symbol: str):
        """Get order pricing results for all levels for the given symbol."""
        if self.pricing_manager:
            local_ticker = self.markets[symbol].get_ticker()
            return self.pricing_manager.get_symbol_pricing(symbol, local_ticker)

        return None

    def _get_pricing_result_for_symbol(self, symbol: str):
        """Get pricing result once per symbol to avoid repeated calls."""
        # Try to get pricing from pricing models first
        if self.pricing_manager:
            local_ticker = self.markets[symbol].get_ticker()
            pricing_result = self.pricing_manager.get_best_price(symbol, local_ticker)
            if pricing_result:
                return pricing_result

        # If no pricing model result, create one from current market data
        local_ticker = self.markets[symbol].get_ticker()
        # Create a basic pricing result from current start positions
        from market_maker.pricing.base import PricingResult

        pricing_result = PricingResult(
            symbol=symbol,
            fair_value=self.start_position_mid,
            bid_price=self.start_position_buy[symbol],
            ask_price=self.start_position_sell[symbol],
            confidence=0.8,  # Medium confidence for local pricing
            spread_bps=(
                (self.start_position_sell[symbol] - self.start_position_buy[symbol])
                / self.start_position_mid
            )
            * 10000,
            timestamp=time.time(),
            model_name="local_market",
        )
        return pricing_result

    def _process_order_amendment(
        self,
        symbol: str,
        order: dict,
        target_price: float,
        target_qty: str,
        adjustment_needed: bool,
        to_amend: list,
    ):
        """Helper method to process potential order amendments with stability checks."""
        # Add hysteresis to prevent oscillation - only amend if change is significant
        current_price = float(order["order_info"]["price"])
        price_change_pct = abs((float(target_price) - current_price) / current_price)

        # Use larger threshold for amendments to prevent oscillation
        min_change_threshold = max(settings.RELIST_INTERVAL, 0.005)  # At least 0.5%

        price_change_needed = (
            target_price != current_price and price_change_pct > min_change_threshold
        )

        qty_change_needed = target_qty != order["leaves_qty"]

        if qty_change_needed or price_change_needed:
            amendment_reason = []
            if adjustment_needed and price_change_needed:
                amendment_reason.append("pricing adjustment")
                # Track that we made a pricing adjustment (don't activate cooldown yet)
                self._adjustments_made_this_cycle.add(symbol)
            if qty_change_needed:
                amendment_reason.append("qty change")
            if price_change_needed and not adjustment_needed:
                amendment_reason.append("price convergence")

            logger.debug(
                f"📝 Amending order {order['id']}: {', '.join(amendment_reason)} (Δ{price_change_pct:.3f}%)"
            )

            to_amend.append(
                {
                    "ref_order_id": order["id"],
                    "new_qty": str(float(order["executed_qty"]) + float(target_qty)),
                    "new_price": str(target_price),
                    "side": order["order_info"]["side"],
                }
            )

    def _check_for_reactive_adjustments(self) -> bool:
        """Check if significant price movements require immediate order adjustments.

        Returns True if any adjustments were triggered.
        """
        if not self.order_adjustment_tracker:
            return False

        current_time = time.time()

        # Don't check too frequently to avoid overload
        if (
            current_time - self._last_price_check
            < self.order_adjustment_tracker.adjustment_interval
        ):
            return False

        self._last_price_check = current_time
        adjustments_triggered = False

        for symbol in settings.SYMBOLS:
            # Skip if in cooldown for this symbol
            if self.order_adjustment_tracker._is_in_cooldown(symbol):
                continue

            # Get current pricing
            current_pricing = self._get_pricing_result_for_symbol(symbol)
            if not current_pricing:
                continue

            # Compare with last pricing snapshot
            last_pricing = self._last_pricing_snapshot.get(symbol)
            if not last_pricing:
                self._last_pricing_snapshot[symbol] = current_pricing
                continue

            # Check if there's been significant price movement
            price_move_detected = self._detect_significant_price_movement(
                symbol, last_pricing, current_pricing
            )

            if price_move_detected:
                logger.info(
                    f"Significant price movement detected for {symbol} - triggering reactive adjustments"
                )

                # Trigger order adjustments by calling place_orders for this symbol
                self.place_orders([symbol])
                adjustments_triggered = True
                if self.order_adjustment_tracker:
                    self.order_adjustment_tracker.record_adjustment(symbol)

            # Update the pricing snapshot
            self._last_pricing_snapshot[symbol] = current_pricing

        return adjustments_triggered

    def _detect_significant_price_movement(
        self, symbol: str, old_pricing, new_pricing
    ) -> bool:
        """Detect if price movement exceeds threshold."""
        threshold = self.order_adjustment_tracker.price_move_threshold

        # Check bid price movement
        if old_pricing.bid_price > 0 and new_pricing.bid_price > 0:
            bid_change_pct = (
                abs(new_pricing.bid_price - old_pricing.bid_price)
                / old_pricing.bid_price
            )
            if bid_change_pct > threshold:
                logger.info(
                    f"📊 {symbol} bid moved {bid_change_pct:.6f}% (threshold: {threshold:.6f}%)"
                )
                return True

        # Check ask price movement
        if old_pricing.ask_price > 0 and new_pricing.ask_price > 0:
            ask_change_pct = (
                abs(new_pricing.ask_price - old_pricing.ask_price)
                / old_pricing.ask_price
            )
            if ask_change_pct > threshold:
                logger.info(
                    f"📊 {symbol} ask moved {ask_change_pct:.6f}% (threshold: {threshold:.6f}%)"
                )
                return True

        # Check fair value movement
        if old_pricing.fair_value > 0 and new_pricing.fair_value > 0:
            fair_change_pct = (
                abs(new_pricing.fair_value - old_pricing.fair_value)
                / old_pricing.fair_value
            )
            if fair_change_pct > threshold:
                logger.info(
                    f"📊 {symbol} fair value moved {fair_change_pct:.6f}% (threshold: {threshold:.6f}%)"
                )
                return True

        return False

    ###
    # Orders
    ###

    def update_pricing(self, symbols=settings.SYMBOLS):
        for symbol in symbols:
            logger.info(f"Updating pricing for symbol: {symbol}")
            self.pricing[symbol] = self._get_order_pricing_results_for_symbol(symbol)

    def prepare_order(self, symbol, index):
        """Create an order object."""

        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
            # Respect lot size
            quantity = (
                round(quantity / settings.ORDER_STEP_SIZE) * settings.ORDER_STEP_SIZE
            )
        else:
            quantity = settings.ORDER_START_SIZE + (
                (abs(index) - 1) * settings.ORDER_STEP_SIZE
            )

        # round to nearest QUOTE_SIZE
        quantity = math.toNearest(quantity, settings.QUOTE_SIZE)

        price = self.get_price_offset(symbol, index)

        return {
            "client_id": self.markets[symbol].get_client(),
            "symbol": symbol,
            "price": str(price),
            "qty": str(quantity),
            "side": "BUY" if index < 0 else "SELL",
        }

    def place_orders(self, symbols=settings.SYMBOLS):
        """Create order items for use in convergence."""

        for symbol in symbols:
            buy_orders = []
            sell_orders = []

            # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
            # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
            # a new order is created in the inside. If we did it inside-out, all orders would be amended
            # down and a new order would be created at the outside.
            for i in reversed(range(1, settings.ORDER_PAIRS + 1)):
                if not self.long_position_limit_exceeded(symbol):
                    buy_orders.append(self.prepare_order(symbol, -i))
                if not self.short_position_limit_exceeded(symbol):
                    sell_orders.append(self.prepare_order(symbol, i))
            self.converge_orders(symbol, buy_orders, sell_orders)

            # Don't run order adjustments immediately after converge_orders to avoid conflicts
            # Order adjustments will run on subsequent loops when prices actually move
            # This prevents the adjustment system from fighting with converge_orders

    def relist_order(self, desired_order, current_order) -> bool:
        price_ratio = float(desired_order["price"]) / float(
            current_order["order_info"]["price"]
        )
        return (desired_order["price"] != current_order["order_info"]["price"]) and (
            abs(price_ratio - 1) > settings.RELIST_INTERVAL
        )

    def converge_orders(self, symbol, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
        This involves amending any open orders and creating new ones if any have filled completely.
        We start from the closest orders outward."""

        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.markets[symbol].get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        # Also check if adjustments are needed based on pricing models.
        for i, order in enumerate(existing_orders):
            try:
                if order["order_info"]["side"] == "BUY":
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Found an existing order. Do we need to amend it?
                # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                if desired_order["qty"] != order["leaves_qty"] or self.relist_order(
                    desired_order, order
                ):
                    to_amend.append(
                        {
                            "ref_order_id": order["id"],
                            "new_qty": str(
                                float(order["executed_qty"])
                                + float(desired_order["qty"])
                            ),
                            "new_price": desired_order["price"],
                            "side": order["order_info"]["side"],
                        }
                    )

            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                to_cancel.append(order)
            except Exception as e:
                logger.error(
                    f"Error processing order {order.get('id', 'unknown')}: {e}"
                )
                to_cancel.append(order)

        while buys_matched < len(buy_orders):
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1

        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [
                    order
                    for order in existing_orders
                    if order["id"] == amended_order["ref_order_id"]
                ][0]
                logger.info(
                    "Amending %4s: %s @ %s to %f @ %s (%+f)"
                    % (
                        amended_order["side"],
                        reference_order["leaves_qty"],
                        reference_order["order_info"]["price"],
                        float(amended_order["new_qty"])
                        - float(reference_order["executed_qty"]),
                        amended_order["new_price"],
                        (
                            float(amended_order["new_price"])
                            - float(reference_order["order_info"]["price"])
                        ),
                    )
                )
                amended_order["client_id"] = self.markets[symbol].get_client()

            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's 
            # status (Filled/Canceled) made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                amend_result = self.markets[symbol].amend_orders(to_amend)
                # Call extension point for each amended order
                for amended_order in to_amend:
                    self.on_order_amended(symbol, amend_result, amended_order)
                if self.order_adjustment_tracker:
                    self.order_adjustment_tracker.record_adjustment(symbol)
            except Exception as e:
                logger.error("Amend failed: %s" % e)

        if len(to_create) > 0:
            for order in reversed(to_create):
                # Check if order should be placed (extension point)
                if not self.should_place_order(symbol, order):
                    logger.info(
                        f"Skipping order creation for {symbol} due to custom logic"
                    )
                    continue

                logger.info(
                    "Creating %4s %10s %s @ %s"
                    % (order["side"], order["symbol"], order["qty"], order["price"])
                )

            # Create orders and call extension point
            create_results = self.markets[symbol].create_orders(to_create)
            for order, result in zip(to_create, create_results or []):
                self.on_order_placed(symbol, result, order)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            # Cancel orders and call extension point
            for order in to_cancel:
                cancel_result = self.markets[symbol].truex.CancelOrder(order["id"])
                self.on_order_cancelled(symbol, cancel_result, order["id"])

        # Activate adjustment cooldown if we made pricing adjustments this cycle
        if (
            symbol in self._adjustments_made_this_cycle
            and self.order_adjustment_tracker
        ):
            self.order_adjustment_tracker.record_adjustment(symbol)
            logger.debug(
                f"🎯 Activated adjustment cooldown for {symbol} after batch processing"
            )
            # Clean up the tracking for this symbol
            self._adjustments_made_this_cycle.discard(symbol)

    ###
    # Position Limits
    ###

    def short_position_limit_exceeded(self, symbol):
        """Returns True if the short position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.markets[symbol].get_delta()
        return position <= settings.MIN_POSITION

    def long_position_limit_exceeded(self, symbol):
        """Returns True if the long position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.markets[symbol].get_delta()
        return position >= settings.MAX_POSITION

    ###
    # Sanity / validation
    ###
    def check_file_change(self):
        for f, mtime in watched_files_mtimes:
            if os.path.getmtime(f) != mtime:
                logger.info("Detected change in %s, reloading." % f)
                self.restart()

    def check_sanity(self):
        """Performs some checks on market status, orders, etc."""
        for symbol in settings.SYMBOLS:
            market = self.markets[symbol]
            # check if the market is open or not
            market.check_market()
            # check if the order is sane
            market.check_orderbook()
            # Get ticker, which sets price offsets and prints some debugging info.
            ticker = self.get_ticker(symbol)

            # Enhanced sanity check with external market bootstrapping
            sanity_failed = (
                self.get_price_offset(symbol, -1) >= ticker["sell"]
                or self.get_price_offset(symbol, 1) <= ticker["buy"]
            )

            if sanity_failed:
                # Calculate time since startup
                time_since_startup = (datetime.now() - self.start_time).total_seconds()
                is_startup_period = time_since_startup < self.startup_grace_period

                logger.error(
                    "Buy: %s, Sell: %s"
                    % (
                        self.start_position_buy[symbol],
                        self.start_position_sell[symbol],
                    )
                )
                logger.error(
                    "First buy position: %s local Best Ask: %s First sell position: %s local Best Bid: %s"
                    % (
                        self.get_price_offset(symbol, -1),
                        ticker["sell"],
                        self.get_price_offset(symbol, 1),
                        ticker["buy"],
                    )
                )

                # Try to bootstrap from external data if available (mainly for non-external-first mode)
                bootstrap_attempted = False
                external_first = getattr(settings, "EXTERNAL_DATA_FIRST", False)

                if not external_first:
                    # Only try bootstrap if we're not already using external data first
                    bootstrap_attempted = self._attempt_external_bootstrap(
                        symbol, ticker
                    )

                if is_startup_period and (bootstrap_attempted or external_first):
                    if external_first:
                        logger.warning(
                            f"⚠️ Market data inconsistent for {symbol} despite using external data first. "
                            f"This may indicate external data quality issues. "
                            f"Will retry (startup grace period: {self.startup_grace_period - time_since_startup:.1f}s remaining)"
                        )
                    else:
                        logger.warning(
                            f"⚠️ Market data inconsistent for {symbol} - attempted bootstrap from external data. "
                            f"Will retry (startup grace period: {self.startup_grace_period - time_since_startup:.1f}s remaining)"
                        )
                    continue  # Skip to next symbol, don't increment failure count
                elif is_startup_period:
                    self.sanity_check_failures += 1
                    logger.warning(
                        f"⚠️ Market data inconsistent for {symbol} (failure {self.sanity_check_failures}/{self.max_sanity_failures}). "
                        f"Startup grace period: {self.startup_grace_period - time_since_startup:.1f}s remaining"
                    )
                    if self.sanity_check_failures < self.max_sanity_failures:
                        continue  # Don't exit yet
                else:
                    self.sanity_check_failures += 1
                    logger.error(
                        f"❌ Sanity check failed for {symbol} (failure {self.sanity_check_failures}/{self.max_sanity_failures}) - market data is inconsistent"
                    )

                # Exit if we've exceeded max failures
                if self.sanity_check_failures >= self.max_sanity_failures:
                    logger.error(
                        f"❌ Maximum sanity check failures exceeded ({self.max_sanity_failures}). "
                        "Consider enabling BOOTSTRAP_TO_EXTERNAL or checking market data sources."
                    )
                    logger.info("🛑 Initiating graceful shutdown...")
                    self._graceful_shutdown()
            else:
                # Reset failure count on successful sanity check
                if self.sanity_check_failures > 0:
                    logger.info(
                        f"✅ Sanity check recovered for {symbol} - resetting failure count"
                    )
                    self.sanity_check_failures = 0

            # Messaging if the position limits are reached
            if self.long_position_limit_exceeded(symbol):
                logger.info("Long delta limit exceeded")
                logger.info(
                    "Current Position: %.f, Maximum Position: %.f"
                    % (market.get_delta(), settings.MAX_POSITION)
                )

            if self.short_position_limit_exceeded(symbol):
                logger.info("Short delta limit exceeded")
                logger.info(
                    "Current Position: %.f, Minimum Position: %.f"
                    % (market.get_delta(), settings.MIN_POSITION)
                )

    def print_orderbook(self, symbols=settings.SYMBOLS):
        """Print the market makers orderbook, for debugging."""
        for symbol in symbols:
            orderbook = self.markets[symbol].get_orders()
            # sort BUYS by price descending
            buys = sorted(
                [o for o in orderbook if o["order_info"]["side"] == "BUY"],
                key=lambda x: float(x["order_info"]["price"]),
                reverse=False,
            )
            # sort SELLS by price ascending
            sells = sorted(
                [o for o in orderbook if o["order_info"]["side"] == "SELL"],
                key=lambda x: float(x["order_info"]["price"]),
                reverse=False,
            )
            for buy in buys:
                logger.info(
                    f"BUY {symbol}: {buy['order_info']['qty']} @ {buy['order_info']['price']}"
                )
            for sell in sells:
                logger.info(
                    f"SELL {symbol}: {sell['order_info']['qty']} @ {sell['order_info']['price']}"
                )

    def run_loop(self, enhanced_reporting=None):
        """Main run loop.

        Args:
            enhanced_reporting: Enable enhanced status reporting (defaults to auto_start_enhanced)
        """
        if enhanced_reporting is None:
            enhanced_reporting = self.auto_start_enhanced

        while self.running:
            try:
                # Check for symbol-specific updates
                try:
                    symbol = self.queue.get(True, settings.LOOP_INTERVAL)
                    self.update_pricing([symbol])
                    self.check_sanity()
                    self.place_orders([symbol])

                except queue.Empty:
                    # Regular interval processing when no symbol-specific updates
                    self.update_pricing()
                    self.check_sanity()
                    self.place_orders()

                    # Check for reactive adjustments based on price movements
                    reactive_adjustments = self._check_for_reactive_adjustments()
                    if reactive_adjustments:
                        logger.debug("🚨 Reactive adjustments triggered")

                    # Print enhanced status periodically if enabled
                    if enhanced_reporting:
                        self.print_enhanced_status()

                except Exception as e:
                    # Handle other queue-related exceptions but don't swallow KeyboardInterrupt
                    logger.error(f"Error in queue processing: {e}")

            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Error in run loop: {e}")
                time.sleep(5)  # Brief pause on error


def run_enhanced(args=None):
    """Run the enhanced market maker with all enhanced features enabled."""
    logger.info("TrueX Enhanced Market Maker Version: %s" % constants.VERSION)

    order_manager = None

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        if order_manager:
            order_manager.exit(signum, frame)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

    try:
        order_manager = OrderManager(
            enable_external_data=True,
            enable_pricing_models=True,
            auto_start_enhanced=True,
        )
        order_manager.run_loop()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down.")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        raise
    finally:
        if order_manager:
            order_manager.exit()
