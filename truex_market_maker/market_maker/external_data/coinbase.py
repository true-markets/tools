"""
Coinbase Exchange (formerly Pro) public data provider.

NO API KEYS OR AUTHENTICATION REQUIRED!

This module uses only public endpoints from the Coinbase Exchange API:
- REST: https://api.exchange.coinbase.com
- WebSocket: wss://ws-feed.exchange.coinbase.com

All endpoints used are public market data endpoints that don't require authentication.
"""

import asyncio
import json
from typing import Any, Dict, Optional

import aiohttp
import websockets

from market_maker.external_data.base import ExternalDataProvider, MarketData
from market_maker.utils import log

logger = log.setup_custom_logger("coinbase_data")


class CoinbaseProvider(ExternalDataProvider):
    """Coinbase Exchange (formerly Pro) public data provider - NO AUTH REQUIRED."""

    def __init__(self):
        super().__init__("coinbase")
        # Use public Coinbase Exchange API - no authentication required
        self.ws_url = "wss://ws-feed.exchange.coinbase.com"
        self.rest_url = "https://api.exchange.coinbase.com"
        self.websocket = None
        self.session = None
        self.subscribed_symbols = set()
        self.symbol_mapping = {}  # Maps our symbols to Coinbase symbols
        self._setup_symbol_mapping()

    def _setup_symbol_mapping(self):
        """Set up mapping between our symbols and Coinbase symbols."""
        # Map TrueX symbols to Coinbase symbols
        self.symbol_mapping = {
            "BTC-PYUSD": "BTC-USD",  # Approximate - PYUSD might not be available
            "ETH-PYUSD": "ETH-USD",  # Approximate - PYUSD might not be available
            "BTC-USD": "BTC-USD",
            "ETH-USD": "ETH-USD",
            "BTC-USDC": "BTC-USDC",
            "ETH-USDC": "ETH-USDC",
        }

    def _get_coinbase_symbol(self, symbol: str) -> str:
        """Convert our symbol to Coinbase symbol."""
        return self.symbol_mapping.get(symbol, symbol)

    async def connect(self) -> bool:
        """Connect to Coinbase websocket."""
        try:
            self.session = aiohttp.ClientSession()
            self.websocket = await websockets.connect(self.ws_url)
            self.connected = True

            # Start message handling task
            asyncio.create_task(self._handle_messages())

            logger.info("Connected to Coinbase websocket")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Coinbase: {e}")
            self.connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Coinbase."""
        self.connected = False

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        if self.session:
            await self.session.close()
            self.session = None

        logger.info("Disconnected from Coinbase websocket")

    async def subscribe(self, symbol: str) -> bool:
        """Subscribe to market data for a symbol."""
        if not self.connected or not self.websocket:
            logger.error("Not connected to Coinbase")
            return False

        coinbase_symbol = self._get_coinbase_symbol(symbol)

        # Subscribe to ticker channel
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": [coinbase_symbol],
            "channels": ["ticker", "level2_batch"],
        }

        try:
            await self.websocket.send(json.dumps(subscribe_msg))
            self.subscribed_symbols.add(symbol)
            logger.info(f"Subscribed to {symbol} ({coinbase_symbol}) on Coinbase")
            return True

        except Exception as e:
            logger.error(f"Failed to subscribe to {symbol}: {e}")
            return False

    async def unsubscribe(self, symbol: str) -> bool:
        """Unsubscribe from market data for a symbol."""
        if not self.connected or not self.websocket:
            return True

        coinbase_symbol = self._get_coinbase_symbol(symbol)

        unsubscribe_msg = {
            "type": "unsubscribe",
            "product_ids": [coinbase_symbol],
            "channels": ["ticker", "level2_batch"],
        }

        try:
            await self.websocket.send(json.dumps(unsubscribe_msg))
            self.subscribed_symbols.discard(symbol)
            logger.info(f"Unsubscribed from {symbol} on Coinbase")
            return True

        except Exception as e:
            logger.error(f"Failed to unsubscribe from {symbol}: {e}")
            return False

    async def get_current_data(self, symbol: str) -> Optional[MarketData]:
        """Get current market data via public REST API - NO AUTH REQUIRED."""
        if not self.session:
            return None

        coinbase_symbol = self._get_coinbase_symbol(symbol)

        try:
            # Use public Coinbase Exchange API - no authentication required
            ticker_url = f"{self.rest_url}/products/{coinbase_symbol}/ticker"

            async with self.session.get(ticker_url) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_public_ticker_data(symbol, data)
                else:
                    logger.error(
                        f"Failed to get ticker for {symbol}: {response.status}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Error getting current data for {symbol}: {e}")
            return None

    async def _handle_messages(self):
        """Handle incoming websocket messages."""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("Coinbase websocket connection closed")
            self.connected = False
        except Exception as e:
            logger.error(f"Error in message handler: {e}")
            self.connected = False

    async def _process_message(self, data: Dict[str, Any]):
        """Process a websocket message."""
        msg_type = data.get("type")

        if msg_type == "ticker":
            await self._handle_ticker(data)
        elif msg_type == "l2update":
            await self._handle_l2_update(data)
        elif msg_type == "subscriptions":
            logger.info(f"Coinbase subscription confirmed: {data}")
        elif msg_type == "error":
            logger.error(f"Coinbase error: {data}")

    async def _handle_ticker(self, data: Dict[str, Any]):
        """Handle ticker updates."""
        try:
            coinbase_symbol = data.get("product_id")
            if not coinbase_symbol:
                return

            # Find our symbol from Coinbase symbol
            our_symbol = None
            for our_sym, cb_sym in self.symbol_mapping.items():
                if cb_sym == coinbase_symbol:
                    our_symbol = our_sym
                    break

            if not our_symbol:
                return

            # Parse ticker data
            market_data = MarketData(
                symbol=our_symbol,
                bid=float(data.get("best_bid", 0)),
                ask=float(data.get("best_ask", 0)),
                last=float(data.get("price", 0)),
                volume=float(data.get("volume_24h", 0)),
                timestamp=float(data.get("time", 0)),
                source=self.name,
            )

            self.last_update[our_symbol] = market_data.timestamp
            self._notify_callbacks(market_data)

        except Exception as e:
            logger.error(f"Error handling ticker: {e}")

    async def _handle_l2_update(self, data: Dict[str, Any]):
        """Handle level 2 order book updates."""
        # For now, we'll focus on ticker data
        # This could be implemented for more detailed book analysis
        pass

    def _parse_public_ticker_data(
        self, symbol: str, data: Dict[str, Any]
    ) -> MarketData:
        """Parse ticker data from public Coinbase Exchange API."""
        import time

        return MarketData(
            symbol=symbol,
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            last=float(data.get("price", 0)),
            volume=float(data.get("volume", 0)),
            timestamp=time.time(),  # Use current time since public API doesn't always include timestamp
            source=self.name,
        )


class CoinbaseRESTProvider(ExternalDataProvider):
    """Coinbase Exchange public REST provider - NO AUTH REQUIRED."""

    def __init__(self, poll_interval: int = 10):
        super().__init__("coinbase_rest")
        # Use public Coinbase Exchange API - no authentication required
        self.rest_url = "https://api.exchange.coinbase.com"
        self.session = None
        self.poll_interval = poll_interval
        self.polling_tasks = {}
        self.symbol_mapping = {
            "BTC-PYUSD": "BTC-USD",
            "ETH-PYUSD": "ETH-USD",
            "BTC-USD": "BTC-USD",
            "ETH-USD": "ETH-USD",
            "BTC-USDC": "BTC-USDC",
            "ETH-USDC": "ETH-USDC",
        }

    def _get_coinbase_symbol(self, symbol: str) -> str:
        """Convert our symbol to Coinbase symbol."""
        return self.symbol_mapping.get(symbol, symbol)

    async def connect(self) -> bool:
        """Connect (create session)."""
        try:
            self.session = aiohttp.ClientSession()
            self.connected = True
            logger.info("Connected to Coinbase REST API")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Coinbase REST: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect and stop all polling."""
        self.connected = False

        # Stop all polling tasks and wait for them to be cancelled
        if self.polling_tasks:
            logger.info(f"Cancelling {len(self.polling_tasks)} polling tasks...")
            tasks_to_cancel = list(self.polling_tasks.values())

            # Cancel all tasks
            for task in tasks_to_cancel:
                if not task.cancelled():
                    task.cancel()

            # Wait for cancellation with timeout to avoid hanging
            if tasks_to_cancel:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for polling tasks to cancel")
                except Exception as e:
                    # This is expected for cancelled tasks, just log debug info
                    logger.debug(f"Expected exception during task cancellation: {e}")

            self.polling_tasks.clear()
            logger.info("All polling tasks cancelled")

        if self.session:
            await self.session.close()
            self.session = None

        logger.info("Disconnected from Coinbase REST")

    async def subscribe(self, symbol: str) -> bool:
        """Start polling for a symbol."""
        if not self.connected:
            return False

        if symbol in self.polling_tasks:
            return True  # Already subscribed

        # Start polling task
        task = asyncio.create_task(self._poll_symbol(symbol))
        self.polling_tasks[symbol] = task

        logger.info(f"Started polling {symbol} on Coinbase REST")
        return True

    async def unsubscribe(self, symbol: str) -> bool:
        """Stop polling for a symbol."""
        if symbol in self.polling_tasks:
            self.polling_tasks[symbol].cancel()
            del self.polling_tasks[symbol]
            logger.info(f"Stopped polling {symbol} on Coinbase REST")
        return True

    async def get_current_data(self, symbol: str) -> Optional[MarketData]:
        """Get current market data from public API - NO AUTH REQUIRED."""
        if not self.session:
            return None

        coinbase_symbol = self._get_coinbase_symbol(symbol)

        try:
            # Use public Coinbase Exchange API ticker endpoint
            url = f"{self.rest_url}/products/{coinbase_symbol}/ticker"

            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_public_ticker_data(symbol, data)
                else:
                    logger.error(
                        f"Failed to get ticker for {symbol}: HTTP {response.status}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Error getting data for {symbol}: {e}")
            return None

    async def _poll_symbol(self, symbol: str):
        """Poll for market data for a symbol."""
        while self.connected:
            try:
                data = await self.get_current_data(symbol)
                if data:
                    self.last_update[symbol] = data.timestamp
                    self._notify_callbacks(data)

                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling {symbol}: {e}")
                await asyncio.sleep(self.poll_interval)

    def _parse_public_ticker_data(
        self, symbol: str, data: Dict[str, Any]
    ) -> MarketData:
        """Parse ticker data from public Coinbase Exchange API."""
        import time

        return MarketData(
            symbol=symbol,
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            last=float(data.get("price", 0)),
            volume=float(data.get("volume", 0)),
            timestamp=time.time(),  # Use current time
            source=self.name,
        )
