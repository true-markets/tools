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
import math
import random
from datetime import datetime
from urllib.parse import urlparse

fix_stop_event = threading.Event()

# =============================
# Helper functions
# =============================
def QueryRest(ctx, method, path, body = None):
    # Define the constants for the headers
    HEADER_AUTH_TIMESTAMP = "x-truex-auth-timestamp"
    HEADER_AUTH_SIGNATURE = "x-truex-auth-signature"
    HEADER_AUTH_TOKEN = "x-truex-auth-token"

    url = ""
    if ctx.env.lower() == "local":
        host = os.getenv("TRUEX_HOST")
        port = os.getenv("TRUEX_REST_PORT")
        url = f"http://{host}:{port}"
    elif ctx.env.lower() == "dev":
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
    response = QueryRest(ctx, "get", "/api/v1/order/active")

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
        super().__init__(caption=caption, edit_text=edit_text )
        self.history = []
        self.history_index = None
        self._saved_edit_text = ''

    def keypress(self, size, key):
        if key in ('enter', 'return'):
            # Emit 'done' signal with current text
            self.history.append(self.edit_text)  # Add to history
            self.history_index = None  # Reset history index
            self._saved_edit_text = ''  # Reset saved text

            urwid.emit_signal(self, 'done', self.edit_text)
            # Clear the input field
            self.edit_text = ''
            self.set_edit_pos(0)
            return None  # Indicate that the key has been handled

        elif key == 'ctrl a':
            # Move to the beginning of the line
            self.set_edit_pos(0)
            return None

        elif key == 'ctrl e':
            # move to the end of the line
            self.set_edit_pos(len(self.edit_text))
            return None

        elif key == 'ctrl u':
            # Clear the command prompt and reset history navigation.
            self.set_edit_text('')
            self.set_edit_pos(0)
            self.history_index = None
            return None

        # if key is up arrow or down arrow cycle through history
        elif key == 'up':
            if len(self.history) > 0:
                if self.history_index is None:
                    self._saved_text = self.edit_text
                    self.history_index = len(self.history) - 1
                # If not at the oldest command, move further back.
                elif self.history_index > 0:
                    self.history_index -= 1
                # Set the edit field to the command at the current history index.
                self.set_edit_text(self.history[self.history_index])
                self.set_edit_pos(len(self.history[self.history_index]))
            return None
        elif key == 'down':
            if self.history_index is not None:
                # If not at the most recent history item, move forward.
                if self.history_index < len(self.history) - 1:
                    self.history_index += 1
                    self.set_edit_text(self.history[self.history_index])
                    self.set_edit_pos(len(self.history[self.history_index]))
                else:
                    # Once at the latest command, restore the saved text and reset index.
                    self.history_index = None
                    self.set_edit_text(self._saved_text)
                    self.set_edit_pos(len(self._saved_text))
            return None
        else:
            # Any other key resets history navigation.
            if self.history_index is not None:
                self.history_index = None
            return super().keypress(size, key)

# =============================
# Custom Market Data Widget
# =============================
class MarketDataWidget(urwid.WidgetWrap):
    def __init__(self, symbol):
        self.symbol = symbol
        self.buy_qty = 0
        self.buy_price = 0.0
        self.sell_qty = 0
        self.sell_price = 0.0

        # Create a text widget displaying header, separator, and market data.
        self.text_widget = urwid.Text(self._get_market_data_text(), align="center")
        # Wrap the text widget in a LineBox with the symbol as the title.
        line_box = urwid.LineBox(self.text_widget)
        super().__init__(line_box)

    def _get_market_data_text(self):
        # Define a header line.
        header = f"{'Symbol':<10} {'Bid Qty':>9} {'Bid Price':>13}  x  {'Ask Price':<13} {'Ask Qty':<9}"
        # Create a separator line matching the header's length.
        dash_line = '-' * len(header)
        # Define the data row with proper formatting.
        data_line = f"{self.symbol:<10} {self.buy_qty:>9.6f} {self.buy_price:>13.6f}  x  {self.sell_price:<13.6f} {self.sell_qty:<9.6f}"
        # Combine the header, separator, and data row.
        return f"{header}\n{dash_line}\n{data_line}"

    def update_market_data(self, side, qty, price):
        """
        Update the market data and refresh the display.
        """
        if side == "BID":
            self.buy_qty = qty
            self.buy_price = price
        elif side == "OFFER":
            self.sell_qty = qty
            self.sell_price = price
        elif side == "OFFER/TRADE":
            self.sell_qty -= qty
        elif side == "BID/TRADE":
            self.buy_qty -= qty
        # Update the displayed text.
        self.text_widget.set_text(self._get_market_data_text())

