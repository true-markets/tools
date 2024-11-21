# Get Client IDs Script

This Python script retrieves client IDs associated with an API key using a secure HMAC authentication mechanism. It interacts with a REST API and processes responses to identify matching client IDs.

---

## Features
- Connects to a REST API endpoint to retrieve client IDs.
- Uses HMAC-SHA-256 for secure API authentication.
- Parses server details and environment variables for dynamic configuration.
- Provides detailed error handling and response output.

---

## Requirements

### Dependencies
The script requires the following Python libraries:
- `requests`
- `argparse`
- `os`
- `hmac`
- `hashlib`
- `base64`
- `time`
- `urllib`

Install dependencies via `pip`:
```
pip install requests
```

### Environment Variables
Set the following environment variables to enable secure API interactions:
- `TRUEX_KEY_ID`: API key ID for authentication.
- `TRUEX_KEY_SECRET`: API secret key for signing requests.

Example:
```
export TRUEX_KEY_ID="your_key_id"
export TRUEX_KEY_SECRET="your_secret_key"
```

---

## Setup

1. **Configure the REST Endpoint**
   - Ensure the target API server is accessible (default: `uat.truex.co:9742`).
   - Update the script's default server or provide the `--server` argument at runtime.

2. **Environment Variables**
   - Store the `TRUEX_KEY_ID` and `TRUEX_KEY_SECRET` securely as environment variables.

---

## Usage

Run the script using the following command:

```
python get_client_ids.py --server <host:port>
```

### Arguments
- **`--server`**:
  - Specifies the host and port of the REST API (default: `uat.truex.co:9742`).

### Example Command
```
python get_client_ids.py --server dev.truex.co:9742
```

---

## Script Flow

1. **Argument Parsing**:
   - The script parses the `--server` argument to determine the API endpoint.

2. **HMAC Authentication**:
   - Combines the HTTP method, timestamp, and path to create a secure HMAC signature.
   - Encodes the signature in base64 for inclusion in request headers.

3. **API Request**:
   - Sends a `GET` request to the REST API endpoint with the appropriate headers.

4. **Response Handling**:
   - Checks the HTTP status code.
   - If successful, parses and prints client IDs from the JSON response.
   - Handles errors and provides detailed output for debugging.

---

## Example Output

### Success
```
Success: [{"id": "12345", "name": "Example Client"}]
Found matching ID: 12345
```

### Failure
```
Failed with status code 401: Unauthorized
No matching api_key_id found.
```

---

## Security Features

- **HMAC-SHA-256 Signatures**:
  - Ensures secure API authentication using the `TRUEX_KEY_SECRET`.

- **Environment-Specific Configuration**:
  - Allows dynamic server specification via the `--server` argument.

---

## Notes

- Ensure that the REST API endpoint is active and accessible from the system running the script.
- The script assumes the API response is JSON-formatted. Update handling logic if the response format changes.

---

## License

This script is provided "as-is" for educational and testing purposes. Ensure compliance with data security standards and organizational policies when using this tool.

