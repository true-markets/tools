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
import bisect
import locale
import re
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
    if ctx.env == "local":
        host = os.getenv("TRUEX_HOST")
        port = os.getenv("TRUEX_REST_PORT")
        url = f"http://{host}:{port}"
    elif ctx.env == "dev":
        url = "http://dev1.truex.co:9742"
    elif ctx.env == "uat":
        url = "http://uat.truex.co:9742"
    elif ctx.env == "prod":
        url = "https://prod.truex.co"

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

class Quote:
    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size

    def __lt__(self, other):
        return self.price < other.price

    def __repr__(self):
        return f"Quote(price={self.price}, size={self.size})"

class QuoteQueue:
    def __init__(self, ascending=True):
        self.quotes = []
        self.price_map = {}  # price -> Quote
        self.ascending = ascending

    def _sort_key(self, price):
        return price if self.ascending else -price

    def add_or_update(self, price: float, size: float):
        if price in self.price_map:
            order = self.price_map[price]
            order.size = size
            if size == 0:
                self.remove(price)
        else:
            if size == 0:
                return
            order = Quote(price, size)
            bisect.insort(self.quotes, order if self.ascending else Quote(-price, size))
            self.price_map[price] = order

    def remove(self, price: float):
        if price in self.price_map:
            order = self.price_map.pop(price)
            key = price if self.ascending else -price
            index = next((i for i, o in enumerate(self.quotes) if o.price == key), None)
            if index is not None:
                self.quotes.pop(index)

    def head(self):
        if not self.quotes:
            return Quote(0.0, 0.0)
        order = self.quotes[0]
        return self.price_map[abs(order.price)] if not self.ascending else order

    def top_n(self, n=5):
        result = []
        for i in range(n):
            if i < len(self.quotes):
                order = self.quotes[i]
                actual_price = abs(order.price) if not self.ascending else order.price
                actual_order = self.price_map.get(actual_price, Quote(0.0, 0.0))
                result.append(actual_order)
            else:
                result.append(Quote(0.0, 0.0))
        return result

    def __repr__(self):
        return repr([self.price_map[abs(o.price)] if not self.ascending else o for o in self.quotes])

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
# TabbedPane: Simple Tab Widget
# =============================
class TabbedPane(urwid.WidgetWrap):
    def __init__(self, tabs, active=None, on_change=None):
        if not tabs:
            raise ValueError("TabbedPane requires at least one tab.")

        self._tab_order = [name for name, _ in tabs]
        self._tab_widgets = {name: widget for name, widget in tabs}
        self._tab_buttons = {}
        self._on_change = on_change
        self.active_tab = active or self._tab_order[0]
        self._header = urwid.Columns([], dividechars=1)
        self._body = urwid.WidgetPlaceholder(self._tab_widgets[self.active_tab])

        super().__init__(urwid.Pile([('pack', self._header), self._body]))
        self._build_header()

    def _build_header(self):
        contents = []
        for name in self._tab_order:
            button = urwid.Button(name)
            urwid.connect_signal(
                button,
                'click',
                self._on_tab_selected,
                user_args=[name],
            )
            attr = 'tab_active' if name == self.active_tab else 'tab_inactive'
            contents.append(
                (
                    urwid.AttrMap(button, attr, focus_map='reversed'),
                    self._header.options('weight', 1),
                )
            )
            self._tab_buttons[name] = button
        self._header.contents = contents

    def _on_tab_selected(self, tab_name, button):
        if tab_name == self.active_tab:
            return

        self.activate(tab_name)

    def activate(self, tab_name):
        if tab_name not in self._tab_widgets:
            raise ValueError(f"Unknown tab: {tab_name}")

        self.active_tab = tab_name
        self._body.original_widget = self._tab_widgets[tab_name]
        self._build_header()
        self._notify_tab_change()

    def activate_next(self):
        idx = self._tab_order.index(self.active_tab)
        self.activate(self._tab_order[(idx + 1) % len(self._tab_order)])

    def activate_previous(self):
        idx = self._tab_order.index(self.active_tab)
        self.activate(self._tab_order[(idx - 1) % len(self._tab_order)])

    def _notify_tab_change(self):
        if callable(self._on_change):
            self._on_change(self.active_tab)

