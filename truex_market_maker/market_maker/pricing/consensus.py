"""Consensus pricing model that aggregates multiple external sources."""

import statistics
import time
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("consensus_model")


class ConsensusModel(PricingModel):
    """Model that takes consensus across multiple providers."""

    def __init__(
        self,
        min_providers: int = 2,
        outlier_threshold: float = 0.02,
        base_spread_bps: float = 30,
    ):
        super().__init__("consensus")
        self.min_providers = min_providers
        self.outlier_threshold = outlier_threshold  # 2% outlier threshold
        self.base_spread_bps = base_spread_bps

    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate consensus price across providers."""
        all_data = external_data.get_all_data(symbol)

        if len(all_data) < self.min_providers:
            return None

        # Filter fresh data
        fresh_data = []
        for provider_name, data in all_data.items():
            if external_data.providers[provider_name].is_data_fresh(symbol):
                fresh_data.append(data)

        if len(fresh_data) < self.min_providers:
            return None

        # Calculate consensus mid price
        mid_prices = [data.mid for data in fresh_data]
        spreads = [data.spread_pct for data in fresh_data]

        # Remove outliers
        filtered_mids = self._remove_outliers(mid_prices)

        if len(filtered_mids) < self.min_providers:
            # Fall back to all data if too many outliers
            filtered_mids = mid_prices

        # Calculate consensus
        fair_value = statistics.median(filtered_mids)
        avg_spread_pct = statistics.mean(spreads) if spreads else 0.5

        # Adjust spread based on consensus quality
        confidence = min(len(filtered_mids) / len(fresh_data), 1.0)
        spread_adjustment = (
            1.0 - confidence
        ) * 20  # Add up to 20bps for low confidence

        final_spread_bps = self.base_spread_bps + spread_adjustment
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
                "num_providers": len(fresh_data),
                "num_after_outlier_removal": len(filtered_mids),
                "provider_mids": {data.source: data.mid for data in fresh_data},
                "avg_external_spread_pct": avg_spread_pct,
            },
        )

    def get_required_providers(self) -> List[str]:
        return []  # Can work with any providers

    def _remove_outliers(self, values: List[float]) -> List[float]:
        """Remove outliers from a list of values."""
        if len(values) < 3:
            return values

        median_val = statistics.median(values)
        filtered = []

        for val in values:
            deviation = abs(val - median_val) / median_val
            if deviation <= self.outlier_threshold:
                filtered.append(val)

        return filtered if filtered else values
