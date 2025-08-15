"""Pricing model manager for selecting and managing multiple pricing models."""

from typing import Dict, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import PricingModel, PricingResult
from market_maker.utils import log

logger = log.setup_custom_logger("pricing_manager")


class PricingModelManager:
    """Manages multiple pricing models and selects the best one."""

    def __init__(self, external_data_manager: ExternalDataManager):
        self.external_data = external_data_manager
        self.models: Dict[str, PricingModel] = {}
        self.model_preferences = []  # Ordered list of preferred models
        self.last_results = {}  # symbol -> PricingResult

    def add_model(self, model: PricingModel, priority: int = 100) -> None:
        """Add a pricing model."""
        self.models[model.name] = model
        self.model_preferences.append((priority, model.name))
        self.model_preferences.sort()  # Sort by priority

    def remove_model(self, name: str) -> None:
        """Remove a pricing model."""
        if name in self.models:
            del self.models[name]
            self.model_preferences = [
                (p, n) for p, n in self.model_preferences if n != name
            ]

    def get_best_price(
        self, symbol: str, local_ticker: Dict
    ) -> Optional[PricingResult]:
        """Get the best price from available models."""
        best_result = None
        best_confidence = 0.0

        # Try models in order of preference
        for _priority, model_name in self.model_preferences:
            model = self.models[model_name]

            if not model.enabled:
                continue

            try:
                result = model.calculate_price(symbol, self.external_data, local_ticker)
                if result and result.confidence > best_confidence:
                    best_result = result
                    best_confidence = result.confidence

            except Exception as e:
                logger.error(f"Error in model {model_name} for {symbol}: {e}")

        if best_result:
            self.last_results[symbol] = best_result
            logger.info(
                f"Best price for {symbol}: {best_result.fair_value:.4f} "
                f"({best_result.bid_price:.4f}/{best_result.ask_price:.4f}) "
                f"from {best_result.model_name} (confidence: {best_result.confidence:.2f})"
            )

        return best_result

    def get_all_prices(
        self, symbol: str, local_ticker: Dict
    ) -> Dict[str, PricingResult]:
        """Get prices from all models."""
        results = {}

        for model_name, model in self.models.items():
            if not model.enabled:
                continue

            try:
                result = model.calculate_price(symbol, self.external_data, local_ticker)
                if result:
                    results[model_name] = result

            except Exception as e:
                logger.error(f"Error in model {model_name} for {symbol}: {e}")

        return results

    def set_model_enabled(self, model_name: str, enabled: bool) -> None:
        """Enable or disable a model."""
        if model_name in self.models:
            self.models[model_name].enabled = enabled
