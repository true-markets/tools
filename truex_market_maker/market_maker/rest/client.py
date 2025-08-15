import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

import requests
from requests.auth import AuthBase

from market_maker.settings import settings
from market_maker.utils import constants, log

logger = log.setup_custom_logger("rest_client")


class RESTAuth(AuthBase):
    """Attaches API Key Authentication to the given Request object."""

    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_client = 0

    def __call__(self, request):
        # modify and return the request
        timestamp = str(int(time.time()))
        message = (
            timestamp
            + request.method
            + urlparse(request.path_url).path
            + str(request.body or "")
        )
        hmac_key = str.encode(self.api_secret)
        signature = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
        request.headers["x-truex-auth-token"] = self.api_key
        request.headers["x-truex-auth-signature"] = base64.b64encode(signature).decode()
        request.headers["x-truex-auth-timestamp"] = timestamp
        request.headers["x-truex-auth-user-id"] = str(self.api_client)
        return request

    def SetApiClient(self, api_client):
        self.api_client = api_client


class TruexRESTClient:
    def __init__(self):
        self.base_url = settings.BASE_REST_URL
        # Prepare HTTP session
        self.session = requests.Session()
        # These headers are always sent
        self.session.headers.update({"user-agent": "truexbot-" + constants.VERSION})
        self.session.headers.update({"content-type": "application/json"})
        self.session.headers.update({"accept": "application/json"})
        self.auth = RESTAuth(settings.API_KEY, settings.API_SECRET)

    def SetApiClient(self, api_client):
        self.auth.SetApiClient(api_client)

    def GetClient(self):
        url = self.base_url + "/client"
        return self.__Request("GET", url)

    def GetInstrument(self, symbol):
        url = self.base_url + "/instrument?symbol=" + symbol
        return self.__Request("GET", url)

    def GetBalance(self, asset):
        url = self.base_url + "/balance?asset_id=" + asset
        return self.__Request("GET", url)

    def GetOpenOrders(self):
        url = self.base_url + "/order/active"
        return self.__Request("GET", url)

    def PlaceOrder(self, order):
        url = self.base_url + "/order"
        return self.__Request("POST", url, body=json.dumps(order))

    def AmendOrder(self, order):
        url = self.base_url + "/order"
        return self.__Request("PATCH", url, body=json.dumps(order))

    def CancelOrder(self, orderID):
        url = self.base_url + "/order/" + str(orderID)
        return self.__Request("DELETE", url)

    def __Request(self, method, url, body=None, query=None, timeout=None):
        if timeout is None:
            timeout = settings.API_REST_TIMEOUT

        response = None
        try:
            logger.debug("Sending %s to %s with body %s" % (method, url, body))
            request = requests.Request(
                method, url, data=body, auth=self.auth, params=query
            )
            prepped = self.session.prepare_request(request)
            response = self.session.send(prepped, timeout=timeout)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # 4XX Client Error, 5XX Server Error responses
            logger.error("Error in request: %s" % response.text)
            raise Exception("Error in request: %s" % response.text)
        except requests.exceptions.RequestException as e:
            logger.error("Error in request: %s" % e)
            raise Exception("Error in request: %s" % response.text)
        except requests.exceptions.Timeout as e:
            logger.error("Request timed out: %s" % e)
            raise Exception("Error in request: %s" % response.text)
        except requests.exceptions.ConnectionError as e:
            logger.error("Connection error: %s" % e)
            raise Exception("Error in request: %s" % response.text)

        return response.json()
