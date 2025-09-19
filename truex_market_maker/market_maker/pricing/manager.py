"""Pricing model manager for selecting and managing multiple pricing models."""

from typing import Dict, Optional

from market_maker.external_data.base import ExternalDataManager
from market_maker.pricing.base import OrderPricingResult, PricingModel, PricingResult
from market_maker.utils import log

from .consensus import ConsensusModel
from .external import ExternalDataModel
from .local_aware import LocalMarketAwareModel
from .momentum import MomentumModel
from .simple_spread import SimpleSpreadModel

logger = log.setup_custom_logger("pricing_manager")


class PricingModelManager:
    """Manages multiple pricing models and selects the best one."""

    def __init__(self, external_data_manager: ExternalDataManager):
        self.external_data = external_data_manager
        self.models: Dict[str, PricingModel] = {}
        self.model_preferences = []  # Ordered list of preferred models
        self.last_results = {}  # symbol -> PricingResult

    def setup_pricing_models(self, models_config: dict) -> None:
        for model_name, config in models_config.items():
            if not config.get("enabled", False):
                continue

            priority = config.get("priority", 100)

            if model_name == "simple_spread":
                model = SimpleSpreadModel(
                    spread_bps=config.get("spread_bps", 50),
                    reference_provider=config.get("reference_provider", "coinbase"),
                )
            elif model_name == "consensus":
                model = ConsensusModel(
                    min_providers=config.get("min_providers", 2),
                    outlier_threshold=config.get("outlier_threshold", 0.02),
                    base_spread_bps=config.get("base_spread_bps", 30),
                )
            elif model_name == "local_aware":
                model = LocalMarketAwareModel(
                    external_weight=config.get("external_weight", 0.7),
                    local_weight=config.get("local_weight", 0.3),
                    base_spread_bps=config.get("base_spread_bps", 40),
                )
            elif model_name == "momentum":
                model = MomentumModel(
                    lookback_minutes=config.get("lookback_minutes", 15),
                    momentum_factor=config.get("momentum_factor", 0.1),
                    base_spread_bps=config.get("base_spread_bps", 35),
                )
            elif model_name == "external":
                model = ExternalDataModel(
                    external_provider=config.get("external_provider", "coinbase"),
                )
            else:
                logger.warning(f"Unknown pricing model: {model_name}")
                continue

            self.add_model(model, priority)
            logger.info(f"Added {model_name} pricing model (priority: {priority})")

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

    def get_symbol_pricing(
        self, symbol: str, local_ticker: Dict
    ) -> Optional[OrderPricingResult]:
        """Get level prices from available models."""
        best_result = None
        best_confidence = 0.0

        # Try models in order of preference
        for _priority, model_name in self.model_preferences:
            model = self.models[model_name]

            if not model.enabled:
                continue

            try:
                result = model.calculate_order_prices(
                    symbol, self.external_data, local_ticker
                )
                if result and result.confidence > best_confidence:
                    best_result = result
                    best_confidence = result.confidence

            except Exception as e:
                logger.error(f"Error in model {model_name} for {symbol}: {e}")

        if best_result:
            self.last_results[symbol] = best_result
            logger.info(
                f"Best price for {symbol} "
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
