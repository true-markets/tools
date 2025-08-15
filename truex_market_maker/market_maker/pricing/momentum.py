"""Momentum-based pricing model."""

import statistics
import time
from typing import Dict, List, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("momentum_model")


class MomentumModel(PricingModel):
    """Model that considers price momentum and volatility."""

    def __init__(
        self,
        lookback_minutes: int = 15,
        momentum_factor: float = 0.1,
        base_spread_bps: float = 35,
    ):
        super().__init__("momentum")
        self.lookback_minutes = lookback_minutes
        self.momentum_factor = momentum_factor
        self.base_spread_bps = base_spread_bps
        self.price_history = {}  # symbol -> list of (timestamp, price) tuples

    def calculate_price(
        self, symbol: str, external_data: ExternalDataManager, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Calculate price with momentum adjustment."""
        # Get current external data
        best_external = external_data.get_best_bid_ask(symbol)
        if not best_external:
            return None

        current_price = best_external["mid"]
        current_time = time.time()

        # Update price history
        if symbol not in self.price_history:
            self.price_history[symbol] = []

        self.price_history[symbol].append((current_time, current_price))

        # Clean old history
        cutoff_time = current_time - (self.lookback_minutes * 60)
        self.price_history[symbol] = [
            (t, p) for t, p in self.price_history[symbol] if t > cutoff_time
        ]

        # Calculate momentum
        momentum = 0.0
        if len(self.price_history[symbol]) >= 2:
            prices = [p for t, p in self.price_history[symbol]]
            old_price = prices[0]
            momentum = (current_price - old_price) / old_price

        # Adjust fair value for momentum
        momentum_adjustment = momentum * self.momentum_factor
        fair_value = current_price * (1 + momentum_adjustment)

        # Adjust spread based on volatility
        volatility = 0.0
        if len(self.price_history[symbol]) >= 3:
            prices = [p for t, p in self.price_history[symbol]]
            price_changes = [
                abs(prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
            ]
            volatility = statistics.mean(price_changes) if price_changes else 0

        volatility_spread_bps = volatility * 5000  # Up to 50bps for 1% volatility
        final_spread_bps = self.base_spread_bps + volatility_spread_bps

        spread_amount = fair_value * (final_spread_bps / 10000)

        # Confidence decreases with high momentum and volatility
        confidence = max(0.3, 1.0 - abs(momentum) * 5 - volatility * 10)

        return PricingResult(
            symbol=symbol,
            fair_value=fair_value,
            bid_price=fair_value - spread_amount / 2,
            ask_price=fair_value + spread_amount / 2,
            confidence=confidence,
            spread_bps=final_spread_bps,
            timestamp=current_time,
            model_name=self.name,
            metadata={
                "momentum": momentum,
                "volatility": volatility,
                "momentum_adjustment": momentum_adjustment,
                "volatility_spread_bps": volatility_spread_bps,
                "history_points": len(self.price_history[symbol]),
            },
        )

    def get_required_providers(self) -> List[str]:
        return []  # Can work with any providers
