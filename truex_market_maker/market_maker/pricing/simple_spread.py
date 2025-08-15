"""Simple spread pricing model."""

import time
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("simple_spread_model")


class SimpleSpreadModel(PricingModel):
    """Simple model that adds a fixed spread to external reference price."""

    def __init__(self, spread_bps: float = 50, reference_provider: str = "coinbase"):
        super().__init__("simple_spread")
        self.spread_bps = spread_bps
        self.reference_provider = reference_provider

    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price using simple spread model."""
        if not self.is_data_sufficient(symbol, external_data):
            return None

        ref_data = external_data.get_data(symbol, self.reference_provider)
        if not ref_data:
            return None

        # Use reference mid as fair value
        fair_value = ref_data.mid
        spread_amount = fair_value * (self.spread_bps / 10000)

        return PricingResult(
            symbol=symbol,
            fair_value=fair_value,
            bid_price=fair_value - spread_amount / 2,
            ask_price=fair_value + spread_amount / 2,
            confidence=0.8,
            spread_bps=self.spread_bps,
            timestamp=time.time(),
            model_name=self.name,
            metadata={
                "reference_provider": self.reference_provider,
                "reference_mid": ref_data.mid,
                "reference_spread": ref_data.spread,
            },
        )

    def get_required_providers(self) -> List[str]:
        return [self.reference_provider]
