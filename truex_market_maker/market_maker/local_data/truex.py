"""TrueX local data provider and API connector."""

import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import json
import queue
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
import websockets
from requests.auth import AuthBase

from market_maker.external_data.base import ExternalDataProvider, MarketData
from market_maker.settings import settings
from market_maker.utils import constants, log

logger = log.setup_custom_logger("truex_local")


class RESTAuth(AuthBase):
    """Attaches API Key Authentication to the given Request object."""

    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_client = 0

    def __call__(self, request):
        # modify and return the request
        timestamp = str(int(time.time()))
        message = (
            timestamp
            + request.method
            + urlparse(request.path_url).path
            + str(request.body or "")
        )
        hmac_key = str.encode(self.api_secret)
        signature = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
        request.headers["x-truex-auth-token"] = self.api_key
        request.headers["x-truex-auth-signature"] = base64.b64encode(signature).decode()
        request.headers["x-truex-auth-timestamp"] = timestamp
        request.headers["x-truex-auth-user-id"] = str(self.api_client)
        return request

    def set_api_client(self, api_client):
        self.api_client = api_client


class WebsocketAuth:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret

    def __call__(self, request):
        timestamp = str(int(time.time()))
        message = timestamp + "TRUEXWS" + self.api_key
        hmac_key = str.encode(self.api_secret)
        signature = hmac.new(
            hmac_key, message.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        request["key"] = self.api_key
        request["timestamp"] = timestamp
        request["signature"] = base64.b64encode(signature).decode("utf-8")
        return request


class TruexDataProvider(ExternalDataProvider):
    """TrueX data provider that combines REST and WebSocket functionality."""

    def __init__(self, queue_callback=None):
        super().__init__("truex_local")

        # REST client setup
        self.base_url = settings.BASE_REST_URL
        self.session = requests.Session()
        self.session.headers.update({"user-agent": "truexbot-" + constants.VERSION})
        self.session.headers.update({"content-type": "application/json"})
        self.session.headers.update({"accept": "application/json"})
        self.auth = RESTAuth(settings.API_KEY, settings.API_SECRET)

        # WebSocket setup
        self.ws_url = settings.BASE_WS_URL
        self.ws_auth = WebsocketAuth(settings.API_KEY, settings.API_SECRET)
        self.ws_thread = None
        self.ws_loop = None
        self.ws_event = None
        self.send_queue = None
        self.recv_task = None
        self.send_task = None

        # Data storage
        self.ws_data = {}  # Internal WebSocket data store
        self.ws_seqnum = {}
        self.symbol_map = {}  # instrument_id -> symbol mapping
        self.market_data_cache = {}  # symbol -> MarketData

        # Queue callback for backward compatibility
        self.queue_callback = queue_callback

        # Order management
        self.order_id_counter = 0
        self.amend_id_counter = 0
        self.order_id_prefix = getattr(settings, "ORDERID_PREFIX", "mm-") + "0-"

        logger.info("TruexDataProvider initialized")

    # ==========================================
    # External Data Provider Interface
    # ==========================================

    async def connect(self) -> bool:
        """Connect to TrueX REST and WebSocket APIs."""
        try:
            # Test REST connection
            client_data = self.get_client()
            if not client_data:
                logger.error("Failed to connect to REST API")
                return False

            # Start WebSocket connection
            if not self.connected:
                self._start_websocket()

            self.connected = True
            logger.info("Connected to TrueX (REST + WebSocket)")
            return True

        except Exception as e:
            logger.error(f"Error connecting to TrueX: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from TrueX APIs."""
        try:
            self.connected = False

            # Stop WebSocket gracefully
            if self.ws_thread and self.ws_thread.is_alive():
                if self.ws_loop and not self.ws_loop.is_closed():
                    try:
                        # Cancel tasks if they exist and are not done
                        if self.recv_task and not self.recv_task.done():
                            self.recv_task.cancel()
                        if self.send_task and not self.send_task.done():
                            self.send_task.cancel()

                        # Schedule shutdown on the WebSocket event loop
                        future = asyncio.run_coroutine_threadsafe(
                            self._shutdown_websocket(), self.ws_loop
                        )

                        # Wait for graceful shutdown
                        try:
                            future.result(timeout=3.0)
                        except concurrent.futures.TimeoutError:
                            logger.warning("WebSocket shutdown timeout, forcing stop")
                            # Force stop the loop as last resort
                            if not self.ws_loop.is_closed():
                                self.ws_loop.call_soon_threadsafe(self.ws_loop.stop)

                    except Exception as e:
                        logger.warning(f"Error during WebSocket shutdown: {e}")

                # Wait for thread to finish
                self.ws_thread.join(timeout=5)
                if self.ws_thread.is_alive():
                    logger.warning("WebSocket thread did not shutdown gracefully")

            logger.info("Disconnected from TrueX")

        except Exception as e:
            logger.error(f"Error disconnecting from TrueX: {e}")

    async def subscribe(self, symbol: str) -> bool:
        """Subscribe to market data for a symbol."""
        try:
            # Subscribe to instrument data first
            self._ws_subscribe_to_instrument(symbol)

            # Subscribe to market data (ticker)
            self._ws_subscribe_to_ticker(symbol)

            # Update last update time
            self.last_update[symbol] = time.time()

            logger.info(f"Subscribed to {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {e}")
            return False

    async def unsubscribe(self, symbol: str) -> bool:
        """Unsubscribe from market data for a symbol."""
        try:
            self._ws_unsubscribe_from_instrument(symbol)
            self._ws_unsubscribe_from_ticker(symbol)

            # Remove from cache
            if symbol in self.market_data_cache:
                del self.market_data_cache[symbol]
            if symbol in self.last_update:
                del self.last_update[symbol]

            logger.info(f"Unsubscribed from {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error unsubscribing from {symbol}: {e}")
            return False

    async def get_current_data(self, symbol: str) -> Optional[MarketData]:
        """Get current market data for a symbol."""
        try:
            # Get ticker data from WebSocket
            ticker = self._get_ticker_from_ws(symbol)
            if not ticker:
                return None

            # Convert to MarketData format
            market_data = MarketData(
                symbol=symbol,
                bid=ticker.get("buy", 0.0),
                ask=ticker.get("sell", 0.0),
                last=ticker.get("last", 0.0),
                volume=ticker.get(
                    "volume", 0.0
                ),  # Volume not available from TrueX ticker
                timestamp=time.time(),
                source=self.name,
            )

            # Cache the data
            self.market_data_cache[symbol] = market_data

            # Notify callbacks
            self._notify_callbacks(market_data)

            return market_data

        except Exception as e:
            logger.error(f"Error getting current data for {symbol}: {e}")
            return None

    # ==========================================
    # TrueX-specific API methods (backward compatibility)
    # ==========================================

    def set_api_client(self, client_id: int):
        """Set the API client ID."""
        self.auth.set_api_client(client_id)

    def get_client(self):
        """Get client information."""
        try:
            url = self.base_url + "/client"
            response = self._rest_request("GET", url)
            return response
        except Exception as e:
            logger.error(f"Error getting client: {e}")
            return None

    def get_instrument(self, symbol: str):
        """Get instrument information."""
        try:
            url = self.base_url + "/instrument?symbol=" + symbol
            return self._rest_request("GET", url)
        except Exception as e:
            logger.error(f"Error getting instrument {symbol}: {e}")
            return None

    def get_balance(self, asset_id: str):
        """Get balance for an asset."""
        try:
            url = self.base_url + "/balance?asset_id=" + asset_id
            return self._rest_request("GET", url)
        except Exception as e:
            logger.error(f"Error getting balance for {asset_id}: {e}")
            return None

    def get_open_orders(self):
        """Get open orders."""
        try:
            url = self.base_url + "/order/active"
            orders = self._rest_request("GET", url)
            # Filter orders by prefix
            filtered_orders = []
            for order in orders:
                if order["external_id"].startswith(self.order_id_prefix) or order[
                    "ref_external_id"
                ].startswith(self.order_id_prefix):
                    filtered_orders.append(order)
            return filtered_orders
        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return []

    def place_order(self, order_data: dict):
        """Place an order."""
        try:
            self.order_id_counter += 1
            order = {
                "external_id": self.order_id_prefix + str(self.order_id_counter),
                "info": {
                    "client_id": str(order_data["client_id"]),
                    "instrument_id": self._get_instrument_id(order_data["symbol"]),
                    "qty": order_data["qty"],
                    "price": order_data["price"],
                    "side": order_data["side"],
                    "type": "LIMIT",
                    "tif": "GTC",
                    "exec_inst_flags": (
                        ["ALO"] if getattr(settings, "POST_ONLY", False) else []
                    ),
                },
            }

            url = self.base_url + "/order"
            return self._rest_request("POST", url, body=json.dumps(order))

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None

    def create_orders(self, orders: List[dict]):
        """Create multiple orders."""
        results = []
        for order in orders:
            result = self.place_order(order)
            results.append(result)
        return results

    def amend_order(self, order_data: dict):
        """Amend an order."""
        try:
            self.amend_id_counter += 1
            modify = {
                "id": order_data["ref_order_id"],
                "external_id": self.order_id_prefix
                + "mod-"
                + str(self.amend_id_counter),
                "info": {
                    "client_id": order_data["client_id"],
                    "new_qty": order_data["new_qty"],
                    "new_price": order_data["new_price"],
                },
            }

            url = self.base_url + "/order"
            return self._rest_request("PATCH", url, body=json.dumps(modify))

        except Exception as e:
            logger.error(f"Error amending order: {e}")
            return None

    def amend_orders(self, orders: List[dict]):
        """Amend multiple orders."""
        results = []
        for order in orders:
            result = self.amend_order(order)
            results.append(result)
        return results

    def cancel_order(self, order_id: str):
        """Cancel an order."""
        try:
            url = self.base_url + "/order/" + str(order_id)
            return self._rest_request("DELETE", url)
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return None

    # ==========================================
    # WebSocket-specific methods (backward compatibility)
    # ==========================================

    def get_position(self, symbol: str):
        """Get position for a symbol."""
        if "POSITION" not in self.ws_data:
            self.ws_data["POSITION"] = {}

        positions = self.ws_data["POSITION"]
        if symbol not in positions:
            # Stub out a position if we don't have one
            positions[symbol] = {"qty": 0, "executed_vwap": 0, "entry_vwap": 0}

        return positions[symbol]

    def get_ticker(self, symbol: str):
        """Get ticker for a symbol."""
        return self._get_ticker_from_ws(symbol)

    def is_market_open(self, symbol: str) -> bool:
        """Check if market is open for a symbol."""
        if not self.ws_data:
            return False

        if "INSTRUMENT" not in self.ws_data:
            return False

        symbols = self.ws_data["INSTRUMENT"]
        if symbol not in symbols:
            logger.error(f"Unknown symbol: {symbol}")
            return False

        logger.debug(f"{symbol} status: {symbols[symbol]['status']}")
        return symbols[symbol]["status"] == "ACTIVE"

    def get_instrument_id(self, symbol: str):
        """Get instrument ID for a symbol."""
        return self._get_instrument_id(symbol)

    def get_base_asset_id(self, symbol: str):
        """Get base asset ID for a symbol."""
        symbols = self.ws_data.get("INSTRUMENT", {})
        if symbol not in symbols:
            return None
        return symbols[symbol]["info"]["base_asset_id"]

    def get_quote_asset_id(self, symbol: str):
        """Get quote asset ID for a symbol."""
        symbols = self.ws_data.get("INSTRUMENT", {})
        if symbol not in symbols:
            return None
        return symbols[symbol]["info"]["quote_asset_id"]

    # ==========================================
    # Internal helper methods
    # ==========================================

    def _rest_request(self, method: str, url: str, body: str = None):
        """Make a REST API request."""
        timeout = getattr(settings, "API_REST_TIMEOUT", 30)

        try:
            logger.debug(f"Sending {method} to {url} with body {body}")
            request = requests.Request(method, url, data=body, auth=self.auth)
            prepped = self.session.prepare_request(request)
            response = self.session.send(prepped, timeout=timeout)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Error in request: {e}")
            raise Exception(f"Error in request: {e}")

    def _start_websocket(self):
        """Start WebSocket connection in background thread."""
        if self.ws_thread and self.ws_thread.is_alive():
            logger.warning("WebSocket is already running.")
            return

        self.ws_event = threading.Event()
        self.ws_thread = threading.Thread(
            target=self._run_ws_loop, args=(self.ws_url,), daemon=True
        )
        self.ws_thread.start()

        # Wait for setup
        setup = self.ws_event.wait(10)
        if not setup:
            raise Exception("Failed to setup websocket connection")
        logger.info("WebSocket connection started in the background.")

    def _run_ws_loop(self, url):
        """Run the WebSocket event loop in a background thread."""
        self.ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.ws_loop)
        try:
            self.ws_loop.run_until_complete(self._connect_websocket(url))
        except Exception as e:
            logger.error(f"Error during WebSocket connection: {e}")
        finally:
            self.ws_loop.close()

    async def _connect_websocket(self, url):
        """Connect to the WebSocket server."""
        logger.info(f"Connecting to {url}")
        try:
            async with websockets.connect(url) as ws:
                self.ws_event.set()
                self.send_queue = asyncio.Queue()
                self.recv_task = asyncio.create_task(self._receive_messages(ws))
                self.send_task = asyncio.create_task(self._send_messages(ws))

                # Wait for both tasks with proper exception handling
                try:
                    await asyncio.gather(self.recv_task, self.send_task)
                except asyncio.CancelledError:
                    # Expected during shutdown - suppress the exception
                    logger.debug(
                        "WebSocket connection tasks cancelled (expected during shutdown)"
                    )
                    raise  # Re-raise to properly close the connection

        except asyncio.CancelledError:
            # Re-raise cancellation to close connection properly
            raise
        except Exception as e:
            logger.error(f"Error connecting to {url}: {e}")

    async def _shutdown_websocket(self):
        """Gracefully shutdown WebSocket tasks and connections."""
        try:
            # Cancel tasks if they exist
            tasks_to_cancel = []
            if self.recv_task and not self.recv_task.done():
                tasks_to_cancel.append(self.recv_task)
            if self.send_task and not self.send_task.done():
                tasks_to_cancel.append(self.send_task)

            if tasks_to_cancel:
                # Cancel all tasks
                for task in tasks_to_cancel:
                    task.cancel()

                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

            # Stop the event loop
            self.ws_loop.stop()

        except Exception as e:
            logger.debug(f"Error during WebSocket shutdown (expected): {e}")

    async def _receive_messages(self, ws):
        """Receive messages from the WebSocket server."""
        self.ws_seqnum = {}
        self.ws_data = {}
        self.symbol_map = {}

        async for message in ws:
            try:
                msg = json.loads(message)
                logger.debug(f"Received message: {msg}")

                await self._process_ws_message(msg)

            except Exception as e:
                logger.error(f"Error processing message: {e}")
                continue

    async def _send_messages(self, ws):
        """Send messages to the WebSocket server."""
        try:
            while True:
                try:
                    message = await self.send_queue.get()
                    await ws.send(message)
                except Exception as e:
                    logger.error(f"Error sending message: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error in send loop: {e}")

    async def _process_ws_message(self, msg):
        """Process a WebSocket message."""
        chn = msg["channel"]
        upd = msg["update"]

        try:
            if chn == "WEBSOCKET":
                if upd == "WELCOME":
                    logger.info(
                        f"Welcome message: {msg['message']} v{msg['version']} @ {msg['datetime']}"
                    )
                elif upd == "SNAPSHOT":
                    for subscription in msg["subscriptions"]:
                        if subscription["channel"] == "INSTRUMENT":
                            logger.info(
                                f"Currently subscribed: {subscription['item_names']}"
                            )

            elif chn == "INSTRUMENT":
                if chn not in self.ws_data:
                    self.ws_data[chn] = {}

                if upd == "SNAPSHOT":
                    self.ws_seqnum[chn] = msg["seqnum"]
                    symbol = msg["data"]["info"]["symbol"]
                    self.ws_data[chn][symbol] = msg["data"]
                    self.symbol_map[msg["data"]["id"]] = symbol

                    if self.queue_callback:
                        self.queue_callback.put(symbol)

                if upd == "UPDATE":
                    if msg["seqnum"] != self.ws_seqnum[chn]:
                        gap = msg["seqnum"] - self.ws_seqnum[chn]
                        logger.error(f"Gap of {gap} msgs detected in {chn}")

                    symbol = msg["data"]["info"]["symbol"]
                    self.ws_data[chn][symbol] = msg["data"]
                    self.ws_seqnum[chn] = str(int(msg["seqnum"]) + 1)

            elif chn == "EBBO":
                if chn not in self.ws_data:
                    self.ws_data[chn] = {}

                instrument_id = msg["data"]["id"]
                if instrument_id not in self.symbol_map:
                    logger.error(f"Unknown instrument id: {instrument_id}")
                    return
                symbol = self.symbol_map[instrument_id]

                if upd == "SNAPSHOT":
                    self.ws_seqnum[chn] = msg["seqnum"]
                    self.ws_data[chn][symbol] = msg["data"]
                if upd == "UPDATE":
                    if msg["seqnum"] != self.ws_seqnum[chn]:
                        gap = int(msg["seqnum"]) - int(self.ws_seqnum[chn])
                        logger.error(f"Gap of {gap} msgs detected in {chn}")

                    self.ws_data[chn][symbol] = msg["data"]
                    self.ws_seqnum[chn] = str(int(msg["seqnum"]) + 1)

                    # Update market data and notify callbacks
                    await self.get_current_data(symbol)

            elif chn == "TRADE":
                if chn not in self.ws_data:
                    self.ws_data[chn] = {}
                    self.ws_seqnum[chn] = 0

                instrument_id = msg["data"]["id"]
                if instrument_id not in self.symbol_map:
                    logger.error(f"Unknown instrument id: {instrument_id}")
                    return
                symbol = self.symbol_map[instrument_id]

                if msg["seqnum"] != self.ws_seqnum[chn]:
                    gap = int(msg["seqnum"]) - int(self.ws_seqnum[chn])
                    logger.error(f"Gap of {gap} msgs detected in {chn}")

                logger.info(f"Trade {symbol}: {msg}")
                self.ws_data[chn][symbol] = msg["data"]
                self.ws_seqnum[chn] = str(int(msg["seqnum"]) + 1)

                if self.queue_callback:
                    self.queue_callback.put(symbol)

            else:
                raise Exception(f"Unknown channel: {chn}")

        except Exception as e:
            logger.error(f"Error processing {chn} message: {e}")

    def _get_ticker_from_ws(self, symbol: str):
        """Get ticker data from WebSocket."""
        if "EBBO" not in self.ws_data:
            self.ws_data["EBBO"] = {}

        ticker = {}
        tickers = self.ws_data["EBBO"]
        ticker_info = {}

        if symbol not in tickers:
            if "INSTRUMENT" not in self.ws_data:
                return False
            instrument = self.ws_data["INSTRUMENT"]
            if symbol not in instrument:
                return None
            # Stub out a ticker if we don't have one
            ref_px = instrument[symbol]["info"]["reference_price"]
            ticker["mid"] = ticker["buy"] = ticker["sell"] = ticker["last"] = float(
                ref_px
            )
        else:
            ticker_info = tickers[symbol]["info"]
            if (
                ticker_info["best_ask"]["price"] == "0"
                or ticker_info["best_bid"]["price"] == "0"
                or ticker_info["best_bid"]["price"]
                == "-170141183460469231731.687303715884105728"
                or ticker_info["best_ask"]["price"]
                == "170141183460469231731.687303715884105727"
            ):
                instrument = self.ws_data["INSTRUMENT"]
                ref_px = instrument[symbol]["info"]["reference_price"]
                ticker["mid"] = ticker["buy"] = ticker["sell"] = ticker["last"] = float(
                    ref_px
                )
            else:
                ticker = {
                    "mid": (
                        float(ticker_info["best_bid"]["price"])
                        + float(ticker_info["best_ask"]["price"])
                    )
                    // 2.0,
                    "buy": float(ticker_info["best_bid"]["price"]),
                    "sell": float(ticker_info["best_ask"]["price"]),
                    "last": float(ticker_info["last_trade"]["price"]),
                }

        logger.debug(f"Ticker info: {ticker_info}")
        logger.debug(f"Ticker: {ticker}")
        return ticker

    def _get_instrument_id(self, symbol: str):
        """Get instrument ID for a symbol."""
        # First try WebSocket data
        symbols = self.ws_data.get("INSTRUMENT", {})
        if symbol in symbols:
            return symbols[symbol]["id"]

        # Fallback: get from REST API if WebSocket data not available
        logger.warning(
            f"WebSocket instrument data not available for {symbol}, fetching via REST API"
        )
        try:
            instrument_data = self.get_instrument(symbol)
            if instrument_data and "id" in instrument_data:
                return instrument_data["id"]
        except Exception as e:
            logger.error(f"Error fetching instrument data for {symbol}: {e}")

        logger.error(f"Could not get instrument ID for {symbol}")
        return None

    def _ws_subscribe_to_instrument(self, symbol: str):
        """Subscribe to instrument data via WebSocket."""
        request = {
            "type": "SUBSCRIBE",
            "item_names": [symbol],
            "channels": ["INSTRUMENT"],
        }
        logger.info(f"Subscribing to instrument {symbol}")
        if self.send_queue and self.ws_loop:
            asyncio.run_coroutine_threadsafe(
                self.send_queue.put(json.dumps(self.ws_auth(request))), self.ws_loop
            )

    def _ws_unsubscribe_from_instrument(self, symbol: str):
        """Unsubscribe from instrument data via WebSocket."""
        request = {
            "type": "UNSUBSCRIBE",
            "item_names": [symbol],
            "channels": ["INSTRUMENT"],
        }
        if self.send_queue and self.ws_loop:
            asyncio.run_coroutine_threadsafe(
                self.send_queue.put(json.dumps(self.ws_auth(request))), self.ws_loop
            )

    def _ws_subscribe_to_ticker(self, symbol: str):
        """Subscribe to ticker data via WebSocket."""
        request = {
            "type": "SUBSCRIBE",
            "item_names": [symbol],
            "channels": ["EBBO", "TRADE"],
        }
        logger.info(f"Subscribing to ticker {symbol}")
        if self.send_queue and self.ws_loop:
            asyncio.run_coroutine_threadsafe(
                self.send_queue.put(json.dumps(self.ws_auth(request))), self.ws_loop
            )

    def _ws_unsubscribe_from_ticker(self, symbol: str):
        """Unsubscribe from ticker data via WebSocket."""
        request = {
            "type": "UNSUBSCRIBE",
            "item_names": [symbol],
            "channels": ["EBBO", "TRADE"],
        }
        if self.send_queue and self.ws_loop:
            asyncio.run_coroutine_threadsafe(
                self.send_queue.put(json.dumps(self.ws_auth(request))), self.ws_loop
            )


class TrueX:
    """TrueX API Connector - Backward Compatible Adapter."""

    def __init__(
        self,
        rest_url=None,
        ws_url=None,
        symbol=None,
        apiKey=None,
        apiSecret=None,
        orderIDPrefix="mm-",
        orderNode=0,
        shouldWSAuth=True,
        postOnly=False,
        timeout=7,
        queue=None,
    ):
        """Init connector."""
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.symbol = symbol
        self.postOnly = postOnly
        self.apiKey = apiKey
        self.apiSecret = apiSecret
        self.apiClient = 0
        self.orderNode = orderNode
        self.timeout = timeout

        # Initialize the unified provider
        self.provider = TruexDataProvider(queue_callback=queue)

        # Connect synchronously (for backward compatibility)
        try:
            # Run connection in asyncio loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            connected = loop.run_until_complete(self.provider.connect())
            loop.close()

            if not connected:
                raise Exception("Failed to connect to TrueX")

        except Exception as e:
            logger.error(f"Error initializing TrueX: {e}")
            raise

        logger.info("TrueX API Client successfully initialized.")

    def Instrument(self, symbol=None):
        """Subscribe to instrument's details/updates and ticker data."""
        if symbol is None:
            symbol = self.symbol
        # Run subscription asynchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.provider.subscribe(symbol))
        loop.close()

    def InstrumentData(self, symbol=None):
        """Get instrument data."""
        if symbol is None:
            symbol = self.symbol
        return self.provider.get_instrument(symbol)

    def BaseBalance(self):
        """Get base balance."""
        base_asset_id = self.provider.get_base_asset_id(self.symbol)
        if base_asset_id is None:
            return {}
        return self.provider.get_balance(base_asset_id)

    def QuoteBalance(self):
        """Get quote balance."""
        quote_asset_id = self.provider.get_quote_asset_id(self.symbol)
        if quote_asset_id is None:
            return {}
        return self.provider.get_balance(quote_asset_id)

    def Position(self, symbol=None):
        """Get position."""
        if symbol is None:
            symbol = self.symbol
        return self.provider.get_position(symbol)

    def OpenOrders(self):
        """Get open orders."""
        return self.provider.get_open_orders()

    def CreateOrders(self, orders):
        """Create orders."""
        return self.provider.create_orders(orders)

    def PlaceOrder(self, order):
        """Place an order."""
        return self.provider.place_order(order)

    def AmendOrders(self, orders):
        """Amend orders."""
        return self.provider.amend_orders(orders)

    def AmendOrder(self, order):
        """Amend an order."""
        return self.provider.amend_order(order)

    def CancelOrder(self, orderID):
        """Cancel an order."""
        return self.provider.cancel_order(orderID)

    def Market(self, symbol=None):
        """Subscribe to market data."""
        if symbol is None:
            symbol = self.symbol
        # Market subscription is handled by the Instrument() method
        # This is for backward compatibility
        pass

    def Ticker(self, symbol=None):
        """Get ticker data."""
        if symbol is None:
            symbol = self.symbol
        return self.provider.get_ticker(symbol)

    def IsMarketOpen(self, symbol=None):
        """Check if the market is open right now."""
        if symbol is None:
            symbol = self.symbol
        return self.provider.is_market_open(symbol)

    def Client(self):
        """Get client data."""
        return self.provider.get_client()

    def SetApiClient(self, clientId):
        """Set API client ID."""
        self.apiClient = clientId
        self.provider.set_api_client(clientId)

    def Exit(self):
        """Exit and disconnect."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.provider.unsubscribe(self.symbol))
            loop.close()
        except Exception as e:
            logger.warning(f"Error during exit: {e}")
        finally:
            try:
                loop.close()
            except Exception as e:
                logger.debug(f"Error closing event loop: {e}")
