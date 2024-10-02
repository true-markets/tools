import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

import quickfix as fix
import quickfix50sp2 as fix50sp2
import requests

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Instrument details from TrueX API
INSTRUMENT = {
    "symbol": "BTC-PYUSD",
    "quote_increment": 0.5,
    "base_increment": 0.0001,
    "reference_price": 10000
}

SIDE_MAP = {
    fix.Side_BUY: "BUY",
    fix.Side_SELL: "SELL"
}

ORDER_TYPE_MAP = {
    fix.OrdType_MARKET: "MARKET",
    fix.OrdType_LIMIT: "LIMIT"
}

EXEC_TYPE_MAP = {
    "0": "New (Order Confirmation)",
    "1": "Partial Fill",
    "2": "Fill",
    "3": "Done for Day",
    "4": "Canceled",
    "5": "Replaced (Modified Order)",
    "6": "Pending Cancel",
    "8": "Rejected",
    "9": "Suspended",
    "A": "Pending New",
    "C": "Expired",
    "E": "Pending Replace"
}

ORDER_STATUS_MAP = {
    "0": "New",
    "1": "Partially Filled",
    "2": "Filled",
    "3": "Done for Day",
    "4": "Canceled",
    "5": "Replaced",
    "6": "Pending Cancel",
    "7": "Stopped",
    "8": "Rejected",
    "9": "Suspended",
    "A": "Pending New",
    "B": "Calculated",
    "C": "Expired",
    "D": "Accepted for Bidding",
    "E": "Pending Replace"
}


