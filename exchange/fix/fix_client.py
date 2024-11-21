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
def QueryRest(ctx, method, path, body = None):
    # Define the constants for the headers
    HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
    HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
    HEADER_AUTH_TOKEN = "x-truex-auth-token"

    url = ""
    if ctx.env.lower() == "dev":
        url = "http://dev1.truex.co:9742"
    elif ctx.env.lower() == "uat":
        url = "http://uat.truex.co:9742"

    url += path

    # Prepare the current timestamp and method
    auth_timestamp = str(int(time.time()))
    http_method = method

    # Combine the values into a payload for HMAC
    parsed_url = urlparse(url)
    path = parsed_url.path
    payload = auth_timestamp + http_method.upper() + path

    # Create HMAC signature using the secret key
    hmac_key = ctx.apiKeySecret.encode('utf-8')
    hmac_message = payload.encode('utf-8')
    hmac_digest = hmac.new(hmac_key, hmac_message, hashlib.sha256).digest()

    # Convert the HMAC result to base64
    auth_signature = base64.b64encode(hmac_digest).decode('utf-8')

    # Setup the headers for the request
    headers = {
        HEADER_AUTH_TIMESTAMP: auth_timestamp,
        HEADER_AUTH_SIGNATURE: auth_signature,
        HEADER_AUTH_TOKEN: ctx.apiKeyId,
        "Content-Type": "application/json"
    }

    # Perform the GET request (or other HTTP methods as necessary)
    response = None
    if method == "get":
        response = requests.get(url, headers=headers)
    elif method == "post":
        response = requests.post(url, headers=headers, json=body)
    elif method == "put":
        response = requests.put(url, headers=headers, json=body)
    elif method == "delete":
        response = requests.delete(url, headers=headers)

    # Check the response
    if response == None:
        ctx.message_queue.put("Unsupported method used: ", method)
        return None
    elif response.status_code != 200:
            ctx.message_queue.put(f"Failed {method} query on {url} with status code {response.status_code}: {response.text}")
            return None

    return response.json()

def GetInstrumentIds(ctx):
    response = QueryRest(ctx, "get", "/api/v1/instrument")

    # Loop through each entry in the response data
    if not isinstance(response, list):
        return []

    instruments = {}
    for instrument in response:
        key = instrument['id']
        instruments[key] = instrument

    return instruments

def GetClientIds(ctx):
    response = QueryRest(ctx, "get", "/api/v1/client")

    matching_ids = []
    # Loop through each entry in the response data
    if not isinstance(response, list):
        return []

    for entry in response:
        matching_ids.append(entry['id'])

    # Check if a match was found
    if len(matching_ids) > 0:
        ctx.message_queue.put(f"Found matching ID(s): {matching_ids}")
    else:
        ctx.message_queue.put("No users for apiKeyId found.")
    return matching_ids

