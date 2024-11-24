# Custodian API Client Script

This Python script is designed to interact with an API using OAuth2 for authentication and supports various HTTP methods such as `GET`, `POST`, `PUT`, `DELETE`, and `PATCH`. It is useful for testing and automating API interactions.

## Features
- Obtain an OAuth2 token using client credentials.
- Make API requests with the token for authentication.
- Supports multiple HTTP methods (`GET`, `POST`, `PUT`, `DELETE`, `PATCH`).
- Configurable via environment variables for flexibility.

## Requirements

### Dependencies
This script requires the following Python libraries:
- `requests`
- `argparse`
- `os`
- `json`

You can install these dependencies using `pip`:
```
pip install requests
```

### Environment Variables
The script relies on the following environment variables:
- **`API_KEY_ID`**: The API key ID provided by your service.
- **`API_KEY_SECRET`**: The API key secret corresponding to the API key ID.
- **`API_KEY_SCOPE`**: The scope of the API key.
- **`BASE_URL`** (optional): The base URL of the API. Defaults to `https://api.sandbox.paxos.com`.

## Usage

### Running the Script
To use the script, you must specify the HTTP method and API path. Optionally, you can include a request body for `POST`, `PUT` or `PATCH` methods.

```
python paxos.py --method METHOD --path PATH [--body BODY]
```

### Arguments
- **`--method`** (required): The HTTP method to use (`get`, `post`, `put`,`delete`, or `patch`).
- **`--path`** (required): The API path (e.g., `/v2/conversion/stablecoin`).
- **`--body`** (optional): The JSON-formatted request body for `POST`, `PUT` or `PATCH` methods.

### Example Commands

#### 1. **Without Body (GET Request)**
Fetch a resource from the API:
```
python paxos.py --method get --path /v2/profiles
```

#### 2. **With Body (POST Request)**
Send data to the API:
```
python paxos.py --method post --path /v2/sandbox/profiles/cac2c7cb-2c79-47aa-acb6-3b6957708fa6/deposit --body '{"asset": "PYUSD", "amount": "1", "crypto_network": "ETHEREUM"}'
```

## Script Flow

1. **Environment Variable Validation**:
   - The script checks if `API_KEY_ID`, `API_KEY_SECRET`, and `API_KEY_SCOPE` are set. If not, it exits with an error.

2. **Token Acquisition**:
   - The script obtains an OAuth2 token by making a `POST` request to the `/oauth2/token` endpoint.

3. **API Request Execution**:
   - Using the token, the script sends the specified request to the API endpoint.

4. **Response Handling**:
   - The script outputs the status code and response body to the console.

## Error Handling
- Missing or invalid environment variables.
- Failed token acquisition.
- Unsupported HTTP methods.
- API response errors.

## Notes
- Ensure that your API credentials and environment variables are correctly configured.
- This script is designed for testing and small-scale automation. For large-scale use, additional error handling and optimizations may be required.

## License
This script is provided "as-is" and is intended for educational and testing purposes. Use at your own risk.
