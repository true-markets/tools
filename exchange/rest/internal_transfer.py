import os
import sys
import argparse
import json
import uuid

# Add project root to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from custodian.paxos import get_token as paxos_get_token, make_request as paxos_make_request
from exchange.rest.rest import make_request as exchange_make_request


def main():
    parser = argparse.ArgumentParser(description="Perform an internal transfer between Paxos profiles and notify the exchange.")
    parser.add_argument("--from-profile", required=True, help="Source Paxos profile ID")
    parser.add_argument("--to-profile", required=True, help="Destination Paxos profile ID")
    parser.add_argument("--asset", required=True, help="Asset to transfer (e.g., USD, PYUSD)")
    parser.add_argument("--amount", required=True, help="Amount to transfer")
    parser.add_argument("--client-id", required=True, help="Exchange client ID")
    parser.add_argument("--asset-id", required=True, help="Exchange asset ID for the transfer notification")
    parser.add_argument("--server", type=str, default="uat.truex.co:9742", help="Exchange REST API host:port")

    args = parser.parse_args()

    # Load Paxos credentials from environment
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

    if not signing_key_path or not signing_key_id:
        print("Error: PAXOS_SIGNING_KEY_PATH and PAXOS_SIGNING_KEY_ID must be set for internal transfers.")
        sys.exit(1)

    with open(signing_key_path, "rb") as f:
        signing_key = f.read()

    # Step 1: Initiate internal transfer on Paxos
    print("Acquiring Paxos OAuth token...")
    token = paxos_get_token(api_key_id, api_key_secret, api_key_scope, oauth_url, args)

    transfer_body = json.dumps({
        "ref_id": str(uuid.uuid4()),
        "from_profile_id": args.from_profile,
        "to_profile_id": args.to_profile,
        "amount": args.amount,
        "asset": args.asset,
    })

    print(f"Initiating Paxos internal transfer: {args.amount} {args.asset} from {args.from_profile} to {args.to_profile}...")
    paxos_response = paxos_make_request(base_url, token, "post", "/v2/transfer/internal", transfer_body, signing_key, signing_key_id)

    if paxos_response.status_code != 200:
        print(f"Paxos transfer failed. HTTP {paxos_response.status_code}: {paxos_response.text}")
        sys.exit(1)

    paxos_data = paxos_response.json()
    transfer_id = paxos_data.get("id")
    if not transfer_id:
        print(f"Error: No transfer ID in Paxos response: {json.dumps(paxos_data, indent=2)}")
        sys.exit(1)

    print(f"Paxos transfer initiated successfully. Transfer ID: {transfer_id}")

    # Step 2: Notify the exchange
    exchange_body = json.dumps({
        "request": {
            "asset_id": args.asset_id,
            "client_id": args.client_id,
            "platform": "INTERNAL",
            "type": "DEPOSIT",
            "amount": args.amount,
            "instructions": {
                "transfer_id": transfer_id
            }
        }
    })

    print(f"Notifying exchange of transfer...")
    exchange_response = exchange_make_request(args.server, "transfer", "post", exchange_body, args.client_id)

    if exchange_response.status_code == 200:
        print("Exchange notified successfully:")
        print(json.dumps(exchange_response.json(), indent=4))
    else:
        print(f"Exchange notification failed. HTTP {exchange_response.status_code}: {exchange_response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
