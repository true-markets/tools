import urwid
import uuid
import quickfix as fix
import quickfix50sp2 as fix50sp2
import requests
import threading
import queue
import os
import sys
import time
import hmac
import hashlib
import base64
import argparse
from datetime import datetime
from urllib.parse import urlparse

# =============================
# Helper functions
# =============================
def GetClientIds(fix, env, api_key_id, api_key_secret):
    # Define the constants for the headers
    HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
    HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
    HEADER_AUTH_TOKEN = "x-truex-auth-token"

    url = ""
    if env.lower() == "dev":
        # Dev shared
        #url = "http://10.10.10.11:10185/api/v1/client"
        # Dev local
        url = "http://10.10.10.11:9742/api/v1/client"
    elif env.lower() == "uat":
        url = "http://10.10.20.11:9742/api/v1/client"

    # Prepare the current timestamp and method
    auth_timestamp = str(int(time.time()))
    http_method = "GET"

    # Combine the values into a payload for HMAC
    parsed_url = urlparse(url)
    path = parsed_url.path
    payload = auth_timestamp + http_method + path

    # Create HMAC signature using the secret key
    hmac_key = api_key_secret.encode('utf-8')
    hmac_message = payload.encode('utf-8')
    hmac_digest = hmac.new(hmac_key, hmac_message, hashlib.sha256).digest()

    # Convert the HMAC result to base64
    auth_signature = base64.b64encode(hmac_digest).decode('utf-8')

    # Setup the headers for the request
    headers = {
        HEADER_AUTH_TIMESTAMP: auth_timestamp,
        HEADER_AUTH_SIGNATURE: auth_signature,
        HEADER_AUTH_TOKEN: api_key_id,
        "Content-Type": "application/json"
    }

    # Perform the GET request (or other HTTP methods as necessary)
    response = requests.get(url, headers=headers)

    # Check the response
    if response.status_code == 200:
        fix.message_queue.put("Success:", response.json())
    else:
        fix.message_queue.put(f"Failed with status code {response.status_code}: {response.text}")

    matching_ids = []
    # Loop through each entry in the response data
    for entry in response.json():
        matching_ids.append(entry['id'])

    # Check if a match was found
    if len(matching_ids) > 0:
        fix.message_queue.put(f"Found matching ID(s): {matching_ids}")
    else:
        fix.message_queue.put("No users for api_key_id found.")
    return matching_ids

def GeneratePassword(secret, sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id, username):
    # Step 1: Concatenate the fields to form the message
    message = str(sending_time) + str(msg_type) + str(msg_seq_num) + str(sender_comp_id) + str(target_comp_id) + str(username)

    # Step 2: Handle potential encoding issues
    try:
        message_bytes = message.encode('utf-8')
    except UnicodeEncodeError as e:
        # Optionally, remove invalid characters or log them
        message_bytes = message.encode('utf-8', 'ignore')  # or 'replace'

    # Step 3: Create the HMAC-SHA-256 signature
    secret_bytes = secret.encode('utf-8')
    hmac_sha256 = hmac.new(secret_bytes, message_bytes, hashlib.sha256)

    # Step 4: Encode the HMAC in base64
    signature = base64.b64encode(hmac_sha256.digest()).decode('utf-8')

    return signature


# =============================
# CommandEdit: Custom Edit Widget
# =============================
class CommandEdit(urwid.Edit):
    """
    A subclass of urwid.Edit that emits a 'done' signal when the user presses Enter.
    """
    signals = ['done']

    def __init__(self, caption='> ', edit_text=''):
        super().__init__(caption=caption, edit_text=edit_text)

    def keypress(self, size, key):
        if key in ('enter', 'return'):
            # Emit 'done' signal with current text
            urwid.emit_signal(self, 'done', self.edit_text)
            # Clear the input field
            self.edit_text = ''
            return None  # Indicate that the key has been handled
        else:
            return super().keypress(size, key)

