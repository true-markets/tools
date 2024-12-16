import os
import requests
import sys
import argparse
import json
from datetime import datetime, timedelta

# Function to acquire a token
def get_token(api_key_id, api_key_secret, api_key_scope, base_url, args):
    auth_url = f"{base_url}/oauth2/token"
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
        if args.raw != True:
            print(f"Acquired token successfully. Expires in {expires_in} seconds (at {expiration_time}).")
        return access_token
    else:
        raise RuntimeError(f"Failed to acquire token. HTTP {response.status_code}: {response.text}")

# Function to make an API request
def make_request(base_url, token, method, path, body=None):
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    method = method.lower()
    
    if method == "get":
        response = requests.get(url, headers=headers)
    elif method == "post":
        response = requests.post(url, headers=headers, json=json.loads(body) if body else None)
    elif method == "put":
        response = requests.put(url, headers=headers, json=json.loads(body) if body else None)
    elif method == "delete":
        response = requests.delete(url, headers=headers)
    elif method == "patch":
        response = requests.patch(url, headers=headers, json=json.loads(body) if body else None)
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
    parser.add_argument("--raw", action=argparse.BooleanOptionalAction, help="Only outputs JSON response data, not status code")
    
    args = parser.parse_args()

    # Load environment variables
    api_key_id = os.getenv("API_KEY_ID")
    api_key_secret = os.getenv("API_KEY_SECRET")
    api_key_scope = os.getenv("API_KEY_SCOPE")
    base_url = os.getenv("BASE_URL", "https://api.sandbox.paxos.com")
    
    if not api_key_id or not api_key_secret or not api_key_scope:
        print("Error: API_KEY_ID, API_KEY_SECRET, and API_KEY_SCOPE must be set as environment variables.")
        sys.exit(1)

    try:
        # Get token
        token = get_token(api_key_id, api_key_secret, api_key_scope, base_url, args)
        
        # Make the request
        response = make_request(base_url, token, args.method, args.path, args.body)
        
        # Output the response
        if args.raw != True:
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

