#!/usr/bin/env python3
"""
truex_depth.py.py

Connects to TrueX WebSocket and maintains a market data book
from the DEPTH channel, printing it at specified intervals.

Usage:
  python truex_depth.py.py                                      # defaults to BTC-PYUSD, 5s interval
  python truex_depth.py.py --products BTC-PYUSD ETH-PYUSD       # multiple products
  python truex_depth.py.py --interval 10                        # 10 second print interval
  python truex_depth.py.py --products SOL-PYUSD --interval 2    # custom products and interval

Requires: Python 3.8+, `websockets` (pip install websockets)
"""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import time
import websockets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional


class PriceLevel:
    def __init__(self, price: str, qty: str, depth: str):
        self.price = Decimal(price)
        self.qty = Decimal(qty) 
        self.depth = int(depth)
        self.price_str = price
        self.qty_str = qty

class OrderBook:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: Dict[Decimal, PriceLevel] = {}  # price -> PriceLevel
        self.asks: Dict[Decimal, PriceLevel] = {}  # price -> PriceLevel
        self.sequence_num = 0
        self.last_update = time.time()

    def handle_snapshot(self, data: dict):
        """Handle SNAPSHOT message - initialize the book"""
        self.bids.clear()
        self.asks.clear()
        
        # Process bids
        for bid_data in data.get("bids", []):
            price_level = PriceLevel(bid_data["price"], bid_data["qty"], bid_data["depth"])
            if price_level.qty > 0:
                self.bids[price_level.price] = price_level
                
        # Process asks
        for ask_data in data.get("asks", []):
            price_level = PriceLevel(ask_data["price"], ask_data["qty"], ask_data["depth"])
            if price_level.qty > 0:
                self.asks[price_level.price] = price_level
                
        self.last_update = time.time()

    def handle_update(self, data: dict):
        """Handle UPDATE message - modify existing book"""
        # Process bid updates
        for bid_data in data.get("bids", []):
            price_level = PriceLevel(bid_data["price"], bid_data["qty"], bid_data["depth"])
            if price_level.qty == 0:
                # Remove the price level
                self.bids.pop(price_level.price, None)
            else:
                # Add or update the price level
                self.bids[price_level.price] = price_level
                
        # Process ask updates  
        for ask_data in data.get("asks", []):
            price_level = PriceLevel(ask_data["price"], ask_data["qty"], ask_data["depth"])
            if price_level.qty == 0:
                # Remove the price level
                self.asks.pop(price_level.price, None)
            else:
                # Add or update the price level
                self.asks[price_level.price] = price_level
                
        self.last_update = time.time()

    def get_sorted_bids(self, limit: int = 10) -> List[PriceLevel]:
        """Get bids sorted in descending order (highest price first)"""
        return sorted(self.bids.values(), key=lambda x: x.price, reverse=True)[:limit]

    def get_sorted_asks(self, limit: int = 10) -> List[PriceLevel]:
        """Get asks sorted in ascending order (lowest price first)"""
        return sorted(self.asks.values(), key=lambda x: x.price)[:limit]

    def print_book(self):
        """Print the current order book with bids and asks side by side"""
        print(f"\n=== ORDER BOOK: {self.symbol} ===")
        print(f"Last Update: {datetime.fromtimestamp(self.last_update).strftime('%H:%M:%S')}")
        print(f"Sequence: {self.sequence_num}")
        
        # Get top levels
        bids = self.get_sorted_bids(10)
        asks = self.get_sorted_asks(10)
        
        # Calculate spread
        spread_text = ""
        if bids and asks:
            spread = asks[0].price - bids[0].price
            spread_text = f" | Spread: {spread}"
        
        print(f"\n{'BID SIDE':<34} | {'ASK SIDE':<35}{spread_text}")
        print(f"{'Price':<12} {'Qty':<12} {'Depth':<8} | {'Price':<12} {'Qty':<12} {'Depth':<8}")
        print("-" * 80)
        
        # Print side by side
        max_levels = max(len(bids), len(asks))
        
        for i in range(max_levels):
            # Format bid side
            if i < len(bids):
                bid = bids[i]
                bid_line = f"{bid.price_str:<12} {bid.qty_str:<12} {bid.depth:<8}"
            else:
                bid_line = f"{'':>32}"
            
            # Format ask side  
            if i < len(asks):
                ask = asks[i]
                ask_line = f"{ask.price_str:<12} {ask.qty_str:<12} {ask.depth:<8}"
            else:
                ask_line = f"{'':>32}"
                
            print(f"{bid_line} | {ask_line}")
            
        print("=" * 80)

def now():
    now_utc = datetime.now(timezone.utc)
    return str(int(now_utc.timestamp()))

