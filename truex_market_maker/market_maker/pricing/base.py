"""Base classes and data structures for pricing models."""

import abc
from dataclasses import dataclass
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.utils import log

logger = log.setup_custom_logger("pricing_base")


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
    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price for the given symbol."""
        pass

    @abc.abstractmethod
    def get_required_providers(self) -> List[str]:
        """Return list of required external data provider names."""
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