class MarketDataPanel(urwid.Pile):
    def __init__(self, dividechars=1):
        # Start with an empty list of columns.
        self.widgets = {}
        self.columns = []
        self.items = []
        self.dividechars = dividechars
        super().__init__(self.items)


    def render(self, size, focus=False):
        maxcol = size[0]
        rows = []
        row = []
        space_left = maxcol

        # if no widgets, display text message
        if not self.items:
            # Display a fallback message when the terminal is too small.
            warning = urwid.Text(
                '\nNo market data available.\n\nSee "subscribe" command to receive market data.\n',
                align='center'
            )
            self.contents = [(warning, ('pack', None))]
            return super().render(size, focus)

        # Calculate the max width required by the child widgets.
        child_width = max([w.pack()[0] for w in self.items])
        for widget in self.items:
            if space_left < child_width and row:
                # Add current row as Columns to rows
                rows.append(urwid.Columns(row, dividechars=self.dividechars))
                row = []
                space_left = maxcol

            row.append(widget)
            space_left -= (child_width + self.dividechars)

        if row:
            rows.append(urwid.Columns(row, dividechars=self.dividechars))

        # Rebuild Pile contents
        self.contents = [(r, ('pack', None)) for r in rows]
        return super().render(size, focus)

    def AddMarketData(self, symbol, side, qty, price):
        """
        Add a new market data widget to the columns.
        """
        new_widget = MarketDataWidget(symbol)
        new_widget.update_market_data(side, qty, price)
        self.widgets[symbol] = new_widget
        # Append the new widget to our local items list.
        self.items.append(new_widget)

    def UpdateMarketData(self, market_data):
        return self._updateMarketData(*market_data)

    def _updateMarketData(self, symbol, side, qty, price):
        """
        Update the market data for a specific symbol.
        """
        if symbol in self.widgets:
            # Update the widget's data.
            self.widgets[symbol].update_market_data(side, qty, price)
        else:
            # If the symbol is not found, add a new widget.
            self.AddMarketData(symbol, side, qty, price)
            self._invalidate()


