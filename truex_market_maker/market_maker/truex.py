"""Truex API Connector."""
from __future__ import absolute_import
import requests
import time
import datetime
import json
import base64
import uuid
import asyncio

from market_maker.utils import constants, errors, log
from market_maker.ws.processor import TruexWebsocket
from market_maker.rest.client import TruexRESTClient

logger = log.setup_custom_logger('root')


class TrueX(object):

    """TrueX API Connector."""

    def __init__(self, rest_url=None,
                 ws_url=None, symbol=None, apiKey=None, apiSecret=None,
                 orderIDPrefix='mm_truex_', shouldWSAuth=True, postOnly=False, timeout=7, condition=None):
        """Init connector."""
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.symbol = symbol
        self.postOnly = postOnly
        self.apiKey = apiKey
        self.apiSecret = apiSecret
        self.apiClient = 0
        if len(orderIDPrefix) > 18:
            raise ValueError("settings.ORDERID_PREFIX must be at most 18 characters long!")
        self.orderIDPrefix = orderIDPrefix
        self.retries = 0  # initialize counter
        self.orderId = 0
        self.amendId = 0

        # Create websocket for streaming data
        self.ws = TruexWebsocket(condition=condition)
        self.ws.Connect(ws_url)
        self.rest = TruexRESTClient()

        self.timeout = timeout
        logger.info("TrueX API Client successfully initialized.")

    def Instrument(self, symbol=None):
        """Subscribe to instrument's details/updates and ticker data."""
        if symbol is None:
            symbol = self.symbol
        self.ws.SubscribeToInstrument(symbol)

    def BaseBalance(self):
        base_asset_id = self.ws.GetInstrumentBaseAsset(self.symbol)
        if (base_asset_id is None):
            return {}
        return self.rest.GetBalance(base_asset_id)

    def QuoteBalance(self):
        """Get your balance."""
        quote_asset_id = self.ws.GetInstrumentQuoteAsset(self.symbol)
        if (quote_asset_id is None):
            return {}
        return self.rest.GetBalance(quote_asset_id)

    def Position(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        """Get your position."""
        return self.ws.Position(symbol)

    def OpenOrders(self):
        """Get open orders."""
        openOrders = []
        orders = self.rest.GetOpenOrders()
        for order in orders:
            if order['external_id'].startswith(self.orderIDPrefix) or order['ref_external_id'].startswith(self.orderIDPrefix):
                openOrders.append(order)
        return openOrders

    def CreateOrders(self, orders):
        """Create orders."""
        results = []
        for order in orders:
            results.append(self.PlaceOrder(order))
        return results

    def PlaceOrder(self, order):
        """Place an order."""

        self.orderId += 1
        order = {
            'external_id': self.orderIDPrefix + str(self.orderId),
            'info': {
                'client_id': order['client_id'],
                'instrument_id': self.ws.GetInstrumentId(order['symbol']),
                'qty': order['qty'],
                'price': order['price'],
                'side': order['side'],
                'type': "LIMIT",
                'tif': "GTC",
                'exec_inst_flags': ["ALO"] if self.postOnly else [],
            }
        }
        return self.rest.PlaceOrder(order)

    def AmendOrders(self, orders):
        """Amend orders."""
        results = []
        for order in orders:
            results.append(self.AmendOrder(order))
        return results

    def AmendOrder(self, order):
        """Amend an order."""
        self.amendId += 1
        modify = {
            'id': order['ref_order_id'],
            'external_id': self.orderIDPrefix + "mod-" + str(self.amendId),
            'info': {
                'client_id': order['client_id'],
                'new_qty': order['new_qty'],
                'new_price': order['new_price'],
            }
        }
        return self.rest.AmendOrder(modify)

    def CancelOrder(self, orderID):
        """Cancel an order."""
        return self.rest.CancelOrder(orderID)

    def Market(self, symbol=None):
        """Get market data."""
        if symbol is None:
            symbol = self.symbol
        self.ws.SubscribeToTicker(symbol)

    def Ticker(self, symbol=None):
        """Get ticker data."""
        if symbol is None:
            symbol = self.symbol
        return self.ws.Ticker(symbol)

    def IsMarketOpen(self, symbol=None):
        """Check if the market is open right now."""
        if symbol is None:
            symbol = self.symbol
        return self.ws.IsMarketOpen(symbol)

    def Client(self):
        """Get a REST client for TrueX."""
        return self.rest.GetClient()

    def SetApiClient(self, clientId):
        self.apiClient = clientId
        self.rest.SetApiClient(clientId)

    def Exit(self):
        self.ws.exit()
