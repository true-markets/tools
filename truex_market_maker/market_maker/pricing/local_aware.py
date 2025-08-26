"""Local market aware pricing model."""

import statistics
import time
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import OrderPricingResult, PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("local_aware_model")


class LocalMarketAwareModel(PricingModel):
    """Model that considers local market conditions."""

    def __init__(
        self,
        external_weight: float = 0.7,
        local_weight: float = 0.3,
        base_spread_bps: float = 40,
    ):
        super().__init__("local_aware")
        self.external_weight = external_weight
        self.local_weight = local_weight
        self.base_spread_bps = base_spread_bps

        # Ensure weights sum to 1
        total_weight = self.external_weight + self.local_weight
        if total_weight != 1.0:
            self.external_weight /= total_weight
            self.local_weight /= total_weight

    def get_required_providers(self) -> List[str]:
        return []  # Can work with any external providers

    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price considering both external and local market data."""
        # Need both external and local data
        if not self.is_data_sufficient(symbol, external_data):
            return None

        if not local_ticker or "mid" not in local_ticker:
            return None

        # Get external consensus
        all_external = external_data.get_all_data(symbol)
        if not all_external:
            return None

        external_mids = []
        external_spreads = []

        for provider_name, data in all_external.items():
            if external_data.providers[provider_name].is_data_fresh(symbol):
                external_mids.append(data.mid)
                external_spreads.append(data.spread_pct)

        if not external_mids:
            return None

        external_mid = statistics.median(external_mids)
        local_mid = local_ticker["mid"]

        # Weighted fair value
        fair_value = self.external_weight * external_mid + self.local_weight * local_mid

        # Adjust confidence based on external/local agreement
        price_divergence = abs(external_mid - local_mid) / external_mid
        confidence = max(
            0.2, 1.0 - price_divergence * 3
        )  # Higher divergence = lower confidence

        # Adjust spread based on market conditions
        market_spread_pct = local_ticker.get("spread_pct", 0.001)
        market_spread_bps = market_spread_pct * 10000

        # Use wider of base spread or market spread + buffer
        final_spread_bps = max(self.base_spread_bps, market_spread_bps * 1.2)

        spread_amount = fair_value * (final_spread_bps / 10000)

        return PricingResult(
            symbol=symbol,
            fair_value=fair_value,
            bid_price=fair_value - spread_amount / 2,
            ask_price=fair_value + spread_amount / 2,
            confidence=confidence,
            spread_bps=final_spread_bps,
            timestamp=time.time(),
            model_name=self.name,
            metadata={
                "external_mid": external_mid,
                "local_mid": local_mid,
                "price_divergence": price_divergence,
                "external_weight": self.external_weight,
                "local_weight": self.local_weight,
                "market_spread_bps": market_spread_bps,
            },
        )

    def calculate_order_prices(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[OrderPricingResult]:
        result = self.calculate_price(symbol, external_data, local_ticker)
        if result:
            return self.get_order_prices(
                symbol, result.bid_price, result.ask_price, result.confidence
            )

        return None