# =============================
# Custom Market Data Widget
# =============================
class MarketDataWidget(urwid.WidgetWrap):
    def __init__(self, symbol):
        self.symbol = symbol
        self.buys = QuoteQueue(ascending=False)
        self.sells = QuoteQueue(ascending=True)

        # Create a text widget displaying header, separator, and market data.
        self.text_widget = urwid.Text(self._get_market_data_text(), align="center")
        # Wrap the text widget in a LineBox with the symbol as the title.
        line_box = urwid.LineBox(self.text_widget)
        super().__init__(line_box)

    def _get_market_data_text(self):
        # Define a header line.
        header = f"{'Symbol':<10} {'Bid Qty':>10} {'Bid Price':>13}  x  {'Ask Price':<13} {'Ask Qty':<10}"
        # Create a separator line matching the header's length.
        dash_line = '-' * len(header)
        first = True;
        for (bid, offer) in zip(self.buys.top_n(5), self.sells.top_n(5)):
            if first:
                data_line = f"{self.symbol:<10} {bid.size:>10.6f} {bid.price:>13.6f}  x  {offer.price:<13.6f} {offer.size:<10.6f}\n"
                first = False
            else:
                data_line += f"{'':<10} {bid.size:>10.6f} {bid.price:>13.6f}  x  {offer.price:<13.6f} {offer.size:<10.6f}\n"
        # add last line with book mid point and spread
        # align mid and spread to the right of the last line
        data_line += f"{'':<10} mid: {((self.buys.head().price + self.sells.head().price) / 2):>13.6f}  |  spread: {(self.sells.head().price - self.buys.head().price):>10.6f}"

        # Combine the header, separator, and data row.
        return f"{header}\n{dash_line}\n{data_line}"

    def remove_market_data(self, side, qty, price):
        """
        Remove market data for a specific side, quantity, and price.
        """
        if side == "BID":
            self.buys.remove(price)
        elif side == "OFFER":
            self.sells.remove(price)
        # Update the displayed text.
        self.text_widget.set_text(self._get_market_data_text())

    def update_market_data(self, side, qty, price):
        """
        Update the market data and refresh the display.
        """
        if side == "BID":
            self.buys.add_or_update(price, qty)
        elif side == "OFFER":
            self.sells.add_or_update(price, qty)
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
        self._invalidate()

    def RemoveMarketData(self, symbol):
        """
        Remove a market data widget by its symbol.
        """
        if symbol in self.widgets:
            # Remove the widget from the dictionary and items list.
            del self.widgets[symbol]
            self.items = [w for w in self.items if w.symbol != symbol]
            # Invalidate the layout to trigger a re-render.
            self._invalidate()
        else:
            raise ValueError(f"Market data for symbol {symbol} does not exist.")

    def UpdateMarketData(self, market_data):
        return self._updateMarketData(*market_data)

    def _updateMarketData(self, symbol, action, side, qty, price):
        """
        Update the market data for a specific symbol.
        """
        if symbol in self.widgets:
            if action == 'UNSUB':
                self.RemoveMarketData(symbol)
            elif action == 'DELETE':
                self.widgets[symbol].remove_market_data(side, qty, price)
            else:
                self.widgets[symbol].update_market_data(side, qty, price)
        else:
            # If the symbol is not found, add a new widget.
            self.AddMarketData(symbol, side, qty, price)

class InstrumentWidget(urwid.WidgetWrap):
    def __init__(self, symbol, notional=0.0, volume=0.0):
        self.symbol = symbol
        self.notional = notional
        self.volume = volume
        self.text_widget = urwid.Text("", align="center")
        line_box = urwid.LineBox(self.text_widget, title=f"{symbol}", title_align="center")
        super().__init__(line_box)

    def _get_instrument_text(self):
        def _add_commas_to_wholepart(num_str):
            return re.sub(r"(?<=\d)(?=(\d{3})+$)", ",", num_str)
        notional = self.notional
        if (not '.' in notional):
            notional = f"${notional}.00"
        else:
            notional = f"${notional}"
        # add comma for thousands if necessary without converting to a float
        if notional.count('.') == 1:
            parts = notional.split('.')
            parts[0] = _add_commas_to_wholepart(parts[0])
            notional = '.'.join(parts)

        # Format the text to display notional and volume.
        return f"24hr notional: {notional}\n24hr volume: {self.volume}"

    def update_instrument(self, notional, volume):
        self.notional = notional
        self.volume = volume
        self.text_widget.set_text(self._get_instrument_text())

class InstrumentPanel(urwid.Pile):
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
                '\nNo instruments available.\n\nSee "instrument" command to receive instruments data.\n',
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

    def AddInstrument(self, symbol, notional=0.0, volume=0.0):
        """
        Add a new instrument widget to the columns.
        """
        new_widget = InstrumentWidget(symbol, notional, volume)
        new_widget.update_instrument(notional, volume)
        self.widgets[symbol] = new_widget
        # Append the new widget to our local items list.
        self.items.append(new_widget)
        self._invalidate()

    def ModifyInstrument(self, action, symbol, notional=0.0, volume=0.0):
        """
        Modify an existing instrument widget.
        """
        if symbol in self.widgets:
            # Update the existing widget's notional and volume.
            self.widgets[symbol].update_instrument(notional, volume)
            # Invalidate the layout to trigger a re-render.
            self._invalidate()
        else:
            raise ValueError(f"Instrument for symbol {symbol} does not exist.")

    def RemoveInstrument(self, symbol):
        """
        Remove an instrument widget by its symbol.
        """
        if symbol in self.widgets:
            # Remove the widget from the dictionary and items list.
            del self.widgets[symbol]
            self.items = [w for w in self.items if w.symbol != symbol]
            # Invalidate the layout to trigger a re-render.
            self._invalidate()
        else:
            raise ValueError(f"Instrument for symbol {symbol} does not exist.")

    def UpdateInstrument(self, instrument):
        self._UpdateInstrument(*instrument)

    def _UpdateInstrument(self, action, symbol, notional=0.0, volume=0.0):
        """
        Update the instrument data for a specific symbol.
        """
        if action == 'DELETE':
            self.RemoveInstrument(symbol)
        elif action == 'MODIFY':
            if symbol in self.widgets:
                self.ModifyInstrument(action, symbol, notional, volume)
            else:
                self.AddInstrument(symbol, notional, volume)
        else:
            # If the symbol is not found, add a new widget.
            self.AddInstrument(symbol, notional, volume)



