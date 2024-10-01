import requests
import time
import hmac
import hashlib
import base64
import os
import argparse
from urllib.parse import urlparse

parser = argparse.ArgumentParser(description="Get the Client IDs affiliated with the API key")

# Add the --name argument with a default value of "World"
parser.add_argument('--server', type=str, default="uat.truex.co:9742", help='The host and port of the REST API')

# Parse the command-line arguments
args = parser.parse_args()

# Define the constants for the headers
HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
HEADER_AUTH_TOKEN = "x-truex-auth-token"

# Assuming the secret and token are fetched from a secure source
auth_token = os.getenv("TRUEX_KEY_ID")
secret_key = os.getenv("TRUEX_KEY_SECRET")

# Example of the URL
url = "http://" + str(args.server) + "/api/v1/client"  # Replace with your REST endpoint

# Prepare the current timestamp and method
auth_timestamp = str(int(time.time()))  # Unix timestamp as string
http_method = "GET"  # Replace with the actual HTTP method

# Combine the values into a payload for HMAC
parsed_url = urlparse(url)
path = parsed_url.path
payload = auth_timestamp + http_method + path

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
    "Content-Type": "application/json"
}

# Perform the GET request (or other HTTP methods as necessary)
response = requests.get(url, headers=headers)

# Check the response
if response.status_code == 200:
    print("Success:", response.json())  # Assuming the response is JSON
else:
    print(f"Failed with status code {response.status_code}: {response.text}")

matching_id = None
# Loop through each entry in the response data
for entry in response.json():
    matching_id = entry['id']
    break  # Exit the loop once a match is found

# Check if a match was found
if matching_id:
    print(f"Found matching ID: {matching_id}")
else:
    print("No matching api_key_id found.")
