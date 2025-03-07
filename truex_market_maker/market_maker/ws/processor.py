import threading
import asyncio
import websockets
import queue
import json
import time
import hmac
import hashlib
import base64

from market_maker.settings import settings
from market_maker.utils import log

logger = log.setup_custom_logger('root')

class WebsocketAuth(object):
    def __init__(self, apiKey, apiSecret):
        self.apiKey = apiKey
        self.apiSecret = apiSecret

    def __call__(self, request):
        timestamp = str(int(time.time()))
        message = timestamp + "TRUEXWS" + self.apiKey
        hmac_key = str.encode(self.apiSecret)
        signature = hmac.new(hmac_key, message.encode('utf-8'), digestmod=hashlib.sha256).digest()
        request["key"] = self.apiKey
        request["timestamp"] = timestamp
        request["signature"] = base64.b64encode(signature).decode('utf-8')
        return request

class TruexWebsocket(object):

    """TruexWebsocket is a websocket client that connects to the Truex server and listens for messages."""

    def __init__(self, queue=None):
        self.__reset()
        self.thread = None
        self.loop = None
        self.recv_task = None
        self.send_task = None
        self.send_queue = None
        self.queue = queue
        self.auth = WebsocketAuth(settings.API_KEY, settings.API_SECRET)

    def __reset(self):
        pass

    def exit(self):
        """Exit the websocket client."""
        logger.info("Shutting down websocket thread.")

    def Position(self, symbol):
        """Get your position."""
        if not "POSITION" in self.data:
            self.data["POSITION"] = {}

        positions = self.data["POSITION"]
        if symbol not in positions:
            # stub out a position if we don't have one
            positions[symbol] = {'qty': 0, 'executed_vwap': 0, 'entry_vwap': 0}

        return positions[symbol]

    def Ticker(self, symbol):
        """Get the ticker for a symbol."""
        if "EBBO" not in self.data:
            self.data["EBBO"] = {}

        ticker = {}
        tickers = self.data["EBBO"]
        ticker_info = {}
        if symbol not in tickers:
            if "INSTRUMENT" not in self.data:
                return False
            instrument = self.data["INSTRUMENT"]
            if symbol not in instrument:
                return None
            # stub out a ticker if we don't have one
            ref_px = instrument[symbol]['info']['reference_price']
            ticker['mid'] = ticker['buy'] = ticker['sell'] = ticker['last'] = float(ref_px)
        else:
            ticker_info = tickers[symbol]['info']
            if ticker_info['best_ask']['price'] == '0' or ticker_info['best_bid']['price'] == '0' \
                or ticker_info['best_bid']['price'] == '-170141183460469231731.687303715884105728' \
                or ticker_info['best_ask']['price'] == '170141183460469231731.687303715884105727':
                instrument = self.data["INSTRUMENT"]
                ref_px = instrument[symbol]['info']['reference_price']
                ticker['mid'] = ticker['buy'] = ticker['sell'] = ticker['last'] = float(ref_px)
            else:
                ticker = {
                    'mid': (float(ticker_info['best_bid']['price']) + float(ticker_info['best_ask']['price'])) // 2.,
                    'buy': float(ticker_info['best_bid']['price']),
                    'sell': float(ticker_info['best_ask']['price']),
                    'last': float(ticker_info['last_trade']['price'])
                }

        logger.info(f"Ticker info: {ticker_info}")
        logger.info(f"Ticker info: {ticker}")
        return ticker

    def IsMarketOpen(self, symbol):
        if not self.data:
            return False

        if "INSTRUMENT" not in self.data:
            return False

        symbols = self.data["INSTRUMENT"]
        if symbol not in symbols:
            logger.error(f"Unknown symbol: {symbol}")
            return False

        logger.info(f"{symbol} status: {symbols[symbol]['status']}")
        return symbols[symbol]["status"] == "ACTIVE"

    def GetInstrumentId(self, symbol):
        symbols = self.data["INSTRUMENT"]
        if symbol not in symbols:
            return None

        return symbols[symbol]["id"]

    def GetInstrumentBaseAsset(self, symbol):
        symbols = self.data["INSTRUMENT"]
        if symbol not in symbols:
            return None

        return symbols[symbol]["info"]["base_asset_id"]

    def GetInstrumentQuoteAsset(self, symbol):
        symbols = self.data["INSTRUMENT"]
        if symbol not in symbols:
            return None

        return symbols[symbol]["info"]["quote_asset_id"]

    def SubscribeToInstrument(self, symbol):
        """Subscribe to instrument's details/updates."""
        request = {
            "type": "SUBSCRIBE",
            "item_names": [
                symbol
            ],
            "channels": [
                "INSTRUMENT"
            ]
        }
        logger.info(f"Subscribing to {symbol}")
        asyncio.run_coroutine_threadsafe(self.send_queue.put(json.dumps(self.auth(request))), self.loop)


    def UnsubscribeFromInstrument(self, symbol):
        """Unsubscribe from instrument's details/updates."""
        request = {
            "type": "UNSUBSCRIBE",
            "item_names": [
                symbol
            ],
            "channels": [
                "INSTRUMENT"
            ]
        }
        asyncio.run_coroutine_threadsafe(self.send_queue.put(json.dumps(self.auth(request))), self.loop)

    def SubscribeToTicker(self, symbol):
        """Subscribe to ticker data."""
        request = {
            "type": "SUBSCRIBE",
            "item_names": [
                symbol
            ],
            "channels": [
                "EBBO",
                "TRADE"
            ]
        }
        logger.info(f"Subscribing to ticker {symbol}")
        asyncio.run_coroutine_threadsafe(self.send_queue.put(json.dumps(self.auth(request))), self.loop)

    def UnsubscribeFromTicker(self, symbol):
        """Unsubscribe from ticker data."""
        request = {
            "type": "UNSUBSCRIBE",
            "item_names": [
                symbol
            ],
            "channels": [
                "EBBO",
                "TRADE"
            ]
        }
        asyncio.run_coroutine_threadsafe(self.send_queue.put(json.dumps(self.auth(request))), self.loop)

    def Connect(self, url):
        """Asynchronous method to connect to the WebSocket server."""
        if self.thread and self.thread.is_alive():
            logger.warning("WebSocket is already running.")
            return

        self.event = threading.Event()
        self.thread = threading.Thread(target=self._run_loop, args=(url,), daemon=True)
        self.thread.start()
        # wait for all the various async stuff to get settled
        setup = self.event.wait(10)
        if not setup:
            raise Exception("Failed to setup websocket connection")
        logger.info("WebSocket connection started in the background.")

    def _run_loop(self, url):
        """Run the event loop in a background thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect_async(url))
        except Exception as e:
            logger.error(f"Error during WebSocket connection: {e}")
        finally:
            self.loop.close()

    async def _connect_async(self, url):
        """Connect to the websocket server."""
        logger.info(f"Connecting to {url}")
        try:
            async with websockets.connect(url) as ws:
                self.event.set()
                self.send_queue = asyncio.Queue()  # Initialize the asyncio.Queue in the same loop
                self.recv_task = asyncio.create_task(self.__ReceiveMessages(ws))
                self.send_task = asyncio.create_task(self.__SendMessages(ws))
                await asyncio.gather(self.recv_task, self.send_task)
        except Exception as e:
            logger.error(f"Error connecting to {url}: {e}")
            self.exit()

    async def __ReceiveMessages(self, ws):
        self.seqnum = {}
        self.data = {}
        self.symbol = {}
        """Receive messages from the websocket server."""
        while True:
            try:
                logger.info("Waiting to recieve message...")
                async for message in ws:
                    msg = json.loads(message)
                    logger.debug(f"Received message: {msg}")

                    chn = msg["channel"]
                    upd = msg["update"]

                    try:
                        if chn == "WEBSOCKET":
                            if upd == "WELCOME":
                                logger.info(f"Welcome message rcvd: {msg['message']} v{msg['version']} @ {msg['datetime']}.")
                            elif upd == "SNAPSHOT":
                                for subscription in msg["subscriptions"]:
                                    if subscription["channel"] == "INSTRUMENT":
                                        logger.info(f"Currently subscribed: {subscription['item_names']}.")

                        elif chn == "INSTRUMENT":
                            if chn not in self.data:
                                self.data[chn] = {}

                            if upd == "SNAPSHOT":
                                self.seqnum[chn] = msg["seqnum"]
                                self.data[chn][msg["data"]["info"]["symbol"]] = msg["data"]
                                self.symbol[msg["data"]["id"]] = msg["data"]["info"]["symbol"]
                                if self.queue:
                                    self.queue.put(msg["data"]["info"]["symbol"])
                            if upd == "UPDATE":
                                if msg["seqnum"]  != self.seqnum[chn]:
                                    gap = msg["seqnum"] - self.seqnum[chn]
                                    logger.error(f"Gap of {gap} msgs detected in {chn}")

                                self.data[chn][msg["data"]["info"]["symbol"]] = msg["data"]
                                self.seqnum[chn] = str(int(msg["seqnum"]) + 1)

                        elif chn == "EBBO":
                            if chn not in self.data:
                                self.data[chn] = {}

                            instrumentId = msg["data"]["id"]
                            if instrumentId not in self.symbol:
                                logger.error(f"Unknown instrument id: {instrumentId}")
                                return
                            symbol = self.symbol[instrumentId]

                            if upd == "SNAPSHOT":
                                self.seqnum[chn] = msg["seqnum"]
                                self.data[chn][symbol] = msg["data"]
                            if upd == "UPDATE":
                                if msg["seqnum"] != self.seqnum[chn]:
                                    gap = int(msg["seqnum"]) - int(self.seqnum[chn])
                                    logger.error(f"Gap of {gap} msgs detected in {chn}")

                                self.data[chn][symbol] = msg["data"]
                                self.seqnum[chn] = str(int(msg["seqnum"]) + 1)

                        elif chn == "TRADE":
                            if chn not in self.data:
                                self.data[chn] = {}
                                self.seqnum[chn] = 0

                            instrumentId = msg["data"]["id"]
                            if instrumentId not in self.symbol:
                                logger.error(f"Unknown instrument id: {instrumentId}")
                                return
                            symbol = self.symbol[instrumentId]
                            if msg["seqnum"]  != self.seqnum[chn]:
                                gap = int(msg["seqnum"]) - int(self.seqnum[chn])
                                logger.error(f"Gap of {gap} msgs detected in {chn}")

                            logger.info(f"Trade {symbol}: {msg}")
                            self.data[chn][symbol] = msg["data"]
                            self.seqnum[chn] = str(int(msg["seqnum"]) + 1)
                            if self.queue:
                                self.queue.put(symbol)

                        else:
                            raise Exception(f"Unknown channel: {chn}")
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        continue

            except asyncio.TimeoutError:
                logger.info("Timeout error.")
                continue

    async def __SendMessages(self, ws):
        """Send messages to the websocket server."""
        try:
            while True:
                try:
                    message = await self.send_queue.get()
                    await ws.send(message)
                except queue.Empty:
                    logger.info("Queue is empty.")
                    continue
        except websockets.exceptions.ConnectionClosedError:
            logger.info("Connection closed.")
            self.exit()
        except Exception as e:
            logger.error(f"Error: {e}")
            self.exit()