# =============================
# FIX Application
# =============================
class FIXApp(fix.Application):
    """
    FIX Application subclass to handle FIX session events.
    Communicates with the UI via a thread-safe queue.
    """
    def __init__(self, message_queue, market_data_queue, instrument_data_queue, app_id):
        super().__init__()
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
        self.message_queue = message_queue
        self.market_data_queue = market_data_queue
        self.instrument_data_queue = instrument_data_queue
        self.sessions = {"TRUEX_LCL_GW": None, "TRUEX_DEV_GW": None, "TRUEX_UAT_GW": None, "TRUEX_PROD_GW": None}
        self.app_id = app_id  # Identifier for the FIX session
        self.reset_seq_num = True
        # request ids
        self.securityReqID_ = None

    def OrderEntrySession(self):
        if self.env == "local":
            return self.sessions.get("TRUEX_LCL_OE", self.sessions["TRUEX_LCL_GW"])
        if self.env == "dev":
           return self.sessions.get("TRUEX_DEV_OE", self.sessions["TRUEX_DEV_GW"])
        if self.env == "uat":
           return self.sessions.get("TRUEX_UAT_OE", self.sessions["TRUEX_UAT_GW"])
        if self.env == "prod":
           return self.sessions.get("TRUEX_PROD_OE", self.sessions["TRUEX_PROD_GW"])

        return None

    def MarketDataSession(self):
        if self.env == "local":
            return self.sessions.get("TRUEX_LCL_MD", self.sessions["TRUEX_LCL_GW"])
        if self.env == "dev":
           return self.sessions.get("TRUEX_DEV_MD", self.sessions["TRUEX_DEV_GW"])
        if self.env == "uat":
           return self.sessions.get("TRUEX_UAT_MD", self.sessions["TRUEX_UAT_GW"])
        if self.env == "prod":
           return self.sessions.get("TRUEX_PROD_MD", self.sessions["TRUEX_PROD_GW"])

        return None

    def onCreate(self, sessionID):
        self.sessions[sessionID.getTargetCompID().getValue()] = sessionID
        self.message_queue.put(f"Session created: {sessionID}")

    def onLogon(self, sessionID):
        self.sessions[sessionID.getTargetCompID().getValue()] = sessionID
        self.clientIds = GetClientIds(self)
        self.instrumentIds = GetInstrumentIds(self)
        for key, instrument in self.instrumentIds.items():
            self.message_queue.put(f"Instrument: {key} Symbol: {instrument['info']['symbol']}")
        if (sessionID != None):
            self.message_queue.put(f"Logon successful: {sessionID}")
        else:
            self.message_queue.put("Logon failed!")

        activeSessions = [k for k, v in self.sessions.items() if v is not None]
        self.message_queue.put(f"Active sessions: {len(activeSessions)} {activeSessions}")

    def onLogout(self, sessionID):
        self.message_queue.put(f"Logout: {sessionID}")
        self.sessions.pop(sessionID.getTargetCompID().getValue(), None)

    def toAdmin(self, message, sessionID):
         # Determine the message type
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        # Check if the message is a Logon message
        if msg_type.getValue() == fix.MsgType_Logon:
            self.message_queue.put(f"Preparing to send admin message {sessionID.toString()}")

            # Set ResetSeqNum
            if self.reset_seq_num:
                message.setField(fix.ResetSeqNumFlag(True))

            # Retrieve SendingTime from the header (Tag 52)
            sending_time =  datetime.utcnow().strftime('%Y%m%d-%H:%M:%S.%f')[:-3]
            # Retrieve other required fields
            msg_type = message.getHeader().getField(fix.MsgType()).getString()  # MsgType (35)
            msg_seq_num = 1 if self.reset_seq_num else message.getHeader().getField(fix.MsgSeqNum()).getString()  # MsgSeqNum (34)
            sender_comp_id = message.getHeader().getField(fix.SenderCompID()).getString()  # SenderCompID (49)
            target_comp_id = message.getHeader().getField(fix.TargetCompID()).getString()  # TargetCompID (56)
            self.message_queue.put(f"sending_time: {sending_time}, msg_type: {msg_type}, msg_seq_num: {msg_seq_num}, sender_comp_id: {sender_comp_id}, target_comp_id: {target_comp_id}")
            # Generate the HMAC-SHA-256 signature for the password
            password = GeneratePassword(self.apiKeySecret, sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id, self.apiKeyId)

            message.getHeader().setField(52, sending_time)
            # Set ResetSeqNum
            if self.reset_seq_num:
                message.setField(fix.ResetSeqNumFlag(True))
            # Set Username (Tag 553)
            message.setField(fix.Username(self.apiKeyId))  # Set tag 553
            # Set Password (Tag 554)
            message.setField(fix.Password(password))  # Set tag 554

            self.message_queue.put("Sending Logon message.")
        self.message_queue.put(f"<TX< {message}")

    def fromAdmin(self, message, sessionID):
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
        elif msg_type.getValue() == fix.MsgType_SecurityList:
            self.onSecurityList(message)
        elif msg_type.getValue() == fix.MsgType_SecurityListUpdateReport:
            self.onSecurityListUpdateReport(message)
        self.message_queue.put(f">RX> {message}")

    def toApp(self, message, sessionID):
        """
        Handle application-level messages about to be sent to the counterparty.
        """
        self.message_queue.put(f"<TX< {message}")
        # optionally can throw DoNotSend if message should not
        # be sent to the  counterparty

    def onExecutionReport(self, message):
        exec_type = fix.ExecType()
        try:
            message.getField(exec_type)
        except fix.FieldNotFound:
            self.message_queue.put("Execution report received without ExecType.")
            return

        exec_type_value = exec_type.getValue()

        def safe_get(field_cls):
            field = field_cls()
            try:
                message.getField(field)
                return field.getValue()
            except fix.FieldNotFound:
                return None

        cl_ord_id = safe_get(fix.ClOrdID)
        symbol = safe_get(fix.Symbol)
        side_value = safe_get(fix.Side)
        ord_status_value = safe_get(fix.OrdStatus)
        leaves_qty = safe_get(fix.LeavesQty)
        cum_qty = safe_get(fix.CumQty)
        avg_px = safe_get(fix.AvgPx)
        last_qty = safe_get(fix.LastQty)
        last_px = safe_get(fix.LastPx)
        text = safe_get(fix.Text)
        ord_rej_reason = safe_get(fix.OrdRejReason)

        side_map = {
            str(fix.Side_BUY): "BUY",
            str(fix.Side_SELL): "SELL",
        }
        side_label = side_map.get(str(side_value)) if side_value is not None else None

        ord_status_map = {
            str(fix.OrdStatus_NEW): "NEW",
            str(fix.OrdStatus_PARTIALLY_FILLED): "PARTIALLY_FILLED",
            str(fix.OrdStatus_FILLED): "FILLED",
            str(fix.OrdStatus_CANCELED): "CANCELED",
            str(fix.OrdStatus_PENDING_CANCEL): "PENDING_CANCEL",
            str(fix.OrdStatus_PENDING_NEW): "PENDING_NEW",
            str(fix.OrdStatus_PENDING_REPLACE): "PENDING_REPLACE",
            str(fix.OrdStatus_REJECTED): "REJECTED",
            str(fix.OrdStatus_DONE_FOR_DAY): "DONE_FOR_DAY",
            str(fix.OrdStatus_REPLACED): "REPLACED",
            str(fix.OrdStatus_EXPIRED): "EXPIRED",
        }
        ord_status_label = (
            ord_status_map.get(str(ord_status_value))
            if ord_status_value is not None
            else None
        )

        statuses = []
        extras = []

        trade_like_exec_types = {
            fix.ExecType_PARTIAL_FILL,
            fix.ExecType_FILL,
        }
        exec_type_trade = getattr(fix, "ExecType_TRADE", None)
        if exec_type_trade:
            trade_like_exec_types.add(exec_type_trade)

        trade_metrics = []

        def append_metric(label, value, target):
            if value is None:
                return
            target.append(f"{label}={value}")

        quantity_metrics = []
        append_metric("LeavesQty", leaves_qty, quantity_metrics)
        append_metric("CumQty", cum_qty, quantity_metrics)
        append_metric("AvgPx", avg_px, quantity_metrics)

        if exec_type_value == fix.ExecType_REJECTED:
            if text:
                extras.append(f"Reason={text}")
            if ord_rej_reason is not None:
                extras.append(f"RejCode={ord_rej_reason}")
        elif exec_type_value == fix.ExecType_PARTIAL_FILL:
            append_metric("LastQty", last_qty, trade_metrics)
            append_metric("LastPx", last_px, trade_metrics)
        elif exec_type_value == fix.ExecType_FILL:
            append_metric("LastQty", last_qty, trade_metrics)
            append_metric("LastPx", last_px, trade_metrics)
        elif exec_type_trade and exec_type_value == exec_type_trade:
            append_metric("LastQty", last_qty, trade_metrics)
            append_metric("LastPx", last_px, trade_metrics)

        if ord_status_label:
            statuses.append(f"OrdStatus={ord_status_label}")

        context_parts = []
        for label, value in (
            ("ClOrdID", cl_ord_id),
            ("Symbol", symbol),
            ("Side", side_label),
        ):
            if value is not None:
                context_parts.append(f"{label}={value}")

        if exec_type_value in trade_like_exec_types:
            trade_metrics.extend(quantity_metrics)
        else:
            trade_metrics = quantity_metrics + trade_metrics

        detail_sections = []
        if statuses:
            detail_sections.append(" ".join(statuses))
        if context_parts:
            detail_sections.append(" ".join(context_parts))
        if trade_metrics:
            detail_sections.append(" ".join(trade_metrics))
        if extras:
            detail_sections.append(" ".join(extras))

        if len(detail_sections) > 0:
            self.message_queue.put(" ".join(detail_sections))

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
                self.market_data_queue.put((symbol.getValue(), 0, md_type, md_entry_size.getValue(), md_entry_px.getValue()))

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
            md_action_type = fix.MDUpdateAction()

            group.getField(symbol)
            if group.isSetField(md_action_type):
                group.getField(md_action_type)
            if group.isSetField(md_update_type):
                group.getField(md_update_type)
            if group.isSetField(md_entry_type):
                group.getField(md_entry_type)
            if group.isSetField(md_entry_px):
                group.getField(md_entry_px)
            if group.isSetField(md_entry_size):
                group.getField(md_entry_size)

            action_type = None
            if md_action_type.getValue() == fix.MDUpdateAction_NEW:
                action_type = "NEW"
            elif md_action_type.getValue() == fix.MDUpdateAction_DELETE:
                action_type = "DELETE"
            elif md_action_type.getValue() == fix.MDUpdateAction_CHANGE:
                action_type = "CHANGE"

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
                self.market_data_queue.put((symbol.getValue(), action_type, md_type, md_entry_size.getValue(), md_entry_px.getValue()))

    def onSecurityList(self, message):
        security_req_id = fix.SecurityReqID()
        no_related_sym = fix.NoRelatedSym()
        message.getField(security_req_id)
        message.getField(no_related_sym)

        num_entries = int(no_related_sym.getValue())
        self.message_queue.put(f"Security List: ReqID={security_req_id.getValue()} NumEntries={num_entries}")
        # Iterate over each instrument attr entry
        for i in range(1, num_entries + 1):
            group = fix50sp2.SecurityList.NoRelatedSym()
            symbol = fix.Symbol()

            message.getGroup(i, group)
            group.getField(symbol)
            attrs = self._get_instr_attribs(group)
            self.message_queue.put(f"  Symbol={symbol.getValue()} NumAttrs={len(attrs)}")
            self.instrument_data_queue.put(("ADD", symbol.getValue(), attrs[40], attrs[41]))

    def onSecurityListUpdateReport(self, message):
        security_req_id = fix.SecurityReqID()
        no_related_sym = fix.NoRelatedSym()
        message.getField(security_req_id)
        message.getField(no_related_sym)

        num_entries = int(no_related_sym.getValue())
        self.message_queue.put(f"Security List Update: ReqID={security_req_id.getValue()} NumEntries={num_entries}")
        # Iterate over each market data entry
        for i in range(1, num_entries + 1):
            group = fix50sp2.SecurityListUpdateReport.NoRelatedSym()
            message.getGroup(i, group)
            symbol = fix.Symbol()
            group.getField(symbol)

            list_update_action = fix.ListUpdateAction()
            group.getField(list_update_action)

            action = None
            if list_update_action.getValue() == 'A':
                action = "ADD"
            elif list_update_action.getValue() == 'D':
                action = "DELETE"
            elif list_update_action.getValue() == 'M':
                action = "MODIFY"

            # Iterate over each instrument attr entry
            for i in range(1, num_entries + 1):
                group = fix50sp2.SecurityList.NoRelatedSym()
                symbol = fix.Symbol()

                message.getGroup(i, group)
                group.getField(symbol)
                attrs = self._get_instr_attribs(group)
                self.message_queue.put(f"  Symbol={symbol.getValue()} Action={action} NumAttrs={len(attrs)}")
                self.instrument_data_queue.put((action, symbol.getValue(), attrs[40], attrs[41]))


    def _get_instr_attribs(self, group):
        """
        Helper function to retrieve instrument attributes from a group.
        """
        num_attrs = int(group.getField(870))
        order = fix.IntArray(3)
        order[0] = 871
        order[1] = 872
        order[2] = 0
        attrs = {}
        for i in range(1, num_attrs + 1):
            attr_group = fix.Group(870, 871, order)
            group.getGroup(i, attr_group)
            attr_type = fix.InstrAttribType()
            attr_value = fix.InstrAttribValue()
            attr_group.getField(attr_type)
            attr_group.getField(attr_value)
            attrs[attr_type.getValue()] = attr_value.getValue()
        return attrs

    #staticmethod
    def ensure_session_id(method):
        def wrapper(self, *args, **kwargs):
            if len(self.sessions.keys()) <= 0:
                self.message_queue.put("No active FIX session.")
                return
            return method(self, *args, **kwargs)
        return wrapper

    @ensure_session_id
    def list_orders(self):
        GetOrders(self)

    @ensure_session_id
    def send_order(self, symbol, side, order_type, tif, price, size, client_id):
        message = fix50sp2.NewOrderSingle()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.Symbol(symbol))
        message.setField(fix.Side(fix.Side_BUY))
        if order_type == "MARKET":
            message.setField(fix.OrdType(fix.OrdType_MARKET))
        else:
            message.setField(fix.OrdType(fix.OrdType_LIMIT))

        if tif == "IOC":
            message.setField(fix.TimeInForce(fix.TimeInForce_IMMEDIATE_OR_CANCEL))
        else:
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
            fix.Session.sendToTarget(message, self.OrderEntrySession())
            side_str = "BUY" if side == fix.Side_BUY else "SELL"
            self.message_queue.put(f"Order sent: Symbol={symbol} Side={side_str} Price={price} Size={size} Client ID={client_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

    @ensure_session_id
    def modify_order(self, orig_cl_ord_id, client_id, new_price, new_qty, new_order_type=None):
        message = fix50sp2.OrderCancelReplaceRequest()
        message.setField(fix.ClOrdID(str(uuid.uuid4())))
        message.setField(fix.OrigClOrdID(str(orig_cl_ord_id)))
        message.setField(fix.Price(new_price))
        message.setField(fix.OrderQty(new_qty))
        if new_order_type == "MARKET":
            message.setField(fix.OrdType(fix.OrdType_MARKET))
        else:
            message.setField(fix.OrdType(fix.OrdType_LIMIT))

        # Add PartyIDs group
        party_group = fix50sp2.OrderCancelReplaceRequest.NoPartyIDs()
        party_group.setField(fix.PartyID(client_id))
        party_group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(party_group)

        try:
            fix.Session.sendToTarget(message, self.OrderEntrySession())
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
            fix.Session.sendToTarget(message, self.OrderEntrySession())
            self.message_queue.put(f"Cancel sent: OrigClOrdId={orig_cl_ord_id}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send order: FIX session not found.")

    @ensure_session_id
    def subscribe_to_market_data(self, md_req_id, symbol, depth):
        message = fix50sp2.MarketDataRequest()
        message.setField(fix.MDReqID(md_req_id))
        message.setField(
            fix.SubscriptionRequestType(
                fix.SubscriptionRequestType_SNAPSHOT_PLUS_UPDATES
            )
        )
        message.setField(fix.MarketDepth(depth))
        related_sym = fix50sp2.MarketDataRequest.NoRelatedSym()
        related_sym.setField(fix.Symbol(symbol))
        message.addGroup(related_sym)

        try:
            fix.Session.sendToTarget(message, self.MarketDataSession());
            self.message_queue.put(f"Subscribed to market data: {symbol}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to subscribe to market data: FIX session not found.")

    @ensure_session_id
    def unsubscribe_from_market_data(self, md_req_id, symbol):
        message = fix50sp2.MarketDataRequest()
        message.setField(fix.MDReqID(md_req_id))
        message.setField(
            fix.SubscriptionRequestType(
                fix.SubscriptionRequestType_DISABLE_PREVIOUS_SNAPSHOT_PLUS_UPDATE_REQUEST
            )
        )
        message.setField(fix.MarketDepth(0))

        try:
            fix.Session.sendToTarget(message, self.MarketDataSession());
            self.message_queue.put(f"Unsubscribed from market data: {md_req_id}")
            if not symbol is None:
                self.market_data_queue.put((symbol, "UNSUB", "", 0, 0))
        except fix.SessionNotFound:
            self.message_queue.put("Failed to unsubscribe from market data: FIX session not found.")

    @ensure_session_id
    def security_list_request(self, request, symbol=None):
        if (self.securityReqID_ == None):
            self.securityReqID_ = str(uuid.uuid4())

        message = fix50sp2.SecurityListRequest()
        message.setField(fix.SecurityReqID(self.securityReqID_))
        if symbol == None:
            message.setField(fix.SecurityListRequestType(4))
        else:
            message.setField(fix.SecurityListRequestType(0))
            message.setField(fix.Symbol(symbol))

        if request == "SNAPSHOT":
            message.setField(fix.SubscriptionRequestType(fix.SubscriptionRequestType_SNAPSHOT))
        elif request == "UPDATES":
            message.setField(fix.SubscriptionRequestType(fix.SubscriptionRequestType_SNAPSHOT_PLUS_UPDATES))
        elif request == "DISABLE":
            message.setField(fix.SubscriptionRequestType(fix.SubscriptionRequestType_DISABLE_PREVIOUS_SNAPSHOT_PLUS_UPDATE_REQUEST))

        try:
            fix.Session.sendToTarget(message, self.MarketDataSession());
            self.message_queue.put(f"Security List Request sent: {request} {symbol if symbol else 'ALL'}")
        except fix.SessionNotFound:
            self.message_queue.put("Failed to send Security List Request: FIX session not found.")

    def send_logout(self):
        for key, session in self.sessions.items():
            if session is None:
                continue
            logout = fix.Message()
            logout.getHeader().setField(fix.MsgType("5"))  # Logout message type
            try:
                fix.Session.sendToTarget(logout, session)
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
    def __init__(self, message_queue, market_data_queue, instrument_data_queue, fix_app):
        self.message_queue = message_queue
        self.market_data_queue = market_data_queue
        self.instrument_data_queue = instrument_data_queue
        self.fix_app = fix_app

        # Create widgets
        self.header = urwid.Text("FIX Trading Tool - Interactive CLI", align='center')
        self.command_output = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.fix_output = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.output_tabs = TabbedPane(
            [
                ("Commands", self.command_output),
                ("FIX Messages", self.fix_output),
            ],
            on_change=self._on_output_tab_change,
        )
        self.md = MarketDataPanel()
        self.md_output = urwid.LineBox(self.md, title="Market Data")
        self.inst = InstrumentPanel()
        self.inst_output = urwid.LineBox(self.inst, title="Instruments")
        self.main_pane = urwid.Pile([self.output_tabs])
        self.input = CommandEdit()
        self.panes = []

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
            ('tab_active', 'black', 'light gray'),
            ('tab_inactive', 'light gray', 'black'),
        ]

        # Create the main loop
        self.mouse_enabled = False

        self.loop = urwid.MainLoop(
            urwid.Padding(self.frame, align='center', left=1, right=1),
            palette=self.palette,
            unhandled_input=self.handle_global_input,
            handle_mouse=self.mouse_enabled
        )

        # Start a periodic callback to check the message queue
        self.loop.set_alarm_in(0.5, self.process_message_queue)

    def display_message(self, message):
        """
        Append a message to the command output pane.
        """
        self._append_to_output(self.command_output, message)

    def display_fix_message(self, message):
        """
        Append a message to the FIX output pane.
        """
        self._append_to_output(self.fix_output, message)

    def _append_to_output(self, listbox, message):
        sanitized_msg = self._sanitize_message(message)
        body = listbox.body
        body.append(urwid.Text(sanitized_msg))
        listbox.set_focus(len(body) - 1)

    @staticmethod
    def _sanitize_message(message):
        sanitized = str(message)
        return sanitized.replace('\r', ' ').replace('\n', ' ').replace('\1', '^')

    @staticmethod
    def _is_fix_message(message):
        normalized = str(message).lstrip()
        return normalized.startswith('<TX<') or normalized.startswith('>RX>')

    def _on_output_tab_change(self, _active_tab):
        self.frame.focus_position = 'footer'

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
        self.frame.focus_position = 'footer'

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
            if "md" in self.panes:
                try:
                    idx = self.panes.index("md")
                    self.main_pane.contents.pop(idx)
                    self.panes.pop(idx)
                except ValueError:
                    pass
            else:
                self.panes.insert(0, "md")
                self.main_pane.contents.insert(0, (self.md_output, ('pack', None)))
        elif cmd == "instrument":
            if len(parts) == 1:
                if "inst" in self.panes:
                    try:
                        idx = self.panes.index("inst")
                        self.main_pane.contents.pop(idx)
                        self.panes.pop(idx)
                    except ValueError:
                        pass
                else:
                    self.panes.insert(0, "inst")
                    self.main_pane.contents.insert(0, (self.inst_output, ('pack', None)))
                return

            if len(parts) == 2:
                self.display_message("Usage: instrument <request> [symbol]")
                request = parts[1].upper()
                if request not in ["SNAPSHOT", "UPDATES", "DISABLE"]:
                    self.display_message("Invalid instrument request type. Use SNAPSHOT, UPDATES, or DISABLE.")
                else:
                    self.fix_app.security_list_request(request)
            elif len(parts) == 3:
                request = parts[1].upper()
                symbol = parts[2].upper()
                if request not in ["SNAPSHOT", "UPDATES", "DISABLE"]:
                    self.display_message("Invalid instrument request type. Use SNAPSHOT, UPDATES, or DISABLE.")
                else:
                    self.fix_app.security_list_request(request, symbol)
            else:
                self.display_message("Usage: instrument <request> [symbol]")
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
            if len(parts) < 6 or len(parts) > 7:
                self.display_message("Usage: buy|sell <symbol> <type> <tif> <price> <size> [client_index]")
            else:
                try:
                    symbol = parts[1].upper()
                    order_type = parts[2].upper()  # Order type (MARKET or LIMIT)
                    tif = parts[3].upper()  # Time in force (GTC, IOC, FOK, etc.)
                    # allow for price to be a range via double dot notation,
                    # i.e 100..105 will generate 6 orders 100, 101, 102, 103, 104, 105
                    # increment based on the number of digits after the dot
                    if ".." in parts[4]:
                        price_range = parts[4].split("..")
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
                            size = float(parts[5])
                            client_index = self.client_index if len(parts) == 6 else int(parts[6])
                            if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                                self.display_message("Invalid client index.")
                                return
                            client_id = self.fix_app.clientIds[client_index]
                            side_enum = fix.Side_BUY if cmd == "buy" else fix.Side_SELL
                            self.fix_app.send_order(symbol, side_enum, order_type, tif, price, size, client_id)
                            price += (price_increment * price_step)
                            price = round(price, price_digits)
                    else:
                        price = float(parts[4])
                        size = float(parts[5])
                        client_index = self.client_index if len(parts) == 6 else int(parts[6])

                        if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                            self.display_message("Invalid client index.")
                            return

                        client_id = self.fix_app.clientIds[client_index]
                        side_enum = fix.Side_BUY if cmd == "buy" else fix.Side_SELL

                        self.fix_app.send_order(symbol, side_enum, order_type, tif, price, size, client_id)
                except ValueError:
                    self.display_message("Invalid parameters. Usage: buy|sell <symbol> <type> <tif> <price> <size> [client_index]")
        elif cmd == "modify":
            if len(parts) < 5 or len(parts) > 6:
                self.display_message("Usage: modify <orig_cl_ord_id> <new_price> <new_size> <new_type> [client_index]")
            else:
                try:
                    orig_cl_ord_id = parts[1]
                    new_price = float(parts[2])
                    new_qty = float(parts[3])
                    new_type = parts[4].upper() if len(parts) > 4 else None
                    client_index = self.client_index if len(parts) == 5 else int(parts[5])

                    if client_index < 0 or client_index >= len(self.fix_app.clientIds):
                        self.display_message("Invalid client index.")
                        return

                    # Validate at least one parameter is provided
                    if new_price is None or new_qty is None:
                        self.display_message("Parameters must be set: price and qty.")
                        return

                    client_id = self.fix_app.clientIds[client_index]

                    # Call the modify_order method with the parsed parameters
                    self.fix_app.modify_order(orig_cl_ord_id, client_id, new_price, new_qty, new_type)
                except ValueError as e:
                    self.display_message(f"Invalid parameters: {e}. Usage: modify <orig_cl_ord_id> <new_price> <new_size> <new_type> [client_index] ")
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
            if len(parts) < 3 or len(parts) > 4:
                self.display_message("Usage: subscribe <md_req_id> <symbol> <depth>")
            else:
                md_req_id = parts[1]
                symbol = parts[2]
                if len(parts) == 4:
                    depth = int(parts[3])
                else:
                    depth = 5
                # Send a subscription request
                self.fix_app.subscribe_to_market_data(md_req_id, symbol, depth)
        elif cmd == "unsubscribe":
            if len(parts) < 2 or len(parts) > 3:
                self.display_message("Usage: unsubscribe <md_req_id> [symbol]")
            else:
                symbol = None

                md_req_id = parts[1]
                if len(parts) == 3:
                    symbol = parts[2]
                # Send an unsubscription request
                self.fix_app.unsubscribe_from_market_data(md_req_id, symbol)
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
            "  help                                                                     Show this help message\n"
            "  use <client> <idx>                                                       Use a specific value for subsequent commands\n"
            "  list                                                                     List active orders\n"
            "  market_data / md                                                         Display market data\n"
            "  buy|sell <symbol> <type> <tif> <price> <size> [client_index]             Send a new order\n"
            "  modify <orig_cl_ord_id> <new_price> <new_size> <new_type> [client_index] Modify an existing order\n"
            "  cancel <orig_cl_ord_id> [client_index]                                   Cancel an existing order\n"
            "  subscribe <md_req_id> <symbol> [depth]                                   Subscribe to market data\n"
            "  unsubscribe <md_req_id> [symbol]                                         Unsubscribe from market data\n"
            "  instrument <request> <symbol>                                            Request instrument details\n"
            "  logout                                                                   Logout from FIX session\n"
            "  exit / quit                                                              Exit the application\n\n"
            "Note: arguments in square brackets are optional.\n"
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
        elif key == 'f3':
            self.output_tabs.activate_next()
        elif key == 'f4':
            self.toggle_mouse_mode()

    def process_message_queue(self, loop, user_data):
        """
        Periodically check the message queue for new messages from FIXApp and display them.
        """
        while not self.market_data_queue.empty():
            market_data = self.market_data_queue.get_nowait()
            self.md.UpdateMarketData(market_data)

        while not self.instrument_data_queue.empty():
            instrument_data = self.instrument_data_queue.get_nowait()
            self.inst.UpdateInstrument(instrument_data)

        while not self.message_queue.empty():
            message = self.message_queue.get_nowait()
            if self._is_fix_message(message):
                self.display_fix_message(message)
            else:
                self.display_message(message)

        # Schedule the next check
        loop.set_alarm_in(0.16, self.process_message_queue)

    def run(self):
        """
        Run the main loop.
        """
        # Ensure input is focused at the start
        self.frame.focus_position = 'footer'
        self._apply_mouse_mode()
        self.loop.run()

    def toggle_mouse_mode(self):
        self.mouse_enabled = not self.mouse_enabled
        self._apply_mouse_mode()
        if self.mouse_enabled:
            self.display_message(
                "Mouse mode enabled: click tabs to switch. Press Ctrl+M to enter selection mode."
            )
        else:
            self.display_message(
                "Selection mode enabled: press Ctrl+M to re-enable mouse interaction."
            )

    def _apply_mouse_mode(self):
        self.loop.screen.set_mouse_tracking(self.mouse_enabled)

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
    instrument_data_queue = queue.Queue()

    # Initialize the FIX application
    fix_app = FIXApp(message_queue, market_data_queue, instrument_data_queue, app_id="FIX_Client")
    fix_app.env = env.lower()
    # Grab key ID and secret from env vars
    fix_app.apiKeyId = os.getenv("TRUEX_KEY_ID")
    fix_app.apiKeySecret = os.getenv("TRUEX_KEY_SECRET")


    # Start the FIX session in a separate thread
    fix_thread = threading.Thread(target=run_fix_session, args=(fix_config_path, fix_app, message_queue), daemon=False)
    fix_thread.start()

    # Initialize the UI interface
    interface = FIXInterface(message_queue, market_data_queue, instrument_data_queue, fix_app)

    # Run the UI
    interface.run()
    fix_thread.join()

# =============================
# Entry Point
# =============================
if __name__ == "__main__":
    main()