def GetOrders(ctx):
    # Perform the GET request (or other HTTP methods as necessary)
    response = QueryRest(ctx, "get", "/api/v1/order")

    if isinstance(response, list) and len(response) > 0:
        i = 0
        for entry in response:
            inst_id = entry['order_info']['instrument_id']
            symbol = ctx.instrumentIds[inst_id]['info']['symbol']
            ctx.message_queue.put(f"Order {i}: {entry['external_id']} {entry['order_info']['side']:<4} {entry['order_info']['qty']} {symbol} @ {entry['order_info']['type']} {entry['order_info']['price']} {entry['order_info']['tif']} | {entry['status']} PQ:{entry['pending_qty']} LQ:{entry['leaves_qty']} EQ:{entry['executed_qty']} VWAP:{entry['executed_vwap']}")
            i+=1
    else:
        ctx.message_queue.put("No orders found.")

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
        self.clientIds = GetClientIds(self)
        self.instrumentIds = GetInstrumentIds(self)
        if (sessionID != None):
            self.message_queue.put(f"Logon successful: {sessionID}")
        else:
            self.message_queue.put("Logon failed!")

    def onLogout(self, sessionID):
        self.message_queue.put(f"Logout: {sessionID}")
        self.sessionID = None

    def toAdmin(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        # Check if the message is a Logon message
        if msg_type.getValue() == fix.MsgType_Logon:
            self.sessionId = None

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

    def list_orders(self):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        GetOrders(self)

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

    def modify_order(self, orig_cl_ord_id, client_id, new_price, new_qty):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        message = fix50sp2.OrderCancelReplaceRequest()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.OrigClOrdID(str(orig_cl_ord_id)))
        message.setField(fix.Price(new_price))
        message.setField(fix.OrderQty(new_qty))
        message.setField(fix.OrdType(fix.OrdType_LIMIT))

        # Add PartyIDs group
        party_group = fix50sp2.OrderCancelReplaceRequest.NoPartyIDs()
        party_group.setField(fix.PartyID(client_id))
        party_group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(party_group)

        try:
            fix.Session.sendToTarget(message, self.sessionID)
            self.message_queue.put(f"Modify sent: OrigClOrdId={orig_cl_ord_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

    def cancel_order(self, orig_cl_ord_id, client_id):
        if not self.sessionID:
            self.message_queue.put("No active FIX session.")
            return

        message = fix50sp2.OrderCancelRequest()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.OrigClOrdID(str(orig_cl_ord_id)))

        # Add PartyIDs group
        party_group = fix50sp2.OrderCancelRequest.NoPartyIDs()
        party_group.setField(fix.PartyID(client_id))
        party_group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(party_group)

        try:
            fix.Session.sendToTarget(message, self.sessionID)
            self.message_queue.put(f"Cancel sent: OrigClOrdId={orig_cl_ord_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

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
        elif cmd == "list":
            if len(parts) != 1:
                self.display_message("Usage: list")
            else:
                self.fix_app.list_orders()
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
            if len(parts) < 5:
                self.display_message("Usage: modify <orig_cl_ord_id> <client_index> <new_price> <new_size>")
            else:
                try:
                    orig_cl_ord_id = parts[1]
                    client_index = int(parts[2])
                    new_price = float(parts[3])
                    new_qty = float(parts[4])

                    # Validate at least one parameter is provided
                    if new_price is None or new_qty is None:
                        self.display_message("Parameters must be set: price and qty.")
                        return
    
                    client_id = self.fix_app.clientIds[client_index]

                    # Call the modify_order method with the parsed parameters
                    self.fix_app.modify_order(orig_cl_ord_id, client_id, new_price, new_qty)
                except ValueError as e:
                    self.display_message(f"Invalid parameters: {e}. Usage: modify <orig_cl_ord_id> <client_index> <new_price> <new_size>")
        elif cmd == "cancel":
            if len(parts) != 3:
                self.display_message("Usage: cancel <orig_cl_ord_id> <client_index>")
            else:
                try:
                    orig_cl_ord_id = parts[1]
                    client_index = int(parts[2])

                    client_id = self.fix_app.clientIds[client_index]

                    self.fix_app.cancel_order(orig_cl_ord_id, client_id)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: cancel <orig_cl_ord_id> <client_index>")
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
            "  help                                                           Show this help message\n"
            "  list                                                           List active orders\n"
            "  order <BUY|SELL> <price> <size> <client_index>                 Send a new order\n"
            "  modify <orig_cl_ord_id> <client_index> <new_price> <new_size>  Modify an existing order\n"
            "  cancel <orig_cl_ord_id> <client_index>                         Cancel an existing order\n"
            "  logout                                                         Logout from FIX session\n"
            "  exit / quit                                                    Exit the application"
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
    # Grab key ID and secret from env vars
    fix_app.apiKeyId = os.getenv("TRUEX_KEY_ID")
    fix_app.apiKeySecret = os.getenv("TRUEX_KEY_SECRET")
            

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

