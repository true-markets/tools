import os
import sys
import argparse
import json
import uuid

# Add project root to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from exchange.rest.rest import make_request as exchange_make_request


def main():
    parser = argparse.ArgumentParser(description="Post an internal withdrawal request to the trading system.")
    parser.add_argument("--from-profile", required=True, help="Source Paxos profile ID (user)")
    parser.add_argument("--from-account", required=True, help="Source Paxos account ID (user)")
    parser.add_argument("--from-identity", required=True, help="Source Paxos identity ID (user)")
    parser.add_argument("--to-profile", required=True, help="Destination Paxos profile ID (TM corporate)")
    parser.add_argument("--to-account", required=True, help="Destination Paxos account ID (TM corporate)")
    parser.add_argument("--to-identity", required=True, help="Destination Paxos identity ID (TM corporate)")
    parser.add_argument("--asset-id", required=True, help="Exchange asset ID")
    parser.add_argument("--client-id", required=True, help="Exchange client ID")
    parser.add_argument("--amount", required=True, help="Amount to withdraw")
    parser.add_argument("--server", type=str, default="uat.truex.co:9742", help="Exchange REST API host:port")

    args = parser.parse_args()

    body = json.dumps({
        "request": {
            "asset_id": args.asset_id,
            "client_id": args.client_id,
            "amount": args.amount,
            "type": "WITHDRAW",
            "platform": "INTERNAL",
            "instructions": {
                "ref_id": str(uuid.uuid4()),
                "from_profile_id": args.from_profile,
                "from_account_id": args.from_account,
                "from_identity_id": args.from_identity,
                "to_profile_id": args.to_profile,
                "to_account_id": args.to_account,
                "to_identity_id": args.to_identity,
            }
        }
    })

    print(f"Posting internal withdrawal: {args.amount} (asset {args.asset_id}) for client {args.client_id}...")
    response = exchange_make_request(args.server, "transfers", "post", body, args.client_id, base_path="/admin/api/v1/")

    if response.status_code == 200:
        print("Withdrawal request succeeded:")
        print(json.dumps(response.json(), indent=4))
    else:
        print(f"Withdrawal request failed. HTTP {response.status_code}: {response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