def make_subscribe(channel: str, product_ids=None):
    msg = {
        "type": "SUBSCRIBE_NO_AUTH",
        "channels": [channel],
    }
    if product_ids:
        msg["item_names"] = product_ids
    msg["timestamp"] =  now()
    return msg

def make_unsubscribe(channel: str, product_ids=None):
    msg = {
        "type": "UNSUBSCRIBE_NO_AUTH",
        "channel": channel,
    }
    if product_ids:
        msg["item_names"] = product_ids
    msg["timestamp"] =  now()
    return msg

async def print_books_periodically(books: Dict[str, OrderBook], interval: float):
    """Print all order books at specified interval"""
    while True:
        await asyncio.sleep(interval)
        for book in books.values():
            book.print_book()

async def run(server, products, print_interval: float, stop_event: asyncio.Event):
    backoff = 1
    books: Dict[str, OrderBook] = {}
    
    while not stop_event.is_set():
        try:
            async with websockets.connect(server, ping_interval=20, ping_timeout=20) as ws:
                # Subscribe to DEPTH for the requested products
                await ws.send(json.dumps(make_subscribe("DEPTH", products)))
                
                # Start the periodic printing task
                print_task = asyncio.create_task(print_books_periodically(books, print_interval))

                try:
                    async for raw in ws:
                        try:
                            # Parse the JSON message
                            message = json.loads(raw)
                            
                            # Handle DEPTH channel messages
                            if message.get("channel") == "DEPTH":
                                symbol = message.get("data", {}).get("symbol")
                                update_type = message.get("update")
                                seqnum = message.get("seqnum")
                                
                                # Create book if it doesn't exist
                                if symbol not in books:
                                    books[symbol] = OrderBook(symbol)
                                
                                book = books[symbol]
                                book.sequence_num = int(seqnum) if seqnum else book.sequence_num
                                
                                # Handle different update types
                                if update_type == "SNAPSHOT":
                                    book.handle_snapshot(message["data"])
                                    print(f"# Received SNAPSHOT for {symbol}, seq {seqnum}")
                                elif update_type == "UPDATE":
                                    book.handle_update(message["data"])
                            else:
                                # Print non-DEPTH messages (like subscription confirmations)
                                print(f"# {raw}")
                                
                        except json.JSONDecodeError:
                            print(f"# Invalid JSON: {raw}", file=sys.stderr)
                        except KeyError as e:
                            print(f"# Missing key in message: {e}, raw: {raw}", file=sys.stderr)
                        except Exception as e:
                            print(f"# Error processing message: {e}, raw: {raw}", file=sys.stderr)
                            
                except asyncio.CancelledError:
                    print_task.cancel()
                    raise
                finally:
                    if not print_task.cancelled():
                        print_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await print_task
                        
        except asyncio.CancelledError:
            # Graceful shutdown
            raise
        except (websockets.ConnectionClosed, OSError) as e:
            if stop_event.is_set():
                break
            print(f"# disconnected: {e}; reconnecting in {backoff}s...", file=sys.stderr, flush=True)
            # Wait for either stop or timeout (no CancelledError traceback from sleep)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)
        except Exception as e:
            if stop_event.is_set():
                break
            print(f"# error: {e}", file=sys.stderr, flush=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)

def parse_args():
    parser = argparse.ArgumentParser(
        description='Coinbase order book WebSocket client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                                    # BTC-PYUSD with 5s interval
  %(prog)s --products BTC-USD ETH-USD         # Multiple products
  %(prog)s --interval 10                      # 10 second intervals
  %(prog)s --products SOL-USD --interval 2    # Custom products and interval
        '''
    )
    
    parser.add_argument(
        '--products', '-p',
        nargs='*',
        default=['BTC-PYUSD'],
        help='Product IDs to subscribe to (default: BTC-PYUSD)'
    )
    
    parser.add_argument(
        '--interval', '-i',
        type=float,
        default=5.0,
        help='Print interval in seconds (default: 5.0)'
    )

    parser.add_argument(
        '--server', '-s',
        type=str,
        default='uat1.truex.co:4444',
        help='The host and port of the WebSocket Server'
    )
    
    return parser.parse_args()

async def amain(args):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    ws_url = f"ws://{args.server}/v1/api"
    task = asyncio.create_task(run(ws_url, args.products, args.interval, stop_event))
    try:
        await asyncio.wait([task, asyncio.create_task(stop_event.wait())],
                           return_when=asyncio.FIRST_COMPLETED)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

def main():
    args = parse_args()
    print(f"# Connecting to products: {', '.join(args.products)}")
    print(f"# Print interval: {args.interval} seconds")
    asyncio.run(amain(args))

if __name__ == "__main__":
    main()
