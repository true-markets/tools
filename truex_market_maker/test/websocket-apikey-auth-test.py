import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import urllib

import websockets

###
# websocket-apikey-auth-test.py
#
# Reference Python implementation for authorizing with websocket.
# See https://docs.truex.co/reference/ws API for more details
#
###

# These must set to your own API key and secret.
API_KEY = os.environ["TRUEX_REST_API_KEY"]
API_SECRET = os.environ["TRUEX_REST_SECRET_KEY"]

# Switch these comments to use testnet instead.
# TRUEX_URL = "ws://uat.truex.co"
# TRUEX_URL = "ws://dev.truex.co"
TRUEX_URL = "10.10.10.13"
TRUEX_PORT = 4444


def main():
    """Authenticate with the TrueX API & request account information."""
    # asyncio.run(TestAuthenticateSubscribe())
    # asyncio.run(TestAuthenticateSubscribeAndUnsubscribe())
    asyncio.run(TestAuthenticateSubscribeUpdateAndUnsubscribe())


async def TestAuthenticateSubscribe():
    timestamp = str(int(time.time()))
    # See signature generation reference at: TBD
    signature = TruexSignature(API_SECRET, timestamp, API_KEY)

    # Initial connection - TrueX sends a welcome message.
    uri = f"ws://{TRUEX_URL}:{TRUEX_PORT}"
    try:
        async with websockets.connect(uri) as ws:
            print("Receiving Welcome Message...")
            response = await ws.recv()
            print("Received '%s'" % response)

            # Send API Key with signed message.
            timestamp = str(int(time.time()))
            signature = TruexSignature(API_SECRET, timestamp, API_KEY)
            request = {
                "type": "SUBSCRIBE",
                "item_names": ["BTC-PYUSD"],
                "channels": ["INSTRUMENT"],
                "key": API_KEY,
                "timestamp": timestamp,
                "signature": signature,
            }
            await ws.send(json.dumps(request))
            print("Sent subscription request")
            response = await ws.recv()
            print("Received '%s'" % response)
            await ws.close()
    except Exception as e:
        print(f"An error occurred: {e}")


async def TestAuthenticateSubscribeAndUnsubscribe():
    async def SendMessages(ws):
        request = {
            "type": "SUBSCRIBE",
            "item_names": ["BTC-PYUSD"],
            "channels": ["INSTRUMENT"],
        }
        await SendRequest(ws, request)

        request = {
            "type": "UNSUBSCRIBE",
            "item_names": ["BTC-PYUSD"],
            "channels": ["INSTRUMENT"],
        }
        await SendRequest(ws, request)

    async def ReceiveMessages(ws):
        """Task to handle receiving messages."""
        try:
            while True:
                message = await ws.recv()
                print(f"Received: {message}")
                message = json.loads(message)
                if (
                    message["channel"] == "WEBSOCKET"
                    and message["update"] == "SNAPSHOT"
                ):
                    if len(message["subscriptions"]) == 0:
                        raise Exception("All subscriptions removed.")
        except websockets.ConnectionClosed:
            print("Connection closed while receiving.")

    # Initial connection - TrueX sends a welcome message.
    uri = f"ws://{TRUEX_URL}:{TRUEX_PORT}"
    try:
        async with websockets.connect(uri) as ws:
            print("Receiving Welcome Message...")
            recv_task = asyncio.create_task(ReceiveMessages(ws))
            send_task = asyncio.create_task(SendMessages(ws))
            await asyncio.gather(send_task, recv_task)
    except Exception as e:
        print(f"{e}")
    finally:
        await ws.close()


async def TestAuthenticateSubscribeUpdateAndUnsubscribe():
    async def SendMessages(ws):
        request = {
            "type": "SUBSCRIBE",
            "item_names": ["BTC-PYUSD"],
            "channels": ["INSTRUMENT"],
        }
        await SendRequest(ws, request)

        request = {
            "type": "SUBSCRIBE",
            "item_names": ["ETH-PYUSD"],
            "channels": ["INSTRUMENT"],
        }
        await SendRequest(ws, request)

        request = {
            "type": "UNSUBSCRIBE",
            "item_names": ["BTC-PYUSD"],
            "channels": ["INSTRUMENT"],
        }
        await SendRequest(ws, request)

    async def ReceiveMessages(ws):
        count = 0
        """Task to handle receiving messages."""
        try:
            while True:
                message = await ws.recv()
                print(f"Received: {message}")
                message = json.loads(message)
                if message["update"] == "SNAPSHOT":
                    count += 1
                    if count == 5:
                        raise Exception("All snapshots recieved.")
        except websockets.ConnectionClosed:
            print("Connection closed while receiving.")

    # Initial connection - TrueX sends a welcome message.
    uri = f"ws://{TRUEX_URL}:{TRUEX_PORT}"
    try:
        async with websockets.connect(uri) as ws:
            print("Receiving Welcome Message...")
            recv_task = asyncio.create_task(ReceiveMessages(ws))
            send_task = asyncio.create_task(SendMessages(ws))
            await asyncio.gather(send_task, recv_task)
    except Exception as e:
        print(f"{e}")
    finally:
        await ws.close()


async def SendRequest(ws, request):
    timestamp = str(int(time.time()))
    signature = TruexSignature(API_SECRET, timestamp, API_KEY)
    request["key"] = API_KEY
    request["timestamp"] = timestamp
    request["signature"] = signature
    await ws.send(json.dumps(request))


def TruexSignature(secret_key, timestamp, api_key):
    message = timestamp + "TRUEXWS" + api_key
    hmac_key = str.encode(secret_key)
    signature = hmac.new(
        hmac_key, message.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(signature).decode()


if __name__ == "__main__":
    main()
