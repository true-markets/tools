"""Base classes for external market data providers."""

import abc
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from market_maker.utils import log

logger = log.setup_custom_logger("external_data")


@dataclass
class MarketData:
    """Market data from an external source."""

    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: float
    source: str

    @property
    def mid(self) -> float:
        """Calculate the mid price."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """Calculate the spread."""
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        """Calculate the spread percentage."""
        return (self.spread / self.mid) * 100 if self.mid > 0 else 0


class ExternalDataProvider(abc.ABC):
    """Base class for external market data providers."""

    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.last_update = {}
        self.callbacks: List[Callable[[MarketData], None]] = []

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Connect to the data provider."""
        pass

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the data provider."""
        pass

    @abc.abstractmethod
    async def subscribe(self, symbol: str) -> bool:
        """Subscribe to market data for a symbol."""
        pass

    @abc.abstractmethod
    async def unsubscribe(self, symbol: str) -> bool:
        """Unsubscribe from market data for a symbol."""
        pass

    @abc.abstractmethod
    async def get_current_data(self, symbol: str) -> Optional[MarketData]:
        """Get current market data for a symbol."""
        pass

    def add_callback(self, callback: Callable[[MarketData], None]) -> None:
        """Add a callback for market data updates."""
        self.callbacks.append(callback)

    def remove_callback(self, callback: Callable[[MarketData], None]) -> None:
        """Remove a callback."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def _notify_callbacks(self, data: MarketData) -> None:
        """Notify all callbacks of new market data."""
        for callback in self.callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in callback for {data.symbol}: {e}")

    def is_data_fresh(self, symbol: str, max_age_seconds: int = 30) -> bool:
        """Check if data for a symbol is fresh."""
        if symbol not in self.last_update:
            return False
        return time.time() - self.last_update[symbol] < max_age_seconds


class ExternalDataManager:
    """Manages multiple external data providers."""

    def __init__(self):
        self.providers: Dict[str, ExternalDataProvider] = {}
        self.market_data: Dict[str, Dict[str, MarketData]] = (
            {}
        )  # symbol -> provider -> data
        self.callbacks: List[Callable[[MarketData], None]] = []

    def add_provider(self, provider: ExternalDataProvider) -> None:
        """Add a data provider."""
        self.providers[provider.name] = provider
        provider.add_callback(self._on_market_data)

    def remove_provider(self, name: str) -> None:
        """Remove a data provider."""
        if name in self.providers:
            provider = self.providers[name]
            provider.remove_callback(self._on_market_data)
            del self.providers[name]

    async def connect_all(self) -> Dict[str, bool]:
        """Connect to all providers."""
        results = {}
        for name, provider in self.providers.items():
            try:
                results[name] = await provider.connect()
                if results[name]:
                    logger.info(f"Connected to {name}")
                else:
                    logger.error(f"Failed to connect to {name}")
            except Exception as e:
                logger.error(f"Error connecting to {name}: {e}")
                results[name] = False
        return results

    async def disconnect_all(self) -> None:
        """Disconnect from all providers."""
        for name, provider in self.providers.items():
            try:
                await provider.disconnect()
                logger.info(f"Disconnected from {name}")
            except Exception as e:
                logger.error(f"Error disconnecting from {name}: {e}")

    async def subscribe_all(self, symbol: str) -> Dict[str, bool]:
        """Subscribe to a symbol on all providers."""
        results = {}
        for name, provider in self.providers.items():
            try:
                results[name] = await provider.subscribe(symbol)
                if results[name]:
                    logger.info(f"Subscribed to {symbol} on {name}")
                else:
                    logger.warning(f"Failed to subscribe to {symbol} on {name}")
            except Exception as e:
                logger.error(f"Error subscribing to {symbol} on {name}: {e}")
                results[name] = False
        return results

    def get_all_data(self, symbol: str) -> Dict[str, MarketData]:
        """Get market data for a symbol from all providers."""
        return self.market_data.get(symbol, {})

    def get_data(self, symbol: str, provider: str) -> Optional[MarketData]:
        """Get market data for a symbol from a specific provider."""
        return self.market_data.get(symbol, {}).get(provider)

    def has_data(self, symbol: str, provider: str = None) -> bool:
        """Check if we have data for a symbol (optionally from specific provider)."""
        if provider is None:
            # Check if we have any data for the symbol
            return bool(self.market_data.get(symbol, {}))
        else:
            # Check if we have data from specific provider
            return provider in self.market_data.get(symbol, {})

    def get_best_bid_ask(self, symbol: str) -> Optional[Dict[str, float]]:
        """Get the best bid/ask across all providers."""
        data = self.get_all_data(symbol)
        if not data:
            return None

        best_bid = max(d.bid for d in data.values() if d.bid > 0)
        best_ask = min(d.ask for d in data.values() if d.ask > 0)

        return {
            "bid": best_bid,
            "ask": best_ask,
            "mid": (best_bid + best_ask) / 2.0,
            "spread": best_ask - best_bid,
        }

    def add_callback(self, callback: Callable[[MarketData], None]) -> None:
        """Add a callback for market data updates."""
        self.callbacks.append(callback)

    def _on_market_data(self, data: MarketData) -> None:
        """Handle market data updates from providers."""
        if data.symbol not in self.market_data:
            self.market_data[data.symbol] = {}

        self.market_data[data.symbol][data.source] = data

        # Notify callbacks
        for callback in self.callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in callback for {data.symbol}: {e}")