# Retrieves client ID from TrueX API using the provided API key ID, API key secret, and mnemonic
def get_client_id(api_key_id, api_key_secret, mnemonic):
    header_auth_timestamp = "x-truex-auth-timestamp"
    header_auth_signature = "x-truex-auth-signature"
    header_auth_token = "x-truex-auth-token"

    address = os.getenv("TRUEX_API_ADDRESS")
    url = f"{address}/api/v1/client"

    auth_timestamp = str(int(time.time()))
    http_method = "GET"

    # Generate payload for HMAC signature using auth timestamp and HTTP method
    parsed_url = urlparse(url)
    path = parsed_url.path
    payload = auth_timestamp + http_method + path

    # Generate HMAC signature using API key secret and payload
    hmac_key = api_key_secret.encode('utf-8')
    hmac_message = payload.encode('utf-8')
    hmac_digest = hmac.new(hmac_key, hmac_message, hashlib.sha256).digest()

    # Base64 encode the HMAC digest to generate the auth signature
    auth_signature = base64.b64encode(hmac_digest).decode('utf-8')

    # Set headers for the request including the auth timestamp, auth signature, and API key ID
    headers = {
        header_auth_timestamp: auth_timestamp,
        header_auth_signature: auth_signature,
        header_auth_token: api_key_id,
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        logging.info("Success: %s", response.json())
    else:
        logging.info("Failed with status code %s: %s", response.status_code, response.text)

    # Find the client ID that matches the provided mnemonic
    matching_id = None
    for entry in response.json():
        if entry['info']['mnemonic'] == mnemonic:
            matching_id = entry['id']
            break

    if matching_id:
        logging.info("Found matching ID: %s", matching_id)
    else:
        logging.info("No matching mnemonic found.")
    return matching_id


class Application(fix.Application):
    def __init__(self, api_key_id, api_key_secret, mnemonic):
        super().__init__()
        self.sessionId = None
        self.lastClOrdId = None
        self.orderIdCounter = 0
        self.clientId = None
        self.sessionID = None
        self.mnemonic = mnemonic
        self.apiKeyId = api_key_id
        self.apiKeySecret = api_key_secret

    def onCreate(self, session_id):
        logging.info("Session created: %s", session_id)

    def onLogon(self, session_id):
        logging.info("Logon successful: %s", session_id)
        self.sessionID = session_id
        # TODO handle case client id is not found
        self.clientId = get_client_id(self.apiKeyId, self.apiKeySecret, self.mnemonic)

    def onLogout(self, session_id):
        logging.info("Logout: %s", session_id)

    def toAdmin(self, message, session_id):
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)

        if msg_type.getValue() == fix.MsgType_Logon:
            self.sessionId = None
            logging.info("Logging on clientId: %s", self.clientId)

            sending_time = datetime.utcnow().strftime('%Y%m%d-%H:%M:%S.%f')[:-3]
            msg_type = message.getHeader().getField(fix.MsgType()).getString()
            msg_seq_num = message.getHeader().getField(fix.MsgSeqNum()).getString()
            sender_comp_id = message.getHeader().getField(fix.SenderCompID()).getString()
            target_comp_id = message.getHeader().getField(fix.TargetCompID()).getString()

            password = self.generate_password(self.apiKeySecret, sending_time, msg_type, msg_seq_num, sender_comp_id,
                                              target_comp_id, self.apiKeyId)

            message.getHeader().setField(52, sending_time)
            message.setField(fix.Username(self.apiKeyId))
            message.setField(fix.Password(password))

        logging.info("Admin message sent: %s, %s", message, message.getHeader().getField(fix.MsgType()).getString())

    def toApp(self, message, session_id):
        logging.info("Application message sent: %s", message)

    def fromAdmin(self, message, session_id):
        logging.info("Admin message received: %s", message)

        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)

        if msg_type.getValue() == fix.MsgType_Reject:
            ref_seq_num = fix.RefSeqNum()
            text = fix.Text()

            if message.isSetField(ref_seq_num):
                message.getField(ref_seq_num)
            if message.isSetField(text):
                message.getField(text)

            logging.info("Received Reject - RefSeqNum: %s, Text: %s", ref_seq_num.getValue(), text.getValue())

    def fromApp(self, message, session_id):
        logging.info("Application message received: %s", message)
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)

        # Handling Execution Report (MsgType = 8)
        if msg_type.getValue() == fix.MsgType_ExecutionReport:
            logging.info("Got execution report message")
            self.on_execution_report(message, session_id)

    def on_execution_report(self, message, session_id):
        poss_dup_flag = fix.PossDupFlag()
        if message.isSetField(poss_dup_flag):
            message.getField(poss_dup_flag)
            if poss_dup_flag.getValue() == "Y":
                logging.info("PossDupFlag is set, ignoring replay message.")
                return

        exec_type = fix.ExecType()
        ord_status = fix.OrdStatus()

        message.getField(exec_type)
        message.getField(ord_status)

        exec_type_value = exec_type.getValue()
        ord_status_value = ord_status.getValue()

        cl_ord_id = fix.ClOrdID()
        price = fix.Price()
        quantity = fix.OrderQty()
        side = fix.Side()
        order_type = fix.OrdType()

        if message.isSetField(cl_ord_id):
            message.getField(cl_ord_id)
        if message.isSetField(price):
            message.getField(price)
        if message.isSetField(quantity):
            message.getField(quantity)
        if message.isSetField(side):
            message.getField(side)
        if message.isSetField(order_type):
            message.getField(order_type)

        exec_type_str = EXEC_TYPE_MAP.get(exec_type_value, f"Unknown ({exec_type_value})")
        order_status_str = ORDER_STATUS_MAP.get(ord_status_value, f"Unknown ({ord_status_value})")
        side_str = SIDE_MAP.get(side.getValue())
        order_type_str = ORDER_TYPE_MAP.get(order_type.getValue())

        logging.info(
            "Execution Report - ClientID: %s, OrderID: %s, ExecType: %s, OrderStatus: %s, Price: %s, Quantity: %s, Side: %s, OrderType: %s",
            self.clientId,
            cl_ord_id.getValue(),
            exec_type_str,
            order_status_str,
            price.getValue(),
            quantity.getValue(),
            side_str,
            order_type_str)

    # Generate a unique order ID based on timestamp, UUID, and a counter
    def generate_order_id(self):
        timestamp = int(time.time() * 1000)  # Use millisecond precision
        self.orderIdCounter += 1
        uuid_fragment = uuid.uuid4().hex[:8]
        return f"{timestamp}-{uuid_fragment}-{self.orderIdCounter}"

    # Modify an existing order with new price, quantity, and/or order type
    def modify_order(self, session_id, new_price=None, new_quantity=None, new_order_type=None):
        if not self.lastClOrdId:
            logging.info("No order to modify")
            return None

        cl_ord_id = self.generate_order_id()

        message = fix50sp2.OrderCancelReplaceRequest()
        message.setField(fix.OrigClOrdID(self.lastClOrdId))
        message.setField(fix.ClOrdID(cl_ord_id))

        # Set new price if provided
        if new_price is not None:
            message.setField(fix.Price(new_price))

        # Set new quantity if provided
        if new_quantity is not None:
            message.setField(fix.OrderQty(new_quantity))

        # Set new order type if provided
        if new_order_type is not None:
            message.setField(fix.OrdType(new_order_type))

        group = fix50sp2.NewOrderSingle.NoPartyIDs()
        group.setField(fix.PartyID(str(self.clientId)))
        group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(group)

        fix.Session.sendToTarget(message, session_id)
        logging.info("Order modified - ClientID: %s, LastOrderID: %s, NewOrderID: %s, NewPrice: %s, NewQuantity: %s, NewOrderType: %s",
                     self.clientId, self.lastClOrdId, cl_ord_id, new_price, new_quantity, new_order_type)
        self.lastClOrdId = cl_ord_id
        return message

    def send_logout(self):
        if self.sessionId is not None:
            logout = fix.Message()
            logout.getHeader().setField(fix.MsgType("5"))
            fix.Session.sendToTarget(logout, self.sessionID)
            logging.info("Logout message sent for clientId: %s", self.clientId)

    # Send a new order with the provided side, price, quantity, and order type
    def send_order(self, session_id, side, price, quantity, order_type=fix.OrdType_LIMIT):
        cl_ord_id = self.generate_order_id()

        message = fix50sp2.NewOrderSingle()
        message.setField(fix.ClOrdID(cl_ord_id))
        message.setField(fix.Symbol(INSTRUMENT["symbol"]))
        message.setField(fix.Side(side))
        message.setField(fix.TransactTime())
        message.setField(fix.OrdType(order_type))
        message.setField(fix.Price(price))
        message.setField(fix.OrderQty(quantity))
        message.setField(fix.TimeInForce(fix.TimeInForce_GOOD_TILL_CANCEL))

        group = fix50sp2.NewOrderSingle.NoPartyIDs()
        group.setField(fix.PartyID(str(self.clientId)))
        group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(group)

        fix.Session.sendToTarget(message, session_id)

        order_type_str = "MARKET" if order_type == fix.OrdType_MARKET else "LIMIT"
        side_str = "BUY" if side == fix.Side_BUY else "SELL"
        logging.info("Order placed - ClientID: %s, OrderID: %s, Price: %s, Quantity: %s, Side: %s, OrderType: %s",
                     self.clientId, self.lastClOrdId, price, quantity, side_str, order_type_str)
        self.lastClOrdId = cl_ord_id

    # Cancel the last order placed
    def cancel_order(self, session_id):
        if not self.lastClOrdId:
            return  # No order to cancel

        cl_ord_id = self.generate_order_id()

        message = fix50sp2.OrderCancelRequest()
        message.setField(fix.OrigClOrdID(self.lastClOrdId))
        message.setField(fix.ClOrdID(cl_ord_id))

        group = fix50sp2.NewOrderSingle.NoPartyIDs()
        group.setField(fix.PartyID(str(self.clientId)))
        group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
        message.addGroup(group)

        fix.Session.sendToTarget(message, session_id)
        logging.info("Order canceled - ClientID: %s, LastOrderID: %s, OrderID: %s",
                     self.clientId, self.lastClOrdId, cl_ord_id)
        self.lastClOrdId = cl_ord_id

    @staticmethod
    def generate_password(secret, sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id, username):
        # Generate message to be signed using the provided parameters and secret
        message = str(sending_time) + str(msg_type) + str(msg_seq_num) + str(sender_comp_id) + str(
            target_comp_id) + str(username)
        logging.info("Raw SendingTime: %s", sending_time)
        logging.info("Raw MsgType: %s", msg_type)
        logging.info("Raw MsgSeqNum: %s", msg_seq_num)
        logging.info("Raw SenderCompID: %s", sender_comp_id)
        logging.info("Raw TargetCompID: %s", target_comp_id)
        logging.info("Raw Username: %s", username)

        logging.info("Message: %s, %s, %s, %s, %s, %s", sender_comp_id, target_comp_id, username, sending_time,
                     msg_type, msg_seq_num)
        try:
            message_bytes = message.encode('utf-8')
        except UnicodeEncodeError as e:
            logging.info("Error encoding message: %s", e)
            message_bytes = message.encode('utf-8', 'ignore')

        # Generate HMAC signature using secret and message
        secret_bytes = secret.encode('utf-8')
        hmac_sha256 = hmac.new(secret_bytes, message_bytes, hashlib.sha256)

        # Base64 encode the HMAC digest to generate the password
        signature = base64.b64encode(hmac_sha256.digest()).decode('utf-8')

        return signature
