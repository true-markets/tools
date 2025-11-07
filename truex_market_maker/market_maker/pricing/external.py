"""External data pricing model."""

import time
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import OrderPricingResult, PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("external_model")


class ExternalDataModel(PricingModel):
    """A simple model that uses external data to construct pricing."""

    def __init__(self, external_provider: str = "coinbase"):
        super().__init__("external_data")
        self.external_provider = external_provider

    def get_required_providers(self) -> List[str]:
        return [self.external_provider]

    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price using external data source."""
        if not self.is_data_sufficient(symbol, external_data):
            return None

        ref_data = external_data.get_data(symbol, self.reference_provider)
        if not ref_data:
            return None

        # Use reference mid as fair value
        fair_value = ref_data.mid
        spread_amount = ref_data.spread

        return PricingResult(
            symbol=symbol,
            fair_value=fair_value,
            bid_price=fair_value - spread_amount / 2,
            ask_price=fair_value + spread_amount / 2,
            confidence=1.0,
            spread_bps=spread_amount / 1000,
            timestamp=time.time(),
            model_name=self.name,
            metadata={
                "reference_provider": self.reference_provider,
                "reference_mid": ref_data.mid,
                "reference_spread": ref_data.spread,
            },
        )

    def calculate_order_prices(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[OrderPricingResult]:
        """Calculate order prices using simple spread model."""

        if not self.is_data_sufficient(symbol, external_data):
            return None

        ref_data = external_data.get_data(symbol, self.reference_provider)
        if not ref_data:
            return None

        fair_value = ref_data.mid
        spread_amount = ref_data.spread
        bid_price = (fair_value - spread_amount / 2,)
        ask_price = (fair_value + spread_amount / 2,)

        return self.get_order_prices(symbol, bid_price, ask_price, 0.8)
