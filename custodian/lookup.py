import os
import sys
import argparse
import json

# Add project root to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from custodian.paxos import get_token, make_request


def get_accounts(token, base_url, signing_key=None, signing_key_id=None, **filters):
    """Fetch accounts with optional filters, handling pagination."""
    accounts = []
    page_token = None
    while True:
        path = "/v2/accounts"
        params = [f"{k}={v}" for k, v in filters.items() if v is not None]
        if page_token:
            params.append(f"page_token={page_token}")
        if params:
            path += "?" + "&".join(params)

        resp = make_request(base_url, token, "get", path, signing_key=signing_key, signing_key_id=signing_key_id)
        if resp.status_code != 200:
            print(f"Error fetching accounts: HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        accounts.extend(data.get("items", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break
    return accounts


def get_profile(token, base_url, profile_id, signing_key=None, signing_key_id=None):
    """Fetch a single profile by ID."""
    resp = make_request(base_url, token, "get", f"/v2/profiles/{profile_id}", signing_key=signing_key, signing_key_id=signing_key_id)
    if resp.status_code != 200:
        print(f"Error fetching profile {profile_id}: HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
        return None
    return resp.json()


def get_identity(token, base_url, identity_id, signing_key=None, signing_key_id=None):
    """Fetch a single identity by ID."""
    resp = make_request(base_url, token, "get", f"/v2/identities/{identity_id}", signing_key=signing_key, signing_key_id=signing_key_id)
    if resp.status_code != 200:
        print(f"Error fetching identity {identity_id}: HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
        return None
    return resp.json()


def resolve_by_profile(token, base_url, profile_id, signing_key=None, signing_key_id=None):
    """Profile -> accounts -> identities."""
    sk = dict(signing_key=signing_key, signing_key_id=signing_key_id)
    profile = get_profile(token, base_url, profile_id, **sk)
    if not profile:
        return []

    accounts = get_accounts(token, base_url, **sk)
    matching = [a for a in accounts if a.get("profile_id") == profile_id]

    results = []
    for acct in matching:
        identity = None
        if acct.get("identity_id"):
            identity = get_identity(token, base_url, acct["identity_id"], **sk)
        results.append({"profile": profile, "account": acct, "identity": identity})

    if not matching:
        results.append({"profile": profile, "account": None, "identity": None})

    return results


def resolve_by_identity(token, base_url, identity_id, signing_key=None, signing_key_id=None):
    """Identity -> accounts -> profiles."""
    sk = dict(signing_key=signing_key, signing_key_id=signing_key_id)
    identity = get_identity(token, base_url, identity_id, **sk)
    if not identity:
        return []

    accounts = get_accounts(token, base_url, identity_id=identity_id, **sk)

    results = []
    for acct in accounts:
        profile = None
        if acct.get("profile_id"):
            profile = get_profile(token, base_url, acct["profile_id"], **sk)
        results.append({"profile": profile, "account": acct, "identity": identity})

    if not accounts:
        results.append({"profile": None, "account": None, "identity": identity})

    return results


def resolve_all(token, base_url, signing_key=None, signing_key_id=None):
    """Fetch all accounts and resolve their identities and profiles."""
    sk = dict(signing_key=signing_key, signing_key_id=signing_key_id)
    accounts = get_accounts(token, base_url, **sk)

    # Batch-fetch unique identities and profiles
    identity_ids = {a["identity_id"] for a in accounts if a.get("identity_id")}
    profile_ids = {a["profile_id"] for a in accounts if a.get("profile_id")}

    identities = {}
    for iid in identity_ids:
        identities[iid] = get_identity(token, base_url, iid, **sk)

    profiles = {}
    for pid in profile_ids:
        profiles[pid] = get_profile(token, base_url, pid, **sk)

    results = []
    for acct in accounts:
        profile = profiles.get(acct.get("profile_id"))
        identity = identities.get(acct.get("identity_id"))
        results.append({"profile": profile, "account": acct, "identity": identity})

    return results


def truncate_id(id_str, length=8):
    if not id_str:
        return ""
    return id_str[:length] + "..." if len(id_str) > length else id_str


def print_table(results):
    """Print results as a formatted table."""
    header = f"{'Profile':<40} {'Account':<40} {'Identity':<40} {'Status'}"
    print(header)
    print("─" * len(header))

    for r in results:
        p = r.get("profile")
        a = r.get("account")
        i = r.get("identity")

        profile_str = ""
        if p:
            nickname = p.get("nickname", "")
            pid = truncate_id(p.get("id", ""))
            profile_str = f"{pid} ({nickname})" if nickname else pid

        account_str = ""
        if a:
            desc = a.get("description", "")
            aid = truncate_id(a.get("id", ""))
            account_str = f"{aid} ({desc})" if desc else aid

        identity_str = ""
        status = ""
        if i:
            itype = i.get("type", "")
            iid = truncate_id(i.get("id", ""))
            identity_str = f"{iid} ({itype})" if itype else iid
            status = i.get("status", "")

        print(f"{profile_str:<40} {account_str:<40} {identity_str:<40} {status}")


def main():
    parser = argparse.ArgumentParser(
        description="Resolve Paxos profile/account/identity relationships."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile-id", help="Lookup by profile ID")
    group.add_argument("--identity-id", help="Lookup by identity ID")
    group.add_argument("--all", action="store_true", help="List all account mappings")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")

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

    # Suppress token acquisition logs
    token_args = argparse.Namespace(raw=True)
    token = get_token(api_key_id, api_key_secret, api_key_scope, oauth_url, token_args)

    sk = dict(signing_key=signing_key, signing_key_id=signing_key_id)
    if args.profile_id:
        results = resolve_by_profile(token, base_url, args.profile_id, **sk)
    elif args.identity_id:
        results = resolve_by_identity(token, base_url, args.identity_id, **sk)
    else:
        results = resolve_all(token, base_url, **sk)

    if args.json:
        indent = 4 if args.pretty else None
        print(json.dumps(results, indent=indent))
    else:
        print_table(results)


if __name__ == "__main__":
    main()
