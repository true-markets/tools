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
        """Start a new thread to run the trading simulation."""
        threading.Thread(target=self.simulation_loop, daemon=True).start()

    def simulation_loop(self):
        """Simulate trading actions in a loop with random delays."""
        while True:
            action = random.choices(['place', 'modify', 'cancel'], weights=[70, 20, 10])[0]
            if action == 'place':
                self.simulate_new_order()
            elif action == 'modify':
                self.simulate_order_modification()
            else:
                self.simulate_order_cancellation()
            time.sleep(random.uniform(7, 10))  # Wait 10-15 seconds between actions

    @staticmethod
    def generate_simulated_order_params():
        """Generate random parameters for a new order within valid price/quantity ranges."""
        price_range = INSTRUMENT["reference_price"] * 0.001  # 0.1% of reference price
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
        """Simulate placing a new order."""
        side = random.choice([fix.Side_BUY, fix.Side_SELL])
        order_type = random.choices([fix.OrdType_LIMIT, fix.OrdType_MARKET], weights=[70, 30])[0]
        price, quantity = self.generate_simulated_order_params()

        self.send_order(self.sessionId, side, price, quantity, order_type)

    def simulate_order_modification(self):
        """Simulate modifying an existing order."""
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
        """Simulate canceling an order."""
        if not self.lastClOrdId:
            return  # No order to cancel
        self.cancel_order(self.sessionId)


def load_config_and_settings(config_file):
    """Load the configuration and session settings from the config file."""
    settings = fix.SessionSettings(config_file)
    default_settings = settings.get()
    begin_str = default_settings.getString(fix.BEGINSTRING)
    target_comp_id = default_settings.getString(fix.TARGETCOMPID)
    return settings, begin_str, target_comp_id


def run_simulated_trading_client(mnemonic):
    """Initialize and run a simulated trading client."""
    config_file = 'client.cfg'
    api_key_id = os.getenv("TRUEX_CLIENT_API_KEY_ID")
    api_key_secret = os.getenv("TRUEX_CLIENT_API_KEY_SECRET")

    if not api_key_id or not api_key_secret:
        logging.error("API key ID or secret not set.")
        return None, None

    try:
        settings, begin_str, target_comp_id = load_config_and_settings(config_file)
        # Create a session ID with the given mnemonic
        session_id = fix.SessionID(begin_str, f"{mnemonic}_8", target_comp_id)
        settings.set(session_id, fix.Dictionary())

        application = SimulatedTradingApplication(api_key_id, api_key_secret, mnemonic)
        store_factory = fix.MemoryStoreFactory()
        log_factory = fix.FileLogFactory(f"log/{mnemonic}")
        initiator = fix.SocketInitiator(application, store_factory, settings, log_factory)

        initiator.start()
        return application, initiator

    except (fix.ConfigError, fix.RuntimeError) as e:
        logging.error("Error starting client with config %s: %s", config_file, e)
        return None, None


def cleanup_logs():
    """Cleanup log directories before running the simulation."""
    log_dirs = [folder for folder in os.listdir(os.getcwd()) if os.path.isdir(folder) and folder.startswith('log')]
    for log_dir in log_dirs:
        shutil.rmtree(log_dir)
        logging.info(f"Removed folder: {log_dir}")


def handle_shutdown(signum, frame, stop_event):
    """Handle graceful shutdown on receiving a signal."""
    logging.info(f"Received signal {signal.Signals(signum).name}, initiating shutdown...")
    stop_event.set()


def main():
    cleanup_logs()

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: handle_shutdown(s, f, stop_event))
    signal.signal(signal.SIGTERM, lambda s, f: handle_shutdown(s, f, stop_event))

    mnemonics = os.getenv("TRUEX_CLIENT_MNEMONICS", "").split(",")
    running_clients = [run_simulated_trading_client(mnemonic) for mnemonic in mnemonics]

    running_clients = [(app, initiator) for app, initiator in running_clients if app and initiator]

    if running_clients:
        try:
            while not stop_event.is_set():
                stop_event.wait(1)
        finally:
            logging.info("Shutting down simulation...")
            for app, initiator in running_clients:
                app.send_logout()
                initiator.stop(True)
            logging.info("All clients have been shut down.")
    else:
        logging.info("Failed to start any clients.")


if __name__ == '__main__':
    main()
