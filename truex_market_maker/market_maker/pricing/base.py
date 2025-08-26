"""Base classes and data structures for pricing models."""

import abc
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.settings import settings
from market_maker.utils import log, math
from market_maker.utils.tick import GetQuoteTick

logger = log.setup_custom_logger("pricing_base")


@dataclass
class OrderLevel:
    """Represents a single order level in the ladder."""

    price: float
    level: int  # 1, 2, 3, etc. (1 is closest to mid)


@dataclass
class OrderPricingResult:
    """Complete order ladder with buy and sell levels."""

    model_name: str
    symbol: str
    buy_levels: List[OrderLevel] = field(default_factory=list)
    sell_levels: List[OrderLevel] = field(default_factory=list)
    timestamp: float = 0.0
    confidence: float = 0.0  # 0-1, how confident we are in this pricing

    @property
    def total_levels(self) -> int:
        """Total number of order pairs."""
        return min(len(self.buy_levels), len(self.sell_levels))

    @property
    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price."""
        if self.buy_levels:
            return max(level.price for level in self.buy_levels)
        return None

    @property
    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price."""
        if self.sell_levels:
            return min(level.price for level in self.sell_levels)
        return None


@dataclass
class PricingResult:
    """Result from a pricing model."""

    symbol: str
    fair_value: float
    bid_price: float
    ask_price: float
    confidence: float  # 0-1, how confident we are in this pricing
    spread_bps: float  # Spread in basis points
    timestamp: float
    model_name: str
    metadata: Dict = None  # Additional model-specific data

    @property
    def mid_price(self) -> float:
        """Calculate mid price."""
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread_pct(self) -> float:
        """Spread as percentage."""
        return self.spread_bps / 100.0


class PricingModel(abc.ABC):
    """Base class for pricing models."""

    def __init__(self, name: str):
        """Initialize the pricing model."""
        self.name = name
        self.enabled = True
        self.last_update = {}

    @abc.abstractmethod
    def get_required_providers(self) -> List[str]:
        """Return list of required external data provider names."""
        pass

    @abc.abstractmethod
    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price for the given symbol."""
        pass

    @abc.abstractmethod
    def calculate_order_prices(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[OrderPricingResult]:
        """Calculate order prices for the given symbol."""
        pass

    def is_data_sufficient(
        self, symbol: str, external_data: ExternalDataManager
    ) -> bool:
        """Check if we have sufficient data for pricing."""
        required_providers = self.get_required_providers()

        # If no specific providers required, check if we have any data
        if not required_providers:
            return len(external_data.get_all_data(symbol)) > 0

        # Check that all required providers have fresh data
        for provider in required_providers:
            if not external_data.has_data(symbol, provider):
                return False
            if not external_data.providers[provider].is_data_fresh(symbol):
                return False

        return True

    def get_order_prices(
        self, symbol: str, bid_price: float, ask_price: float, confidence: float
    ) -> Optional[OrderPricingResult]:
        order_pricing = OrderPricingResult(
            model_name="Base", symbol=symbol, timestamp=time.time()
        )
        order_pricing.confidence = confidence
        # Get configuration values
        order_pairs = getattr(settings, "ORDER_PAIRS", 6)
        interval = getattr(settings, "INTERVAL", 0.01)
        order_start_size = getattr(settings, "ORDER_START_SIZE", 0.1)
        order_step_size = getattr(settings, "ORDER_STEP_SIZE", 0.1)
        quote_size = getattr(settings, "QUOTE_SIZE", 0.0001)

        order_pricing.buy_levels.append(OrderLevel(price=0.0, level=0))
        for i in range(1, order_pairs + 1):
            # Calculate price with interval spacing outward from bid
            price = bid_price * (1 - interval) ** (i - 1)
            tick_size = GetQuoteTick(price)
            price = math.toNearest(price, tick_size)

            buy_level = OrderLevel(price=price, level=i)
            order_pricing.buy_levels.append(buy_level)

        order_pricing.sell_levels.append(OrderLevel(price=0.0, level=0))
        for i in range(1, order_pairs + 1):
            # Calculate price with interval spacing outward from ask
            price = ask_price * (1 + interval) ** (i - 1)
            tick_size = GetQuoteTick(price)
            price = math.toNearest(price, tick_size)

            sell_level = OrderLevel(price=price, level=i)
            order_pricing.sell_levels.append(sell_level)

        return order_pricing