# =============================
# FIX Application
# =============================
class FIXApp(fix.Application):
    """
    FIX Application subclass to handle FIX session events.
    Communicates with the UI via a thread-safe queue.
    """
    def __init__(self, message_queue, market_data_queue, app_id):
        super().__init__()
        self.message_queue = message_queue
        self.market_data_queue = market_data_queue
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
        self.message_queue.put(f"<TX< {message}")

    def fromAdmin(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        self.message_queue.put(f">RX> {message}")

    def fromApp(self, message, sessionID):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        if msg_type.getValue() == fix.MsgType_ExecutionReport:
            self.onExecutionReport(message)
        elif msg_type.getValue() == fix.MsgType_MarketDataSnapshotFullRefresh:
            self.onMarketDataSnapshotFullRefresh(message)
        elif msg_type.getValue() == fix.MsgType_MarketDataIncrementalRefresh:
            self.onMarketDataIncrementalRefresh(message)
        self.message_queue.put(f">RX> {message}")

    def toApp(self, message, sessionID):
        """
        Handle application-level messages about to be sent to the counterparty.
        """
        # For this example, we'll just log the message about to be sent
        self.message_queue.put(f"<TX< {message}")
        # optionally can throw DoNotSend if message should not
        # be sent to the  counterparty

    def onExecutionReport(self, message):
        exec_type = fix.ExecType()
        message.getField(exec_type)

    def onMarketDataSnapshotFullRefresh(self, message):
        symbol = fix.Symbol()
        message.getField(symbol)
        self.message_queue.put(f"Market Data Snapshot: Symbol={symbol.getValue()}")
        self.parse_market_data_refresh(message)

    def onMarketDataIncrementalRefresh(self, message):
        self.parse_market_data_incr(message)

    def parse_market_data_refresh(self, message):
        """
        Parse and process a Market Data Incremental message.
        """
        symbol = fix.Symbol()
        no_md_entries = fix.NoMDEntries()
        message.getField(symbol)
        message.getField(no_md_entries)

        num_entries = int(no_md_entries.getValue())
        # Iterate over each market data entry
        for i in range(1, num_entries + 1):
            group = fix50sp2.MarketDataSnapshotFullRefresh.NoMDEntries()
            message.getGroup(i, group)

            md_entry_type = fix.MDEntryType()
            md_entry_px = fix.MDEntryPx()
            md_entry_size = fix.MDEntrySize()

            if group.isSetField(md_entry_type):
                group.getField(md_entry_type)
            if group.isSetField(md_entry_px):
                group.getField(md_entry_px)
            if group.isSetField(md_entry_size):
                group.getField(md_entry_size)

            md_type = None
            if md_entry_type.getValue() == fix.MDEntryType_BID:
                md_type = "BID"
            elif md_entry_type.getValue() == fix.MDEntryType_OFFER:
                md_type = "OFFER"

            if md_type:
                self.market_data_queue.put((symbol.getValue(), md_type, md_entry_size.getValue(), md_entry_px.getValue()))

    def parse_market_data_incr(self, message):
        """
        Parse and process a Market Data Incremental message.
        """
        no_md_entries = fix.NoMDEntries()
        message.getField(no_md_entries)

        num_entries = int(no_md_entries.getValue())
        # Iterate over each market data entry
        for i in range(1, num_entries + 1):
            group = fix50sp2.MarketDataIncrementalRefresh.NoMDEntries()
            message.getGroup(i, group)

            symbol = fix.Symbol()
            md_update_type = fix.MDUpdateType()
            md_entry_type = fix.MDEntryType()
            md_entry_px = fix.MDEntryPx()
            md_entry_size = fix.MDEntrySize()

            group.getField(symbol)
            if group.isSetField(md_update_type):
                group.getField(md_update_type)
            if group.isSetField(md_entry_type):
                group.getField(md_entry_type)
            if group.isSetField(md_entry_px):
                group.getField(md_entry_px)
            if group.isSetField(md_entry_size):
                group.getField(md_entry_size)

            md_type = None
            if md_entry_type.getValue() == fix.MDEntryType_BID:
                md_type = "BID"
            elif md_entry_type.getValue() == fix.MDEntryType_OFFER:
                md_type = "OFFER"
            elif md_entry_type.getValue() == fix.MDEntryType_TRADE:
                md_aggressor_side = group.getField(2446)
                if md_aggressor_side == "1": # buy
                    md_type = "OFFER/TRADE"
                elif md_aggressor_side == "2": # sell
                    md_type = "BID/TRADE"

            if md_type:
                self.market_data_queue.put((symbol.getValue(), md_type, md_entry_size.getValue(), md_entry_px.getValue()))

    #staticmethod
    def ensure_session_id(method):
        def wrapper(self, *args, **kwargs):
            if not self.sessionID:
                self.message_queue.put("No active FIX session.")
                return
            return method(self, *args, **kwargs)
        return wrapper

    @ensure_session_id
    def list_orders(self):
        GetOrders(self)

    @ensure_session_id
    def send_order(self, symbol, side, price, size, client_id):
        message = fix50sp2.NewOrderSingle()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.Symbol(symbol))
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
            self.message_queue.put(f"Order sent: Symbol={symbol} Side={side_str} Price={price} Size={size} Client ID={client_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

    @ensure_session_id
    def modify_order(self, orig_cl_ord_id, client_id, new_price, new_qty):
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

    @ensure_session_id
    def cancel_order(self, orig_cl_ord_id, client_id):
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

    @ensure_session_id
    def subscribe_to_market_data(self, md_req_id, symbol):
        message = fix50sp2.MarketDataRequest()
        message.setField(fix.MDReqID(md_req_id))
        message.setField(
            fix.SubscriptionRequestType(
                fix.SubscriptionRequestType_SNAPSHOT_PLUS_UPDATES
            )
        )
        message.setField(fix.MarketDepth(0))
        related_sym = fix50sp2.MarketDataRequest.NoRelatedSym()
        related_sym.setField(fix.Symbol(symbol))
        message.addGroup(related_sym)

        try:
            fix.Session.sendToTarget(message, self.sessionID)
            self.message_queue.put(f"Subscribed to market data: {symbol}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to subscribe to market data: FIX session not found.")

    @ensure_session_id
    def unsubscribe_from_market_data(self, md_req_id):
        message = fix50sp2.MarketDataRequest()
        message.setField(fix.MDReqID(md_req_id))
        message.setField(
            fix.SubscriptionRequestType(
                fix.SubscriptionRequestType_DISABLE_PREVIOUS_SNAPSHOT_PLUS_UPDATE_REQUEST
            )
        )
        message.setField(fix.MarketDepth(0))

        try:
            fix.Session.sendToTarget(message, self.sessionID)
            self.message_queue.put(f"Unsubscribed from market data: {md_req_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to unsubscribe from market data: FIX session not found.")

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
    def __init__(self, message_queue, market_data_queue, fix_app):
        self.message_queue = message_queue
        self.market_data_queue = market_data_queue
        self.fix_app = fix_app

        # Create widgets
        self.header = urwid.Text("FIX Trading Tool - Interactive CLI", align='center')
        self.output = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.md = MarketDataPanel()
        self.md_output = urwid.LineBox(self.md, title="Market Data")
        self.main_pane = urwid.Pile([self.output])
        self.input = CommandEdit()

        # Session data
        self.client_index = -1

        # Connect the 'done' signal from the input widget to the handler
        urwid.connect_signal(self.input, 'done', self.handle_command)

        # Frame layout
        self.frame = urwid.Frame(
            header=urwid.LineBox(self.header),
            body=urwid.LineBox(self.main_pane, title="Output"),
            footer=urwid.LineBox(self.input, title="Input"),
        )

        # Define palette for styling
        self.palette = [
            ('reversed', 'standout', ''),
        ]

        # Create the main loop
        self.loop = urwid.MainLoop(
            urwid.Padding(self.frame, align='center', left=1, right=1),
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
        elif cmd == "md" or cmd == "market_data":
            if (len(self.main_pane.contents) > 1):
                self.main_pane.contents.pop(0)
            else:
                self.main_pane.contents.insert(0, (self.md_output, ('pack', None)))
        elif cmd in ("exit", "quit"):
            self.exit()
        elif cmd == "use":
            if len(parts) != 3:
                self.display_message("Usage: use <client> <idx>")
            else:
                if parts[1].lower() == "client":
                    try:
                        client_index = int(parts[2])
                        if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                            self.display_message("Invalid client index.")
                            return
                        self.client_index = client_index
                        self.display_message(f"Using client ID: {self.fix_app.clientIds[client_index]} at index {client_index}")
                    except ValueError:
                        self.display_message("Invalid client index value.")
                else:
                    self.display_message("Unknown parameter: use <client> <idx>")
        elif cmd == "list":
            if len(parts) != 1:
                self.display_message("Usage: list")
            else:
                self.fix_app.list_orders()
        elif cmd == "buy" or cmd == "sell":
            if len(parts) < 4 or len(parts) > 5:
                self.display_message("Usage: buy|sell <symbol> <price> <size> [client_index]")
            else:
                try:
                    symbol = parts[1].upper()
                    # allow for price to be a range via double dot notation,
                    # i.e 100..105 will generate 6 orders 100, 101, 102, 103, 104, 105
                    # increment based on the number of digits after the dot
                    if ".." in parts[2]:
                        price_range = parts[2].split("..")
                        price_start = float(price_range[0])
                        price_end = float(price_range[1])
                        price_increment = 1 if price_start < price_end else -1
                        price_digits = 0
                        price_step = 1
                        if "." in price_range[0]:
                            price_digits = len(price_range[0].split(".")[1])
                            price_step = price_step / float("0." + price_range[0].split(".")[1])

                        # calculate how man orders would be generated
                        total_orders = abs(int((price_end - price_start) / price_step))
                        if total_orders > 100:
                            self.display_message("Price range is too large. Limited to 100 orders.")
                            return

                        price = price_start
                        while (price_increment == 1 and price <= price_end) or (price_increment == -1 and price >= price_end):
                            size = float(parts[3])
                            client_index = self.client_index if len(parts) == 4 else int(parts[4])
                            if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                                self.display_message("Invalid client index.")
                                return
                            client_id = self.fix_app.clientIds[client_index]
                            side_enum = fix.Side_BUY if cmd == "buy" else fix.Side_SELL
                            self.fix_app.send_order(symbol, side_enum, price, size, client_id)
                            price += (price_increment * price_step)
                            price = round(price, price_digits)
                    else:
                        price = float(parts[2])
                        size = float(parts[3])
                        client_index = self.client_index if len(parts) == 4 else int(parts[4])

                        if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                            self.display_message("Invalid client index.")
                            return

                        client_id = self.fix_app.clientIds[client_index]
                        side_enum = fix.Side_BUY if cmd == "buy" else fix.Side_SELL

                        self.fix_app.send_order(symbol, side_enum, price, size, client_id)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: buy|sell <symbol> <price> <size> [client_index]")
        elif cmd == "modify":
            if len(parts) < 4 or len(parts) > 5:
                self.display_message("Usage: modify <orig_cl_ord_id> <new_price> <new_size> [client_index]")
            else:
                try:
                    orig_cl_ord_id = parts[1]
                    new_price = float(parts[2])
                    new_qty = float(parts[3])
                    client_index = self.client_index if len(parts) == 4 else int(parts[4])

                    if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                        self.display_message("Invalid client index.")
                        return

                    # Validate at least one parameter is provided
                    if new_price is None or new_qty is None:
                        self.display_message("Parameters must be set: price and qty.")
                        return

                    client_id = self.fix_app.clientIds[client_index]

                    # Call the modify_order method with the parsed parameters
                    self.fix_app.modify_order(orig_cl_ord_id, client_id, new_price, new_qty)
                except ValueError as e:
                    self.display_message(f"Invalid parameters: {e}. Usage: modify <orig_cl_ord_id> <new_price> <new_size> [client_index] ")
        elif cmd == "cancel":
            if len(parts) < 2 or len(parts) > 3:
                self.display_message("Usage: cancel <orig_cl_ord_id> [client_index]")
            else:
                try:
                    orig_cl_ord_id = parts[1]
                    client_index = self.client_index if len(parts) == 2 else int(parts[2])

                    if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                        self.display_message("Invalid client index.")
                        return

                    client_id = self.fix_app.clientIds[client_index]
                    self.fix_app.cancel_order(orig_cl_ord_id, client_id)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: cancel <orig_cl_ord_id> <client_index>")
        elif cmd == "subscribe":
            if len(parts) != 3:
                self.display_message("Usage: subscribe <md_req_id> <symbol>")
            else:
                md_req_id = parts[1]
                symbol = parts[2]
                # Send a subscription request
                self.fix_app.subscribe_to_market_data(md_req_id, symbol)
        elif cmd == "unsubscribe":
            if len(parts) != 2:
                self.display_message("Usage: unsubscribe <md_req_id>")
            else:
                md_req_id = parts[1]
                # Send an unsubscription request
                self.fix_app.unsubscribe_from_market_data(md_req_id)
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
            "  use <client> <idx>                                             Use a specific value for subsequent commands\n"
            "  list                                                           List active orders\n"
            "  market_data / md                                               Display market data\n"
            "  buy|sell <symbol> <price> <size> [client_index]                Send a new order\n"
            "  modify <orig_cl_ord_id> <new_price> <new_size> [client_index]  Modify an existing order\n"
            "  cancel <orig_cl_ord_id> [client_index]                         Cancel an existing order\n"
            "  subscribe <md_req_id> <symbol>                                 Subscribe to market data\n"
            "  unsubscribe <md_req_id>                                        Unsubscribe from market data\n"
            "  logout                                                         Logout from FIX session\n"
            "  exit / quit                                                    Exit the application\n\n"
            "Note: arguments in square brackets are optional if set using the 'use' command"
        )
        for line in help_text.split('\n'):
            self.display_message(line)

    def exit(self):
        """
        Exit the application.
        """
        self.display_message("Exiting application...")
        self.fix_app.send_logout()
        fix_stop_event.set()
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
        while not self.market_data_queue.empty():
            market_data = self.market_data_queue.get_nowait()
            self.md.UpdateMarketData(market_data)

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
        while not fix_stop_event.is_set():
            time.sleep(1)

        raise Exception("Stopping FIX session...")
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
    parser.add_argument("--env", choices=["local", "dev", "uat", "prod"], required=True, help="Environment to run the FIX client in")
    args = parser.parse_args()

    # Determine FIX configuration file based on environment
    env = args.env
    fix_config_path = ""
    if env.lower() == "local":
        fix_config_path = "fix_config_local.cfg"
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
    market_data_queue = queue.Queue()

    # Initialize the FIX application
    fix_app = FIXApp(message_queue, market_data_queue, app_id="FIX_Client")
    fix_app.env = env
    # Grab key ID and secret from env vars
    fix_app.apiKeyId = os.getenv("TRUEX_KEY_ID")
    fix_app.apiKeySecret = os.getenv("TRUEX_KEY_SECRET")


    # Start the FIX session in a separate thread
    fix_thread = threading.Thread(target=run_fix_session, args=(fix_config_path, fix_app, message_queue), daemon=False)
    fix_thread.start()

    # Initialize the UI interface
    interface = FIXInterface(message_queue, market_data_queue, fix_app)

    # Run the UI
    interface.run()
    fix_thread.join()

# =============================
# Entry Point
# =============================
if __name__ == "__main__":
    main()

