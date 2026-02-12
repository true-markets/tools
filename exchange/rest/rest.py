import requests
import time
import hmac
import hashlib
import base64
import json
import os
import argparse
from urllib.parse import urlparse

# Define the constants for the headers
HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
HEADER_AUTH_TOKEN = "x-truex-auth-token"
HEADER_AUTH_USER_ID = "x-truex-auth-user-id"


def sign_request(secret_key, method, path, body, timestamp):
    """Create an HMAC signature for the given request parameters."""
    payload = timestamp + method.upper() + path + body
    hmac_key = secret_key.encode('utf-8')
    hmac_message = payload.encode('utf-8')
    hmac_digest = hmac.new(hmac_key, hmac_message, hashlib.sha256).digest()
    return base64.b64encode(hmac_digest).decode('utf-8')


def make_request(server, path, method, body="", client_id="0", auth_token=None, secret_key=None):
    """Make an authenticated request to the exchange REST API."""
    if auth_token is None:
        auth_token = os.getenv("TRUEX_KEY_ID")
    if secret_key is None:
        secret_key = os.getenv("TRUEX_KEY_SECRET")

    url = "http://" + server + "/api/v1/" + path
    parsed_url = urlparse(url)
    url_path = parsed_url.path

    auth_timestamp = str(int(time.time()))
    method = method.lower()

    auth_signature = sign_request(secret_key, method, url_path, body, auth_timestamp)

    headers = {
        HEADER_AUTH_TIMESTAMP: auth_timestamp,
        HEADER_AUTH_SIGNATURE: auth_signature,
        HEADER_AUTH_TOKEN: auth_token,
        HEADER_AUTH_USER_ID: str(client_id),
        "Content-Type": "application/json"
    }

    response = None
    if method == "get":
        response = requests.get(url, headers=headers)
    elif method == "post":
        response = requests.post(url, headers=headers, data=body)
    elif method == "patch":
        response = requests.patch(url, headers=headers, data=body)
    elif method == "delete":
        response = requests.delete(url, headers=headers)

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get the Client IDs affiliated with the API key")

    parser.add_argument('--server', type=str, default="uat1.truex.co:9742", help='The host and port of the REST API')
    parser.add_argument('--path', type=str, default="", help='URL path of the API call')
    parser.add_argument('--method', type=str, default="", help='Type of http request: get, post, patch, delete')
    parser.add_argument('--body', type=str, default="", help='JSON payload for post and patch')
    parser.add_argument('--client', type=str, default="0", help='Client ID to use in header')

    args = parser.parse_args()

    response = make_request(args.server, args.path, args.method, args.body, args.client)

    if response.status_code == 200:
        print("Success:", json.dumps(response.json(), indent=4))
    else:
        print(f"Failed with status code {response.status_code}: {response.text}")
