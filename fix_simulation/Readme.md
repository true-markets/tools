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
TRUEX_USER1_MNEMONIC=your_user1_mnemonic
TRUEX_USER1_KEY_ID=your_user1_key_id
TRUEX_USER1_KEY_SECRET=your_user1_key_secret
TRUEX_USER2_MNEMONIC=your_user2_mnemonic
TRUEX_USER2_KEY_ID=your_user2_key_id
TRUEX_USER2_KEY_SECRET=your_user2_key_secret
```

Replace the placeholder values with your actual credentials.

3. Ensure that the `client1.cfg` and `client2.cfg` files are present and correctly configured.

## Running the Trade Simulation
To run the trade simulation using Docker Compose:

1. Open a terminal and navigate to the project directory.

2. Run the following command: `docker-compose up --build`


## TRUEX Environments
1. Development - 10.10.10.11
2. User Acceptance Test(UAT) - 10.10.20.11

Replace `SocketConnectHost` in `client1.cfg` and `client2.cfg` with the appropriate values for the environment you are connecting to.<br>
Replace `TRUEX_API_ADDRESS` in `docker-compose.yml` with the appropriate value for the environment you are connecting to.

## Troubleshooting

If you encounter any issues with permissions or file access, ensure that the necessary files are in the correct locations and have the proper permissions.
Check the logs for any error messages that might indicate configuration issues or connection problems.

To exit the container and stop the simulation, run `docker stop truex_fix_trade_simulation` in a separate terminal window or press `Ctrl+C` in the terminal where the `docker-compose up` command was run. <br>
Might have to update the SENDER_COMP_ID after each restart of the container, as the fix session might not have logged out properly.

Additional Information

The main simulation logic is in trade_simulation.py. <br>
The FIX client implementation is in client.py. <br>
Configuration for each client is in client1.cfg and client2.cfg. <br>

For more detailed information about the implementation or to modify the simulation behavior, refer to the comments in the source code files.
