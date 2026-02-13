import os
import requests
import sys
import argparse
import json
import time
from datetime import datetime, timedelta

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

# Function to acquire a token
def get_token(api_key_id, api_key_secret, api_key_scope, oauth_url, args):
    auth_url = f"{oauth_url}/oauth2/token"
    body = {
        "grant_type": "client_credentials",
        "client_id": api_key_id,
        "client_secret": api_key_secret,
        "scope": api_key_scope
    }
    response = requests.post(auth_url, data=body)
    
    if response.status_code == 200:
        response_data = response.json()
        access_token = response_data.get("access_token")
        expires_in = response_data.get("expires_in")
        if not access_token or not expires_in:
            raise ValueError("Missing token or expiration in response")
        
        expiration_time = datetime.utcnow() + timedelta(seconds=expires_in)
        if not getattr(args, 'raw', False):
            print(f"Acquired token successfully. Expires in {expires_in} seconds (at {expiration_time}).")
        return access_token
    else:
        raise RuntimeError(f"Failed to acquire token. HTTP {response.status_code}: {response.text}")

# Generate a JWS signature for Paxos request signing
def _detect_algorithm(signing_key):
    key = load_pem_private_key(signing_key, password=None)
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return "ES256"
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        return "EdDSA"
    else:
        raise ValueError(f"Unsupported key type: {type(key)}")

def sign_request(method, path, body_bytes, signing_key, signing_key_id):
    alg = _detect_algorithm(signing_key)
    jws_headers = {
        "kid": signing_key_id,
        "alg": alg,
        "paxos.com/timestamp": str(int(time.time())),
        "paxos.com/request-method": method.upper(),
        "paxos.com/request-path": path,
    }
    return jwt.api_jws.encode(
        payload=body_bytes,
        key=signing_key,
        headers=jws_headers,
        algorithm=alg,
    )

# Function to make an API request
def make_request(base_url, token, method, path, body=None, signing_key=None, signing_key_id=None):
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    method = method.lower()

    # Use raw body bytes so the JWS payload matches exactly what's sent
    body_bytes = body.encode("utf-8") if body else b""

    if signing_key and signing_key_id:
        headers["Paxos-Signature"] = sign_request(method, path, body_bytes, signing_key, signing_key_id)

    if method == "get":
        response = requests.get(url, headers=headers)
    elif method == "post":
        response = requests.post(url, headers=headers, data=body_bytes)
    elif method == "put":
        response = requests.put(url, headers=headers, data=body_bytes)
    elif method == "delete":
        response = requests.delete(url, headers=headers)
    elif method == "patch":
        response = requests.patch(url, headers=headers, data=body_bytes)
    else:
        raise ValueError(f"Unsupported method: {method}")

    return response

# Main function to parse arguments and execute the script
def main():
    parser = argparse.ArgumentParser(description="API client script.")
    parser.add_argument("--method", required=True, help="HTTP method (get, post, put, delete, patch).")
    parser.add_argument("--path", required=True, help="API path (e.g., /v2/conversion/stablecoin).")
    parser.add_argument("--body", help="Request body (for POST, PUT or PATCH methods).")
    parser.add_argument("--pretty", action=argparse.BooleanOptionalAction, help="Formats the output for readability.")
    parser.add_argument("--raw", action=argparse.BooleanOptionalAction, help="Only outputs JSON response data, no status code or other logs")
    
    args = parser.parse_args()

    # Load environment variables
    api_key_id = os.getenv("API_KEY_ID")
    api_key_secret = os.getenv("API_KEY_SECRET")
    api_key_scope = os.getenv("API_KEY_SCOPE")
    base_url = os.getenv("BASE_URL", "https://api.sandbox.paxos.com")
    oauth_url = os.getenv("OAUTH_URL", "https://oauth.sandbox.paxos.com")
    signing_key_path = os.getenv("PAXOS_SIGNING_KEY_PATH")
    signing_key_id = os.getenv("PAXOS_SIGNING_KEY_ID")

    if not api_key_id or not api_key_secret or not api_key_scope:
        print("Error: API_KEY_ID, API_KEY_SECRET, and API_KEY_SCOPE must be set as environment variables.")
        sys.exit(1)

    signing_key = None
    if signing_key_path and signing_key_id:
        with open(signing_key_path, "rb") as f:
            signing_key = f.read()

    try:
        # Get token
        token = get_token(api_key_id, api_key_secret, api_key_scope, oauth_url, args)

        # Make the request
        response = make_request(base_url, token, args.method, args.path, args.body, signing_key, signing_key_id)
        
        # Output the response
        if not getattr(args, 'raw', False):
            print(f"Status Code: {response.status_code}")
        if args.pretty == True:
            print(json.dumps(response.json(), indent=4))
        else:
            print(response.text)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

