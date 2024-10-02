import logging
import os
import random
import shutil
import threading
import time

import quickfix as fix

from client import Application


class SimulatedTradingApplication(Application):
    def __init__(self, api_key_id, api_key_secret, mnemonic):
        super().__init__(api_key_id, api_key_secret, mnemonic)

    def onLogon(self, session_id):
        super().onLogon(session_id)
        self.start_simulation()

    def start_simulation(self):
        threading.Thread(target=self.simulation_loop, daemon=True).start()

    def simulation_loop(self):
        while True:
            action = random.choices(['place', 'modify', 'cancel'], weights=[70, 20, 10])[0]
            if action == 'place':
                self.simulate_new_order()
            elif action == 'modify':
                self.simulate_order_modification()
            else:
                self.simulate_order_cancellation()
            time.sleep(random.uniform(10, 15))  # Wait 10-15 seconds between actions

    @staticmethod
    def generate_simulated_order_params():
        # Generate a price in the range 9999.00 to 10001.00 with increments of 0.01
        price = round(random.randint(999900, 1000100) / 100.0, 2)

        # Generate a quantity in the range 0.2 to 0.3 with increments of 0.0001
        quantity = round(random.randint(2000, 3000) / 10000.0, 4)

        return price, quantity

    def simulate_new_order(self):
        side = random.choice([fix.Side_BUY, fix.Side_SELL])
        order_type = random.choices([fix.OrdType_LIMIT, fix.OrdType_MARKET], weights=[70, 30])[0]
        price, quantity = self.generate_simulated_order_params()

        self.send_order(self.sessionID, side, price, quantity, order_type)

    def simulate_order_modification(self):
        if not self.lastClOrdId:
            return  # No order to modify

        # Ensure at least one attribute is modified
        while True:
            modify_price = random.choice([True, False])
            modify_quantity = random.choice([True, False])
            modify_order_type = random.choice([True, False])

            if modify_price or modify_quantity or modify_order_type:
                break

        new_price, new_quantity = self.generate_simulated_order_params()
        new_price = new_price if modify_price else None
        new_quantity = new_quantity if modify_quantity else None
        new_order_type = random.choice([fix.OrdType_LIMIT, fix.OrdType_MARKET]) if modify_order_type else None

        self.modify_order(self.sessionID, new_price, new_quantity, new_order_type)

    def simulate_order_cancellation(self):
        if not self.lastClOrdId:
            return  # No order to cancel
        self.cancel_order(self.sessionID)

def run_simulated_trading_client(user_number):
    config_file = f'client{user_number}.cfg'
    api_key_id = os.getenv(f"TRUEX_USER{user_number}_KEY_ID")
    api_key_secret = os.getenv(f"TRUEX_USER{user_number}_KEY_SECRET")
    mnemonic = os.getenv(f"TRUEX_USER{user_number}_MNEMONIC")

    try:
        settings = fix.SessionSettings(config_file)
        application = SimulatedTradingApplication(api_key_id, api_key_secret, mnemonic)
        store_factory = fix.FileStoreFactory(settings)
        log_factory = fix.FileLogFactory(settings)
        initiator = fix.SocketInitiator(application, store_factory, settings, log_factory)

        initiator.start()
        return application, initiator
    except (fix.ConfigError, fix.RuntimeError) as e:
        logging.info("Error starting client with config %s: %s", config_file, e)
        return None, None


# Cleanup function to remove all log_ and store_ folders
def cleanup():
    current_directory = os.getcwd()
    for folder_name in os.listdir(current_directory):
        # Check if the item is a directory and starts with 'log_' or 'store_'
        if os.path.isdir(folder_name) and (folder_name.startswith('log') or folder_name.startswith('store')):
            # Remove the directory and all its contents
            shutil.rmtree(folder_name)
            print(f"Removed folder: {folder_name}")


def main():
    cleanup()

    user_numbers = ['1', '2']
    running_clients = []

    for user_number in user_numbers:
        app, initiator = run_simulated_trading_client(user_number)
        if app and initiator:
            running_clients.append((app, initiator))

    if running_clients:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("\nCaught KeyboardInterrupt, shutting down...")
        finally:
            for app, initiator in running_clients:
                app.send_logout()
                initiator.stop()
    else:
        logging.info("Failed to start any clients.")

if __name__ == '__main__':
    main()
