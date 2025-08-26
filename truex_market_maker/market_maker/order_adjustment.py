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


class OrderAdjustmentTracker:
    """Tracker for determining order adjustments based on pricing changes."""

    def __init__(self, settings):
        self.settings = settings
        self.last_pricing_results = {}  # symbol -> PricingResult
        self.order_placement_times = {}  # order_id -> timestamp
        self.price_move_threshold = getattr(
            settings, "PRICE_MOVE_THRESHOLD", 0.002
        )  # 0.2%
        self.price_move_tolerance = getattr(
            settings, "PRICE_MOVE_TOLERANCE", 0.0002
        )  # 0.02%
        self.max_order_age = getattr(
            settings, "MAX_ORDER_AGE_SECONDS", 300
        )  # 5 minutes
        self.adjustment_cooldown = getattr(
            settings, "ORDER_ADJUSTMENT_COOLDOWN", 30
        )  # 30 seconds
        self.adjustment_interval = getattr(
            settings, "ORDER_ADJUSTMENT_INTERVAL", 3
        )  # 3 seconds
        self.last_adjustment = {}  # symbol -> timestamp

    def _is_in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in adjustment cooldown period."""
        last_adj = self.last_adjustment.get(symbol, 0)
        return time.time() - last_adj < self.adjustment_cooldown

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
