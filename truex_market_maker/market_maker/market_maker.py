import random
import atexit
import signal
import sys
import os
import time

from datetime import datetime
# our modules
from market_maker import truex
from market_maker.settings import settings
from market_maker.utils import log, constants, errors, math

#
# Helpers
#
watched_files_mtimes = [(f, os.path.getmtime(f)) for f in settings.WATCHED_FILES]
logger = log.setup_custom_logger('root')

class ExchangeInterface:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        if len(sys.argv) > 1:
            self.symbol = sys.argv[1]
        else:
            self.symbol = settings.SYMBOL
        self.truex = truex.TrueX(rest_url=settings.BASE_REST_URL, ws_url=settings.BASE_WS_URL, symbol=self.symbol,
                                    apiKey=settings.API_KEY, apiSecret=settings.API_SECRET,
                                    orderIDPrefix=settings.ORDERID_PREFIX, postOnly=settings.POST_ONLY,
                                    timeout=settings.TIMEOUT)
        self.client_id = None

    def get_instrument(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        logger.info("Subscribing to instrument data for symbol: %s" % symbol)
        self.truex.Instrument(symbol)
        self.truex.Ticker(symbol)
        return symbol

    def get_client(self):
        # return JSON object of client data
        response = self.truex.Client()
        for entry in response:
            if entry['info']['mnemonic'] == settings.API_USER:
                logger.info("Found matching ID: %s" % entry['id'])
                self.client_id = entry['id']
                return self.client_id

        logger.error("No matching ID found for user: %s." % settings.TRUEX_USER)
        return 0

    def get_delta(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.get_position(symbol)['qty']

    def get_position(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.truex.Position(symbol)

    def get_base_balance(self):
        return self.truex.BaseBalance()

    def get_quote_balance(self):
        return self.truex.QuoteBalance()

    def get_orders(self):
        if self.dry_run:
            return []
        return self.truex.OpenOrders()

    def get_highest_buy(self):
        buys = [o for o in self.get_orders() if o['order_info']['side'] == 'Buy']
        if not len(buys):
            return {'price': -2**32}
        highest_buy = max(buys or [], key=lambda o: o['order_info']['price'])
        return highest_buy if highest_buy else {'price': -2**32}

    def get_lowest_sell(self):
        sells = [o for o in self.get_orders() if o['order_info']['side'] == 'Sell']
        if not len(sells):
            return {'price': 2**32}
        lowest_sell = min(sells or [], key=lambda o: o['order_info']['price'])
        return lowest_sell if lowest_sell else {'price': 2**32}  # ought to be enough for anyone

    def get_ticker(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.truex.Ticker(symbol)

    def create_orders(self, orders):
        if self.dry_run:
            return orders
        return self.truex.CreateOrders(orders)

    def amend_orders(self, orders):
        if self.dry_run:
            return orders
        return self.truex.AmendOrders(orders)

    def cancel_orders(self, orders):
        for o in orders:
            logger.info("Cancelling order %s" % order['external_id'])
            self.truex.CancelOrder(o['id'])

    def cancel_all_orders(self):
        if self.dry_run:
            return
        orders = self.get_orders()
        logger.info("Cancelling all open orders.")
        for order in orders:
            logger.info("Cancelling order %s" % order['external_id'])
            self.truex.CancelOrder(order['id'])

    # Checks
    def check_market(self):
        # Check if the market is open
        if not self.truex.IsMarketOpen(self.symbol):
            logger.error("Market is closed right now. Please try again later.")
            raise Exception("Market is closed right now. Please try again later.")

    def check_orderbook(self):
        # Check if the orderbook is sane
        return True

    def exit(self):
        self.truex.Exit()

class OrderManager:
    def __init__(self):
        self.exchange = ExchangeInterface(settings.DRY_RUN)
        if settings.DRY_RUN:
            logger.info("DRY RUN -- Orders printed below represent what would be posted to the exchange")
        else:
            logger.info("LIVE RUN -- Orders will be posted to the exchange")
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)

        self.start_time = datetime.now()
        self.client = self.exchange.get_client()
        self.instrument = self.exchange.get_instrument()
        self.starting_qty = self.exchange.get_delta()
        self.running_qty = self.starting_qty
        self.running = True

        logger.info(f"Order Manager initializing @ {self.start_time}")

    def restart(self):
        pass

    def reset(self):
        self.exchange.cancel_all_orders()
        self.check_sanity()
        self.print_status()

        # Create orders and converge.
        self.place_orders()

    def exit(self):
        if self is None or not self.running:
            return

        self.running = False
        if settings.CANCEL_ORDERS_ON_EXIT:
            logger.info("Shutting down. Cancelling all orders.")
            self.exchange.cancel_all_orders()
        self.exchange.exit()
        sys.exit()

    def get_ticker(self):
        ticker = self.exchange.get_ticker()
        # Set up our buy & sell positions as the smallest possible unit above and below the current spread
        # and we'll work out from there. That way we always have the best price but we don't kill wide
        # and potentially profitable spreads.
        self.start_position_buy = ticker["buy"] #+ self.instrument['tickSize']
        self.start_position_sell = ticker["sell"] #- self.instrument['tickSize']

        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if settings.MAINTAIN_SPREADS:
            if ticker['buy'] == self.exchange.get_highest_buy()['price']:
                self.start_position_buy = ticker["buy"]
            if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
                self.start_position_sell = ticker["sell"]

        # Back off if our spread is too small.
        if self.start_position_buy * (1.00 + settings.MIN_SPREAD) > self.start_position_sell:
            self.start_position_buy *= (1.00 - (settings.MIN_SPREAD / 2))
            self.start_position_sell *= (1.00 + (settings.MIN_SPREAD / 2))

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        logger.info(
            "%s Ticker: Buy: %f, Sell: %f" %
            (self.instrument, ticker["buy"], ticker["sell"])
        )
        logger.info('Start Positions: Buy: %f, Sell: %f, Mid: %f' %
                    (self.start_position_buy, self.start_position_sell,
                     self.start_position_mid))
        return ticker

    def get_price_offset(self, index):
        """Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
           Negative is a buy, positive is a sell."""
        # Maintain existing spreads for max profit
        if settings.MAINTAIN_SPREADS:
            start_position = self.start_position_buy if index < 0 else self.start_position_sell
            # First positions (index 1, -1) should start right at start_position, others should branch from there
            index = index + 1 if index < 0 else index - 1
        else:
            # Offset mode: ticker comes from a reference exchange and we define an offset.
            start_position = self.start_position_buy if index < 0 else self.start_position_sell

            # If we're attempting to sell, but our sell price is actually lower than the buy,
            # move over to the sell side.
            if index > 0 and start_position < self.start_position_buy:
                start_position = self.start_position_sell
            # Same for buys.
            if index < 0 and start_position > self.start_position_sell:
                start_position = self.start_position_buy

        tickSize = 0.50
        return math.toNearest(start_position * (1 + settings.INTERVAL) ** index, tickSize)


    ###
    # Orders
    ###
    def prepare_order(self, index):
        """Create an order object."""

        if settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(settings.MIN_ORDER_SIZE, settings.MAX_ORDER_SIZE)
            # Respect lot size
            quantity = round(quantity / settings.ORDER_STEP_SIZE) * settings.ORDER_STEP_SIZE
        else:
            quantity = settings.ORDER_START_SIZE + ((abs(index) - 1) * settings.ORDER_STEP_SIZE)

        price = self.get_price_offset(index)

        return {
            'client_id': self.client,
            'symbol': self.instrument,
            'price': str(price),
            'qty': str(quantity),
            'side': "BUY" if index < 0 else "SELL"
        }

    def place_orders(self):
        """Create order items for use in convergence."""

        buy_orders = []
        sell_orders = []
        # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        for i in reversed(range(1, settings.ORDER_PAIRS + 1)):
            if not self.long_position_limit_exceeded():
                buy_orders.append(self.prepare_order(-i))
            if not self.short_position_limit_exceeded():
                sell_orders.append(self.prepare_order(i))

        return self.converge_orders(buy_orders, sell_orders)

    def converge_orders(self, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
           This involves amending any open orders and creating new ones if any have filled completely.
           We start from the closest orders outward."""

        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.exchange.get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        for order in existing_orders:
            try:
                if order['order_info']['side'] == 'BUY':
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Found an existing order. Do we need to amend it?
                if desired_order['qty'] != order['leaves_qty'] or (
                        # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                        desired_order['price'] != order['order_info']['price'] and
                        abs((float(desired_order['price']) / float(order['order_info']['price'])) - 1) > settings.RELIST_INTERVAL):
                    logger.info(f"desired order {desired_order} {order}")
                    to_amend.append({'ref_order_id': order['id'], 'new_qty': str(float(order['executed_qty']) + float(desired_order['qty'])),
                                     'new_price': desired_order['price'], 'side': order['order_info']['side']})
            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                to_cancel.append(order)

        while buys_matched < len(buy_orders):
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1

        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [o for o in existing_orders if o['id'] == amended_order['ref_order_id']][0]
                logger.info("Amending %4s: %s @ %s to %f @ %s (%+f)" % (
                    amended_order['side'], reference_order['leaves_qty'],
                    reference_order['order_info']['price'], float(amended_order['new_qty']) - float(reference_order['executed_qty']),
                    amended_order['new_price'], (float(amended_order['new_price']) - float(reference_order['order_info']['price']))
                ))
                amended_order['client_id'] = self.client

            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
            # made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                self.exchange.amend_orders(to_amend)
            except Exception as e:
                logger("Amend failed: %s" % e)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %10s %s @ %s" % (order['side'], order['symbol'], order['qty'], order['price']))
            self.exchange.create_orders(to_create)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            self.exchange.cancel_orders(to_cancel)

    ###
    # Position Limits
    ###

    def short_position_limit_exceeded(self):
        """Returns True if the short position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position <= settings.MIN_POSITION

    def long_position_limit_exceeded(self):
        """Returns True if the long position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position >= settings.MAX_POSITION


    ###
    # Sanity / validation
    ###
    def check_file_change(self):
        for f, mtime in watched_files_mtimes:
            if os.path.getmtime(f) != mtime:
                logger.info("Detected change in %s, reloading." % f)
                self.restart()

    def check_sanity(self):
        """Performs some checks on exchange status, orders, etc."""
        # check if the market is open or not
        self.exchange.check_market()
        # check if the order is sane
        self.exchange.check_orderbook()
        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker()

        if self.get_price_offset(-1) >= ticker["sell"] or self.get_price_offset(1) <= ticker["buy"]:
            logger.error("Buy: %s, Sell: %s" % (self.start_position_buy, self.start_position_sell))
            logger.error("First buy position: %s\nTrueX Best Ask: %s\nFirst sell position: %s\nTrueX Best Bid: %s" %
                         (self.get_price_offset(-1), ticker["sell"], self.get_price_offset(1), ticker["buy"]))
            logger.error("Sanity check failed, exchange data is inconsistent")
            self.exit()

        # Messaging if the position limits are reached
        if self.long_position_limit_exceeded():
            logger.info("Long delta limit exceeded")
            logger.info("Current Position: %.f, Maximum Position: %.f" %
                        (self.exchange.get_delta(), settings.MAX_POSITION))

        if self.short_position_limit_exceeded():
            logger.info("Short delta limit exceeded")
            logger.info("Current Position: %.f, Minimum Position: %.f" %
                        (self.exchange.get_delta(), settings.MIN_POSITION))


    def print_status(self):
        """Print the current MM status."""

        position = self.exchange.get_position()
        self.running_qty = self.exchange.get_delta()

        logger.info("Current Position: %f" % self.running_qty)
        if settings.CHECK_POSITION_LIMITS:
            logger.info("Position limits: %d/%d" % (settings.MIN_POSITION, settings.MAX_POSITION))
        if position['qty'] != 0:
            logger.info("Avg Cost Price: %f" % (float(position['executed_vwap'])))
            logger.info("Avg Entry Price: %f" % (float(position['entry_vwap'])))
        logger.info("Quantity Traded This Run: %d" % (self.running_qty - self.starting_qty))

    def run_loop(self):
        while True:
            self.check_file_change()
            time.sleep(settings.LOOP_INTERVAL)

            self.check_sanity()
            self.print_status()
            self.place_orders()

def run():
    logger.info('TrueX Market Maker Version: %s\n' % constants.VERSION)

    om = OrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        om.run_loop()
    except (KeyboardInterrupt, SystemExit):
        om.exit()

if __name__ == "__main__":
    run()