# =============================
# FIX Application
# =============================
class FIXApp(fix.Application):
    """
    FIX Application subclass to handle FIX session events.
    Communicates with the UI via a thread-safe queue.
    """
    def __init__(self, message_queue, app_id):
        super().__init__()
        self.message_queue = message_queue
        self.sessionID = None
        self.app_id = app_id  # Identifier for the FIX session

    def onCreate(self, sessionID):
        self.message_queue.put(f"Session created: {sessionID}")

    def onLogon(self, sessionID):
        self.sessionID = sessionID
        self.clientIds = GetClientIds(self, self.env, self.apiKeyId, self.apiKeySecret)
        self.message_queue.put(f"Logon successful: {sessionID}")

    def onLogout(self, sessionID):
        self.message_queue.put(f"Logout: {sessionID}")
        self.sessionID = None

    def toAdmin(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        # Check if the message is a Logon message
        if msg_type.getValue() == fix.MsgType_Logon:
            self.sessionId = None

            # Grab key ID and secret from env vars
            self.apiKeyId = os.getenv("TRUEX_KEY_ID")
            self.apiKeySecret = os.getenv("TRUEX_KEY_SECRET")
            
            # Retrieve SendingTime from the header (Tag 52)
            sending_time =  datetime.utcnow().strftime('%Y%m%d-%H:%M:%S.%f')[:-3]
            # Retrieve other required fields
            msg_type = message.getHeader().getField(fix.MsgType()).getString()  # MsgType (35)
            msg_seq_num = message.getHeader().getField(fix.MsgSeqNum()).getString()  # MsgSeqNum (34)
            sender_comp_id = message.getHeader().getField(fix.SenderCompID()).getString()  # SenderCompID (49)
            target_comp_id = message.getHeader().getField(fix.TargetCompID()).getString()  # TargetCompID (56)

            # Generate the HMAC-SHA-256 signature for the password
            password = GeneratePassword(self.apiKeySecret, sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id, self.apiKeyId)
            
            message.getHeader().setField(52, sending_time)
            # Set ResetSeqNum
            message.setField(fix.ResetSeqNumFlag(True))
            # Set Username (Tag 553)
            message.setField(fix.Username(self.apiKeyId))  # Set tag 553
            # Set Password (Tag 554)
            message.setField(fix.Password(password))  # Set tag 554

            self.message_queue.put("Sending Logon message.")

    def fromAdmin(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        self.message_queue.put(f"Admin message received: {msg_type.getValue()} - {message}")

    def fromApp(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        if msg_type.getValue() == fix.MsgType_ExecutionReport:
            self.onExecutionReport(message)
    
    def toApp(self, message, sessionID):
        """
        Handle application-level messages sent from the counterparty.
        """
        try:
            # For this example, we'll just log the received message
            self.message_queue.put(f"Received application message: {message}")
            
            # If you need to process specific message types, do so here
            # Example:
            # if msg_type.getValue() == fix.MsgType_SomeOtherType:
            #     self.handle_some_other_type(message)
            
        except fix.FieldNotFound as e:
            self.message_queue.put(f"Field not found in message: {e}")
        except Exception as e:
            self.message_queue.put(f"Error in toApp: {e}")

    def onExecutionReport(self, message):
        exec_type = fix.ExecType()
        message.getField(exec_type)
        self.message_queue.put(f"Execution Report: ExecType={exec_type.getValue()} {message}")

    def send_order(self, side, price, size, client_id):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        message = fix50sp2.NewOrderSingle()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.Symbol("BTC-PYUSD"))
        message.setField(fix.Side(fix.Side_BUY))
        message.setField(fix.OrdType(fix.OrdType_LIMIT))
        message.setField(fix.TimeInForce(fix.TimeInForce_GOOD_TILL_CANCEL))

        message.setField(fix.Side(side))
        message.setField(fix.Price(price))
        message.setField(fix.OrderQty(size))

        # Add PartyIDs group
        party_group = fix50sp2.NewOrderSingle.NoPartyIDs()
        party_group.setField(fix.PartyID(client_id))
        party_group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(party_group)

        try:
            fix.Session.sendToTarget(message, self.sessionID)
            side_str = "BUY" if side == fix.Side_BUY else "SELL"
            self.message_queue.put(f"Order sent: Side={side_str}, Price={price}, Size={size}, Client ID={client_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

    def modify_order(self, new_price, new_size):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        # Placeholder for modification logic
        # In real scenarios, you need the OrigClOrdID to modify an existing order
        self.message_queue.put("Order modification feature is not implemented in this demo.")

    def cancel_order(self):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        # Placeholder for cancellation logic
        # In real scenarios, you need the OrigClOrdID to cancel an existing order
        self.message_queue.put("Order cancellation feature is not implemented in this demo.")

    def send_logout(self):
        if self.sessionID:
            logout = fix.Message()
            logout.getHeader().setField(fix.MsgType("5"))  # Logout message type
            try:
                fix.Session.sendToTarget(logout, self.sessionID)
                self.message_queue.put("Logout message sent.")
            except fix.SessionNotFound:
                self.message_queue.put("Failed to send logout: FIX session not found.")

# =============================
# FIX Interface: urwid UI
# =============================
class FIXInterface:
    """
    urwid-based terminal interface with three panes:
    - Top: Title bar
    - Middle: Output display
    - Bottom: Input field
    Communicates with the FIXApp via queues.
    """
    def __init__(self, message_queue, fix_app):
        self.message_queue = message_queue
        self.fix_app = fix_app

        # Create widgets
        self.header = urwid.Text("FIX Trading Tool - Interactive CLI", align='center')
        self.output = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.input = CommandEdit("> ")

        # Connect the 'done' signal from the input widget to the handler
        urwid.connect_signal(self.input, 'done', self.handle_command)

        # Frame layout
        self.frame = urwid.Frame(
            header=urwid.LineBox(self.header),
            body=urwid.LineBox(self.output, title="Output"),
            footer=urwid.LineBox(self.input, title="Input")
        )

        # Define palette for styling
        self.palette = [
            ('reversed', 'standout', ''),
        ]

        # Create the main loop
        self.loop = urwid.MainLoop(
            self.frame,
            palette=self.palette,
            unhandled_input=self.handle_global_input,
            handle_mouse=False
        )

        # Start a periodic callback to check the message queue
        self.loop.set_alarm_in(0.5, self.process_message_queue)

    def display_message(self, message):
        """
        Append a message to the output pane.
        """
        sanitized_msg = message.replace('\r', ' ').replace('\n', ' ').replace('\1', '^')
        self.output.body.append(urwid.Text(sanitized_msg))
        # Scroll to the bottom to show the latest message
        self.output.set_focus(len(self.output.body) - 1)

    def handle_command(self, command):
        """
        Handle the command entered by the user.
        """
        command = command.strip()
        if not command:
            return

        # Display the command in the output pane
        self.display_message(f"> {command}")

        # Execute the command
        self.execute_command(command)

        # Ensure the input widget remains focused
        self.frame.set_focus('footer')

    def execute_command(self, command):
        """
        Parse and execute the given command.
        """
        parts = command.split()
        if not parts:
            return

        cmd = parts[0].lower()

        if cmd == "help":
            self.help()
        elif cmd in ("exit", "quit"):
            self.exit()
        elif cmd == "order":
            if len(parts) != 5:
                self.display_message("Usage: order BUY|SELL <price> <size> <client_index>")
            else:
                try:
                    side_str = parts[1].upper()
                    price = float(parts[2])
                    size = float(parts[3])
                    client_index = int(parts[4])

                    if side_str not in ["BUY", "SELL"]:
                        self.display_message("Invalid side. Use BUY or SELL.")
                        return

                    if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                        self.display_message("Invalid client index.")
                        return

                    client_id = self.fix_app.clientIds[client_index]
                    side_enum = fix.Side_BUY if side_str == "BUY" else fix.Side_SELL

                    self.fix_app.send_order(side_enum, price, size, client_id)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: order BUY|SELL <price> <size> <client_index>")
        elif cmd == "modify":
            if len(parts) != 3:
                self.display_message("Usage: modify <new_price> <new_size>")
            else:
                try:
                    new_price = float(parts[1])
                    new_size = float(parts[2])
                    self.fix_app.modify_order(new_price, new_size)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: modify <new_price> <new_size>")
        elif cmd == "cancel":
            if len(parts) != 1:
                self.display_message("Usage: cancel")
            else:
                self.fix_app.cancel_order()
        elif cmd == "logout":
            if len(parts) != 1:
                self.display_message("Usage: logout")
            else:
                self.fix_app.send_logout()
        else:
            self.display_message(f"Unknown command: {cmd}")

    def help(self):
        """
        Display available commands.
        """
        help_text = (
            "Available FIX commands:\n"
            "  help                             Show this help message\n"
            "  order BUY|SELL <price> <size> <client_index>    Send a new order\n"
            "  modify <new_price> <new_size>                    Modify an existing order\n"
            "  cancel                                           Cancel an existing order\n"
            "  logout                                           Logout from FIX session\n"
            "  exit / quit                                      Exit the application"
        )
        for line in help_text.split('\n'):
            self.display_message(line)

    def exit(self):
        """
        Exit the application.
        """
        self.display_message("Exiting application...")
        self.fix_app.send_logout()
        raise urwid.ExitMainLoop()

    def handle_global_input(self, key):
        """
        Handle global key inputs, like Ctrl+C to exit.
        """
        if key in ('ctrl c', 'ctrl C'):
            self.fix_app.send_logout()
            raise urwid.ExitMainLoop()

    def process_message_queue(self, loop, user_data):
        """
        Periodically check the message queue for new messages from FIXApp and display them.
        """
        while not self.message_queue.empty():
            message = self.message_queue.get_nowait()
            self.display_message(message)
        # Schedule the next check
        loop.set_alarm_in(0.5, self.process_message_queue)

    def run(self):
        """
        Run the main loop.
        """
        # Ensure input is focused at the start
        self.frame.set_focus('footer')
        self.loop.run()

# =============================
# FIX Session Runner
# =============================
def run_fix_session(config_file, fix_app, message_queue):
    """
    Initialize and run the FIX session.
    """
    try:
        settings = fix.SessionSettings(config_file)
        # Persist session between runs
        #store_factory = fix.FileStoreFactory(settings)a
        # Reset session between runs
        store_factory = fix.MemoryStoreFactory()
        log_factory = fix.FileLogFactory(settings)
        initiator = fix.SocketInitiator(fix_app, store_factory, settings, log_factory)
        initiator.start()

        # Keep the thread alive while the session is active
        while True:
            time.sleep(1)
    except Exception as e:
        message_queue.put(f"FIX session error: {e}")
    finally:
        initiator.stop()

# =============================
# Main Function
# =============================
def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="FIX Trading Tool with Interactive CLI")
    parser.add_argument("--env", choices=["dev", "uat", "prod"], required=True, help="Environment to run the FIX client in")
    args = parser.parse_args()

    # Determine FIX configuration file based on environment
    env = args.env
    fix_config_path = ""
    if env.lower() == "dev":
        fix_config_path = "fix_config_dev.cfg"
    elif env.lower() == "uat":
        fix_config_path = "fix_config_uat.cfg"
    elif env.lower() == "prod":
        fix_config_path = "fix_config_prod.cfg"

    if not os.path.exists(fix_config_path):
        print(f"FIX configuration file not found for environment '{env}' at: {fix_config_path}")
        sys.exit(1)

    # Create a thread-safe queue for messages from FIXApp to UI
    message_queue = queue.Queue()

    # Initialize the FIX application
    fix_app = FIXApp(message_queue, app_id="FIX_Client")
    fix_app.env = env

    # Start the FIX session in a separate daemon thread
    fix_thread = threading.Thread(target=run_fix_session, args=(fix_config_path, fix_app, message_queue), daemon=True)
    fix_thread.start()

    # Initialize the UI interface
    interface = FIXInterface(message_queue, fix_app)

    # Run the UI
    interface.run()

# =============================
# Entry Point
# =============================
if __name__ == "__main__":
    main()

