"""Order adjustment system based on pricing model changes."""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from market_maker.pricing import PricingResult
from market_maker.settings import settings
from market_maker.utils import log, math
from market_maker.utils.tick import GetQuoteTick

logger = log.setup_custom_logger("order_adjustment")


@dataclass
class OrderAdjustment:
    """Represents an order adjustment action."""

    action: str  # 'cancel', 'modify', 'keep'
    order_id: str
    symbol: str
    side: str
    current_price: float
    target_price: Optional[float] = None
    reason: str = ""
    priority: int = 1  # 1=high, 2=medium, 3=low


class OrderAdjustmentEngine:
    """Engine for determining order adjustments based on pricing changes."""

    def __init__(self, settings):
        self.settings = settings
        self.last_pricing_results = {}  # symbol -> PricingResult
        self.order_placement_times = {}  # order_id -> timestamp
        self.price_move_threshold = getattr(
            settings, "PRICE_MOVE_THRESHOLD", 0.002
        )  # 0.2%
        self.max_order_age = getattr(
            settings, "MAX_ORDER_AGE_SECONDS", 300
        )  # 5 minutes
        self.adjustment_cooldown = getattr(
            settings, "ORDER_ADJUSTMENT_COOLDOWN", 30
        )  # 30 seconds
        self.last_adjustment = {}  # symbol -> timestamp

    def should_adjust_orders(
        self, symbol: str, new_pricing: PricingResult, current_orders: List[Dict]
    ) -> List[OrderAdjustment]:
        """Determine if orders should be adjusted based on new pricing."""
        adjustments = []

        # Check cooldown
        if self._is_in_cooldown(symbol):
            return adjustments

        # Get previous pricing for comparison
        old_pricing = self.last_pricing_results.get(symbol)

        if old_pricing is None:
            # First pricing result - check if current orders are reasonable
            adjustments.extend(
                self._check_initial_pricing(symbol, new_pricing, current_orders)
            )
        else:
            # Compare with previous pricing
            adjustments.extend(
                self._check_pricing_changes(
                    symbol, old_pricing, new_pricing, current_orders
                )
            )

        # Check for stale orders
        adjustments.extend(self._check_stale_orders(symbol, current_orders))

        # Store new pricing
        self.last_pricing_results[symbol] = new_pricing

        # Sort by priority
        adjustments.sort(key=lambda x: x.priority)

        return adjustments

    def _is_in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in adjustment cooldown period."""
        last_adj = self.last_adjustment.get(symbol, 0)
        return time.time() - last_adj < self.adjustment_cooldown

    def _check_initial_pricing(
        self, symbol: str, pricing: PricingResult, orders: List[Dict]
    ) -> List[OrderAdjustment]:
        """Check orders against initial external pricing."""
        adjustments = []

        for order in orders:
            order_price = float(order["order_info"]["price"])
            side = order["order_info"]["side"]
            order_id = order["id"]

            # Calculate appropriate target price maintaining spread structure
            if side == "BUY":
                # Find the order's position in the buy ladder
                buy_orders = [o for o in orders if o["order_info"]["side"] == "BUY"]
                buy_orders.sort(
                    key=lambda x: float(x["order_info"]["price"]), reverse=True
                )  # Highest price first
                order_index = next(
                    (i for i, o in enumerate(buy_orders) if o["id"] == order_id), 0
                )

                # Calculate target price for this position in the ladder
                # Use bid_price as base and add offsets for ladder positions
                base_price = pricing.bid_price
                spread_increment = (
                    pricing.spread_bps / 10000 * pricing.fair_value * 0.1
                )  # Small increment between orders
                target_price = base_price - (order_index * spread_increment)

                # Allow generous tolerance for existing orders to prevent constant adjustments
                tolerance = target_price * 0.05  # 5% tolerance - much more generous

                if abs(order_price - target_price) > tolerance:
                    # Order price significantly different from target
                    new_price = self._round_to_tick(target_price)
                    adjustments.append(
                        OrderAdjustment(
                            action="modify",
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            current_price=order_price,
                            target_price=new_price,
                            reason=f"Buy order #{order_index+1} price {order_price:.4f} vs target {target_price:.4f}",
                            priority=2,
                        )
                    )

            else:  # SELL
                # Find the order's position in the sell ladder
                sell_orders = [o for o in orders if o["order_info"]["side"] == "SELL"]
                sell_orders.sort(
                    key=lambda x: float(x["order_info"]["price"])
                )  # Lowest price first
                order_index = next(
                    (i for i, o in enumerate(sell_orders) if o["id"] == order_id), 0
                )

                # Calculate target price for this position in the ladder
                base_price = pricing.ask_price
                spread_increment = pricing.spread_bps / 10000 * pricing.fair_value * 0.1
                target_price = base_price + (order_index * spread_increment)

                tolerance = target_price * 0.02

                if abs(order_price - target_price) > tolerance:
                    # Order price significantly different from target
                    new_price = self._round_to_tick(target_price)
                    adjustments.append(
                        OrderAdjustment(
                            action="modify",
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            current_price=order_price,
                            target_price=new_price,
                            reason=f"Sell order #{order_index+1} price {order_price:.4f} vs target {target_price:.4f}",
                            priority=2,
                        )
                    )

        return adjustments

    def _check_pricing_changes(
        self,
        symbol: str,
        old_pricing: PricingResult,
        new_pricing: PricingResult,
        orders: List[Dict],
    ) -> List[OrderAdjustment]:
        """Check if pricing changes warrant order adjustments."""
        adjustments = []

        # Calculate price movement
        old_mid = old_pricing.fair_value
        new_mid = new_pricing.fair_value

        if old_mid <= 0:
            return adjustments

        price_change_pct = abs(new_mid - old_mid) / old_mid

        # Only adjust if significant price movement
        if price_change_pct < self.price_move_threshold:
            return adjustments

        logger.info(
            f"Significant price movement for {symbol}: "
            f"{old_mid:.4f} -> {new_mid:.4f} ({price_change_pct:.2%})"
        )

        # Check confidence change
        confidence_change = new_pricing.confidence - old_pricing.confidence

        # Adjust orders based on new pricing
        for order in orders:
            order_price = float(order["order_info"]["price"])
            side = order["order_info"]["side"]
            order_id = order["id"]

            if side == "BUY":
                # Find the order's position in the buy ladder to maintain spread structure
                buy_orders = [o for o in orders if o["order_info"]["side"] == "BUY"]
                buy_orders.sort(
                    key=lambda x: float(x["order_info"]["price"]), reverse=True
                )
                order_index = next(
                    (i for i, o in enumerate(buy_orders) if o["id"] == order_id), 0
                )

                # Calculate target price for this ladder position
                base_price = new_pricing.bid_price
                spread_increment = (
                    new_pricing.spread_bps / 10000 * new_pricing.fair_value * 0.1
                )
                target_price = base_price - (order_index * spread_increment)

                # Check if price movement requires adjustment
                price_diff_pct = (
                    abs(order_price - target_price) / target_price
                    if target_price > 0
                    else 0
                )

                if price_diff_pct > self.price_move_threshold:
                    new_price = self._round_to_tick(target_price)
                    adjustments.append(
                        OrderAdjustment(
                            action="modify",
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            current_price=order_price,
                            target_price=new_price,
                            reason=f"Price moved, adjusting buy #{order_index+1} from {order_price:.4f} to {new_price:.4f}",
                            priority=1 if confidence_change > 0 else 2,
                        )
                    )

            else:  # SELL
                # Find the order's position in the sell ladder to maintain spread structure
                sell_orders = [o for o in orders if o["order_info"]["side"] == "SELL"]
                sell_orders.sort(key=lambda x: float(x["order_info"]["price"]))
                order_index = next(
                    (i for i, o in enumerate(sell_orders) if o["id"] == order_id), 0
                )

                # Calculate target price for this ladder position
                base_price = new_pricing.ask_price
                spread_increment = (
                    new_pricing.spread_bps / 10000 * new_pricing.fair_value * 0.1
                )
                target_price = base_price + (order_index * spread_increment)

                # Check if price movement requires adjustment
                price_diff_pct = (
                    abs(order_price - target_price) / target_price
                    if target_price > 0
                    else 0
                )

                if price_diff_pct > self.price_move_threshold:
                    new_price = self._round_to_tick(target_price)
                    adjustments.append(
                        OrderAdjustment(
                            action="modify",
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            current_price=order_price,
                            target_price=new_price,
                            reason=f"Price moved, adjusting sell #{order_index+1} from {order_price:.4f} to {new_price:.4f}",
                            priority=1 if confidence_change > 0 else 2,
                        )
                    )

        # Check if confidence dropped significantly
        if confidence_change < -0.2:  # 20% confidence drop
            # Consider canceling some orders
            for order in orders[-2:]:  # Cancel outermost orders
                adjustments.append(
                    OrderAdjustment(
                        action="cancel",
                        order_id=order["id"],
                        symbol=symbol,
                        side=order["order_info"]["side"],
                        current_price=float(order["order_info"]["price"]),
                        reason=f"Low confidence pricing (confidence dropped to {new_pricing.confidence:.2f})",
                        priority=1,
                    )
                )

        return adjustments

    def _check_stale_orders(
        self, symbol: str, orders: List[Dict]
    ) -> List[OrderAdjustment]:
        """Check for orders that have been open too long."""
        adjustments = []
        current_time = time.time()

        for order in orders:
            order_id = order["id"]

            # Estimate order age (this would be better with actual order timestamps)
            placement_time = self.order_placement_times.get(order_id, current_time)
            age = current_time - placement_time

            if age > self.max_order_age:
                adjustments.append(
                    OrderAdjustment(
                        action="cancel",
                        order_id=order_id,
                        symbol=symbol,
                        side=order["order_info"]["side"],
                        current_price=float(order["order_info"]["price"]),
                        reason=f"Order aged out ({age:.0f}s > {self.max_order_age}s)",
                        priority=3,
                    )
                )

        return adjustments

    def _round_to_tick(self, price: float) -> float:
        """Round price to appropriate tick size."""
        tick_size = GetQuoteTick(price)
        return math.toNearest(price, tick_size)

    def record_order_placement(self, order_id: str, timestamp: Optional[float] = None):
        """Record when an order was placed."""
        self.order_placement_times[order_id] = timestamp or time.time()

    def record_adjustment(self, symbol: str, timestamp: Optional[float] = None):
        """Record when an adjustment was made to reset cooldown."""
        self.last_adjustment[symbol] = timestamp or time.time()

    def get_adjustment_summary(self, adjustments: List[OrderAdjustment]) -> Dict:
        """Get summary of adjustments for logging."""
        summary = {
            "total": len(adjustments),
            "cancel": len([a for a in adjustments if a.action == "cancel"]),
            "modify": len([a for a in adjustments if a.action == "modify"]),
            "keep": len([a for a in adjustments if a.action == "keep"]),
            "high_priority": len([a for a in adjustments if a.priority == 1]),
        }
        return summary


class OrderAdjustmentExecutor:
    """Executes order adjustments on the market."""

    def __init__(self, market_interface):
        self.market = market_interface

    def execute_adjustments(
        self, adjustments: List[OrderAdjustment]
    ) -> Dict[str, bool]:
        """Execute a list of order adjustments."""
        results = {}

        # Execute in priority order
        for adjustment in adjustments:
            try:
                if adjustment.action == "cancel":
                    result = self._cancel_order(adjustment)
                elif adjustment.action == "modify":
                    result = self._modify_order(adjustment)
                else:  # keep
                    result = True

                results[adjustment.order_id] = result

                if result:
                    logger.info(
                        f"Successfully {adjustment.action}ed order {adjustment.order_id}: {adjustment.reason}"
                    )
                else:
                    logger.warning(
                        f"Failed to {adjustment.action} order {adjustment.order_id}"
                    )

            except Exception as e:
                logger.error(
                    f"Error executing adjustment for order {adjustment.order_id}: {e}"
                )
                results[adjustment.order_id] = False

        return results

    def _cancel_order(self, adjustment: OrderAdjustment) -> bool:
        """Cancel an order."""
        try:
            # Use the market interface to cancel the order
            result = self.market.cancel_order(adjustment.order_id)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to cancel order {adjustment.order_id}: {e}")
            return False

    def _modify_order(self, adjustment: OrderAdjustment) -> bool:
        """Modify an order price."""
        try:
            # Get current orders to find the original order details
            current_orders = self.market.get_orders()
            original_order = None

            # Find the original order
            for order in current_orders:
                if str(order.get("id", "")) == str(adjustment.order_id) or str(
                    order.get("external_id", "")
                ) == str(adjustment.order_id):
                    original_order = order
                    break

            if not original_order:
                logger.warning(
                    f"Could not find original order {adjustment.order_id} to modify"
                )
                return False

            # Calculate the correct ladder quantity for this order position BEFORE canceling
            # Find order position in the ladder to determine correct quantity
            current_orders = self.market.get_orders()

            if adjustment.side.upper() == "BUY":
                buy_orders = [
                    o
                    for o in current_orders
                    if o.get("order_info", {}).get("side") == "BUY"
                ]
                buy_orders.sort(
                    key=lambda x: float(x.get("order_info", {}).get("price", 0)),
                    reverse=True,
                )
                order_position = next(
                    (
                        i
                        for i, o in enumerate(buy_orders)
                        if str(o.get("id", "")) == str(adjustment.order_id)
                    ),
                    0,
                )
            else:  # SELL
                sell_orders = [
                    o
                    for o in current_orders
                    if o.get("order_info", {}).get("side") == "SELL"
                ]
                sell_orders.sort(
                    key=lambda x: float(x.get("order_info", {}).get("price", 0))
                )
                order_position = next(
                    (
                        i
                        for i, o in enumerate(sell_orders)
                        if str(o.get("id", "")) == str(adjustment.order_id)
                    ),
                    0,
                )

            # Cancel and replace strategy (since direct modification might not be supported)
            cancel_result = self.market.cancel_order(adjustment.order_id)

            if cancel_result:

                # Calculate correct quantity using the same logic as prepare_order()
                # quantity = ORDER_START_SIZE + ((position) * ORDER_STEP_SIZE)
                adjusted_quantity = settings.ORDER_START_SIZE + (
                    order_position * settings.ORDER_STEP_SIZE
                )

                # Round to proper quote size
                adjusted_quantity = math.toNearest(
                    adjusted_quantity, settings.QUOTE_SIZE
                )

                # Place new order with adjusted price and correct ladder quantity
                new_order = {
                    "client_id": self.market.get_client(),
                    "symbol": adjustment.symbol,
                    "price": str(adjustment.target_price),
                    "qty": str(adjusted_quantity),
                    "side": adjustment.side.upper(),  # Ensure uppercase (BUY/SELL)
                }

                logger.info(
                    f"🔄 Replacing order {adjustment.order_id}: {adjustment.side} {adjusted_quantity} {adjustment.symbol} @ {adjustment.target_price} (position #{order_position+1})"
                )

                # Place the new order using create_orders (which takes a list)
                results = self.market.create_orders([new_order])
                success = results and len(results) > 0 and results[0] is not None

                if success and results[0]:
                    new_order_id = results[0].get("id", "unknown")
                    logger.info(
                        f"✅ Order replaced successfully: {adjustment.order_id} → {new_order_id}"
                    )
                    return True
                else:
                    logger.error(
                        f"❌ Failed to place replacement order for {adjustment.order_id}"
                    )
                    return False
            else:
                logger.error(
                    f"❌ Failed to cancel original order {adjustment.order_id}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to modify order {adjustment.order_id}: {e}")
            return False
