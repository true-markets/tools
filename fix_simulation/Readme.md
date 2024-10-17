# Trade Simulation
This project contains a simulated trading application that interacts with a FIX (Financial Information eXchange) server. It simulates placing, modifying, and canceling orders for two clients.

## Prerequisites
- Docker and Docker Compose installed on your system
- Python 3.7 or higher

## Setup
1. Clone the `true-markets/tools` repository, `git clone git@github.com:true-markets/tools.git` and cd into the tools directory.
2. Install the required git submodules: `git submodule update --init --recursive`
3. Navigate to the `trade_simulation` directory: `cd tools/trade_simulation`
3. Create a `.env` file in the root directory of the project with the following content:

```
TRUEX_CLIENT_MNEMONICS=your_client_mnemonics (comma-separated)
TRUEX_CLIENT_API_KEY_ID=your_client_api_key_id
TRUEX_CLIENT_API_KEY_SECRET=your_client_api_key_secret
```

Replace the placeholder values with your actual credentials.<br>
The `TRUEX_CLIENT_MNEMONICS` should be a comma-separated list of client mnemonics that you want to use for the simulation. e.g client1,client2<br>
The `TRUEX_CLIENT_API_KEY_ID` and `TRUEX_CLIENT_API_KEY_SECRET` are the API key ID and secret for the clients.
For the simulation, use the same API key and secret for both clients.
The client mnemonics must be registered clients in the TRUEX system and have the necessary permissions to access the FIX server.


## Running the Trade Simulation
To run the trade simulation using Docker Compose:

1. Open a terminal and navigate to the project directory.

2. Run the following command: `docker-compose up --build`


## TRUEX Environments
2. User Acceptance Test(UAT) -`FIX SocketConnectHost=uat1.truex.co`, `SocketConnectPort=19484`
3. User Acceptance Test(UAT) -`REST API Address=http://uat1.truex.co:9742`


## Troubleshooting

If you encounter any issues with permissions or file access, ensure that the necessary files are in the correct locations and have the proper permissions.
Check the logs for any error messages that might indicate configuration issues or connection problems.

To exit the container and stop the simulation, run `docker stop truex_fix_trade_simulation` in a separate terminal window or press `Ctrl+C` in the terminal where the `docker-compose up` command was run. <br>
Consider updating the `SENDER_COMP_ID` if issues persist with the connection to the FIX server.

Additional Information

The main simulation logic is in trade_simulation.py. <br>
The FIX client implementation is in client.py. <br>
Configuration for each client is in client1.cfg and client2.cfg. <br>

For more detailed information about the implementation or to modify the simulation behavior, refer to the comments in the source code files.
