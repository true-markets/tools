import logging
import os
import random
import shutil
import signal
import threading
import time

import quickfix as fix

from client import Application, INSTRUMENT


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
        # Generate a price within ±1% of the reference price, using quote_increment
        price_range = INSTRUMENT["reference_price"] * 0.01  # 1% of reference price
        min_price = INSTRUMENT["reference_price"] - price_range
        max_price = INSTRUMENT["reference_price"] + price_range

        # Calculate the number of possible price points
        price_steps = round((max_price - min_price) / INSTRUMENT["quote_increment"])

        # Generate a random price within the range
        random_step = random.randint(0, price_steps)
        price = round(min_price + (random_step * INSTRUMENT["quote_increment"]), 2)

        # Ensure the price doesn't exceed max_price due to rounding
        price = min(price, max_price)

        # Generate a quantity between 0.2 and 0.3, using base_increment
        min_quantity = 0.2
        max_quantity = 0.3
        quantity_steps = int((max_quantity - min_quantity) / INSTRUMENT["base_increment"])
        quantity = round(min_quantity + (random.randint(0, quantity_steps) * INSTRUMENT["base_increment"]), 4)

        return price, quantity

    def simulate_new_order(self):
        side = random.choice([fix.Side_BUY, fix.Side_SELL])
        order_type = random.choices([fix.OrdType_LIMIT, fix.OrdType_MARKET], weights=[70, 30])[0]
        price, quantity = self.generate_simulated_order_params()

        self.send_order(self.sessionId, side, price, quantity, order_type)

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

        self.modify_order(self.sessionId, new_price, new_quantity, new_order_type)

    def simulate_order_cancellation(self):
        if not self.lastClOrdId:
            return  # No order to cancel
        self.cancel_order(self.sessionId)

def run_simulated_trading_client(user_number):
    config_file = f'client{user_number}.cfg'
    api_key_id = os.getenv(f"TRUEX_USER{user_number}_KEY_ID")
    api_key_secret = os.getenv(f"TRUEX_USER{user_number}_KEY_SECRET")
    mnemonic = os.getenv(f"TRUEX_USER{user_number}_MNEMONIC")

    try:
        settings = fix.SessionSettings(config_file)
        application = SimulatedTradingApplication(api_key_id, api_key_secret, mnemonic)
        # Use MemoryStoreFactory instead of FileStoreFactory to avoid storing session state
        store_factory = fix.MemoryStoreFactory()
        log_factory = fix.FileLogFactory(settings)
        initiator = fix.SocketInitiator(application, store_factory, settings, log_factory)

        initiator.start()
        return application, initiator
    except (fix.ConfigError, fix.RuntimeError) as e:
        logging.info("Error starting client with config %s: %s", config_file, e)
        return None, None


# Cleanup function to remove `log` folder
def cleanup():
    current_directory = os.getcwd()
    for folder_name in os.listdir(current_directory):
        if os.path.isdir(folder_name) and (folder_name.startswith('log')):
            # Remove the directory and all its contents
            shutil.rmtree(folder_name)
            print(f"Removed folder: {folder_name}")


def main():
    cleanup()

    user_numbers = ['1', '2']
    running_clients = []
    stop_event = threading.Event()

    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logging.info(f"\nCaught signal {signum} ({sig_name}), initiating shutdown...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for user_number in user_numbers:
        app, initiator = run_simulated_trading_client(user_number)
        if app and initiator:
            running_clients.append((app, initiator))

    if running_clients:
        try:
            while not stop_event.is_set():
                stop_event.wait(1)
        finally:
            logging.info("Shutting down simulation...")
            for app, initiator in running_clients:
                app.send_logout()
                initiator.stop(True)  # True means wait for stop to complete

            logging.info("All clients have been shut down.")
    else:
        logging.info("Failed to start any clients.")


if __name__ == '__main__':
    main()
