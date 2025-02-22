import requests
import time
import hmac
import hashlib
import base64
import json
import os
import argparse
from urllib.parse import urlparse

parser = argparse.ArgumentParser(description="Get the Client IDs affiliated with the API key")

# Add the --name argument with a default value of "World"
parser.add_argument('--server', type=str, default="uat1.truex.co:9742", help='The host and port of the REST API')
parser.add_argument('--path', type=str, default="", help='URL path of the API call')
parser.add_argument('--method', type=str, default="", help='Type of http request: get, post, patch, delete')
parser.add_argument('--body', type=str, default="", help='JSON payload for post and patch')
parser.add_argument('--client', type=str, default="0", help='Client ID to use in header')

# Parse the command-line arguments
args = parser.parse_args()

# Define the constants for the headers
HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
HEADER_AUTH_TOKEN = "x-truex-auth-token"
HEADER_AUTH_USER_ID = "x-truex-auth-user-id"

# Assuming the secret and token are fetched from a secure source
auth_token = os.getenv("TRUEX_KEY_ID")
secret_key = os.getenv("TRUEX_KEY_SECRET")

# Example of the URL
url = "http://" + str(args.server) + "/api/v1/" + str(args.path)  # Replace with your REST endpoint

# Prepare the current timestamp and method
auth_timestamp = str(int(time.time()))  # Unix timestamp as string
method = str(args.method).lower()

# Combine the values into a payload for HMAC
parsed_url = urlparse(url)
path = parsed_url.path
payload = auth_timestamp + method.upper() + path + str(args.body)

# Create HMAC signature using the secret key
hmac_key = secret_key.encode('utf-8')
hmac_message = payload.encode('utf-8')
hmac_digest = hmac.new(hmac_key, hmac_message, hashlib.sha256).digest()

# Convert the HMAC result to base64
auth_signature = base64.b64encode(hmac_digest).decode('utf-8')

# Setup the headers for the request
headers = {
    HEADER_AUTH_TIMESTAMP: auth_timestamp,
    HEADER_AUTH_SIGNATURE: auth_signature,
    HEADER_AUTH_TOKEN: auth_token,
    HEADER_AUTH_USER_ID: str(args.client),
    "Content-Type": "application/json"
}

response = None
if method == "get":
    response = requests.get(url, headers=headers)
elif method == "post":
    response = requests.post(url, headers=headers, data=str(args.body))
elif method == "patch":
    response = requests.patch(url, headers=headers, data=str(args.body))
elif method == "delete":
    response = requests.delete(url, headers=headers)

# Check the response
if response.status_code == 200:
    print("Success:", json.dumps(response.json(), indent=4))  # Assuming the response is JSON
else:
    print(f"Failed with status code {response.status_code}: {response.text}")
