# FIX Trading Client with Interactive CLI

This Python script provides a **FIX (Financial Information Exchange)** trading client with an interactive CLI interface. It supports key FIX session features, such as sending and managing orders, and integrates with an external API for managing client IDs.

---

## Features
- Interactive command-line interface built using `urwid`.
- Integration with FIX protocol using `quickfix` and `quickfix50sp2`.
- FIX logon with ES256 API keys from the web UI (JSON key file) or legacy HMAC keys.
- REST lookups (client IDs, instruments, active orders) authenticated with a JWT from the auth API for ES256 keys, or HMAC signatures for HMAC keys.
- Supports `order`, `modify`, `cancel`, and `logout` commands.
- Real-time FIX session monitoring and management.

---

## Requirements

### Dependencies
The script relies on the following Python libraries:
- `quickfix` and `quickfix50sp2`
- `urwid`
- `requests`
- Standard Python modules: `queue`, `threading`, `os`, `sys`, `argparse`, `hmac`, `hashlib`, `base64`, `time`, `uuid`, `datetime`, `urllib`.

Install dependencies via `pip`:
```
pip install urwid requests cryptography
```

The `quickfix` library must be installed separately (refer to [QuickFIX installation guide](https://github.com/quickfix/quickfix)).

---

## Setup

1. **API Key**
   The clients accept either kind of key the FIX gateway supports.

   **ES256 key (recommended).** Create a key at console.truemarkets.co (organization key) or in Settings → API keys (personal key) and download the JSON key file. Pass it with `--key-file`, or set `TRUEX_KEY_FILE`:
```
python fix_client.py --env dev --key-file ~/Downloads/truemarkets-api-key-6c951334.json
```
   The file holds `key_id` and a P-256 `private_key` JWK. The logon Password (554) is the unpadded base64url raw `r || s` ES256 signature. Keep the file private.

   For REST calls, the clients sign `{key_id}.{timestamp}` and exchange it at `POST /v1/auth/api-key/token` for a JWT, which they send as `Authorization: Bearer`. They mint a new one shortly before it expires. The auth API is `https://api.dev.truemarkets.co`, `https://api.uat.truemarkets.co` or `https://api.truemarkets.co` depending on `--env`. Set `TRUEX_AUTH_URL` to override it (required for `--env local`).

   REST calls go to the TrueX CeFi API (`/v1/cefi/...`) at `https://api.dev.truex.co`, `https://api.uat.truex.co` or `https://api.truex.co`, or `TRUEX_HOST`:`TRUEX_REST_PORT` for `--env local`.

   The REST API only accepts JWTs that carry a client, so this works for personal keys. Organization keys can still log on to FIX, but their REST calls are rejected. For those, pass each client ID with `--client-id` (repeat it for more than one); `--client-id` also skips the REST client lookup for any key. A personal key's session can only trade for that user's own client.

   **HMAC key (legacy, being phased out).** Set these environment variables. They are used when no key file is given:
   - `TRUEX_KEY_ID`: API key ID for authentication.
   - `TRUEX_KEY_SECRET`: API secret key for signing requests.
```
export TRUEX_KEY_ID="your_key_id"
export TRUEX_KEY_SECRET="your_key_secret"
```

2. **FIX Configuration**
   The script requires FIX configuration files for each environment (`dev`, `uat`, `prod`), e.g., `fix_config_dev.cfg`. The configuration file should define FIX session settings, such as:
   - Connection details (host, port).
   - Sender/Target comp IDs.
   - Log and store paths.

---

## Usage

### Download the latest FIX specification
Use the following command to download the latest TrueX FIX specification:

```
python download_spec.py
```

### Run the Script
Use the following command to start the FIX client:

```
python fix_client.py --env ENV [--key-file KEY_FILE] [--client-id CLIENT_ID]
```

Replace `ENV` with the environment you want to run the client in (`dev`, `uat`, or `prod`).

### Listen to the Drop Copy Stream
The repository also contains `dropcopy_client.py`, a lightweight program focused on order-state drop copy updates. It connects via the same FIX configuration files, makes a REST call to discover the API user's client IDs, sends a `35=AD` request (including those IDs), and renders the latest `35=8` execution reports per order on the console.

```
python dropcopy_client.py --env dev
```

Authenticate with an ES256 key file (`--key-file`), or set `TRUEX_KEY_ID` and `TRUEX_KEY_SECRET` for an HMAC key:

```
python dropcopy_client.py --env dev --key-file ~/Downloads/truemarkets-api-key-6c951334.json
```

### Available Commands
Interact with the script using the following commands in the CLI:

- **Help**:
  ```
  help
  ```
  Displays available commands and usage information.

- **Order**:
  ```
  order BUY|SELL <price> <size> <client_index>
  ```
  Sends a new order with the specified parameters:
  - `BUY|SELL`: Order direction.
  - `<price>`: Price of the order.
  - `<size>`: Size of the order.
  - `<client_index>`: Index of the client ID retrieved via the API.

- **Modify**:
  ```
  modify <orig_cl_ord_id> <client_index> <new_price> <new_size>
  ```
  Modifies an existing order using the ClOrdId and the index of the client ID with the specified modification parameters:
  - `<new_price>`: Modified price of the order.
  - `<new_size>`: Modified size of the order.

- **Cancel**:
  ```
  cancel <orig_cl_ord_id> <client_index> 
  ```
  Cancels an existing order using the ClOrdId.

- **Logout**:
  ```
  logout
  ```
  Logs out from the current FIX session.
  
- **List**:
  ```
  list
  ```
  Lists information about active orders.
  
- **Exit**:
  ```
  exit
  ```
  Exits the application.

---

## Script Flow

1. **Initialization**:
   - Parses environment and loads the appropriate FIX configuration file.
   - Initializes the FIX session and starts the FIX client in a separate thread.

2. **UI Interaction**:
   - Displays real-time session updates and allows interactive command input.

3. **Order Management**:
   - Sends, modifies, or cancels orders through the FIX protocol.

4. **API Integration**:
   - Retrieves client IDs securely from the external API using HMAC authentication.

---

## Security Features

- **ES256 Signatures**:
  FIX logons and auth API token requests with a web UI key file are signed with the ECDSA P-256 private key, which never leaves your machine. REST calls carry the short-lived JWT.

- **HMAC-SHA-256 Signatures**:
  With a legacy HMAC key, FIX logons and REST requests are signed using the `TRUEX_KEY_SECRET`.

- **Environment-Specific Configuration**:
  Each environment (`dev`, `uat`, `prod`) is isolated with its own FIX configuration and API endpoint.

---

## Notes

- This script is designed as a demo and may require additional customization for production use.
- Order modification and cancellation features are placeholders and require proper implementation for real-world trading.

---

## License

This script is provided "as-is" for educational and testing purposes. Ensure compliance with FIX protocol standards and trading regulations when using this tool.
