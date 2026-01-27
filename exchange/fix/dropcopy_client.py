"""
Drop copy listener that connects to the TrueX FIX gateway, subscribes to the
drop copy stream (35=AD), and renders consolidated order states on the console.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import queue
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import quickfix as fix
import quickfix50sp2 as fix50sp2
import requests


CONFIG_BY_ENV = {
    "local": "dc_config_local.cfg",
    "dev": "dc_config_dev.cfg",
    "uat": "dc_config_uat.cfg",
    "prod": "dc_config_prod.cfg",
}

ANSI_CLEAR = "\033[2J\033[H"


def query_rest(
    env: str,
    api_key_id: str,
    api_key_secret: str,
    method: str,
    path: str,
    body: Optional[dict] = None,
):
    """Minimal TrueX REST helper replicating the trading client logic."""

    def base_url_for_env(environment: str) -> str:
        if environment == "local":
            host = os.getenv("TRUEX_HOST")
            port = os.getenv("TRUEX_REST_PORT")
            if not host or not port:
                raise RuntimeError(
                    "TRUEX_HOST and TRUEX_REST_PORT must be set for local REST access"
                )
            return f"http://{host}:{port}"
        if environment == "dev":
            return "http://dev1.truex.co:9742"
        if environment == "uat":
            return "http://uat1.truex.co:9742"
        if environment == "prod":
            return "https://prod.truex.co"
        raise RuntimeError(f"Unsupported environment '{environment}' for REST base URL")

    base_url = base_url_for_env(env)
    url = base_url + path

    method_upper = method.upper()
    timestamp = str(int(time.time()))
    parsed = urlparse(url)
    payload = timestamp + method_upper + parsed.path

    digest = hmac.new(
        api_key_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")

    headers = {
        "x-truex-auth-timestamp": timestamp,
        "x-truex-auth-signature": signature,
        "x-truex-auth-token": api_key_id,
        "Content-Type": "application/json",
    }

    try:
        if method_upper == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        elif method_upper == "POST":
            response = requests.post(url, headers=headers, json=body, timeout=10)
        elif method_upper == "PUT":
            response = requests.put(url, headers=headers, json=body, timeout=10)
        elif method_upper == "DELETE":
            response = requests.delete(url, headers=headers, timeout=10)
        else:
            raise RuntimeError(f"Unsupported REST method {method}")
    except requests.RequestException as exc:  # noqa: BLE001
        raise RuntimeError(f"REST request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"REST request to {path} failed with {response.status_code}: {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:  # noqa: BLE001
        raise RuntimeError("REST response was not valid JSON") from exc


def get_client_ids(env: str, mnemonic: str, api_key_id: str, api_key_secret: str) -> Tuple[str, ...]:
    """Fetch client IDs associated with the API key."""

    query = "/api/v1/client" if mnemonic is None else f"/api/v1/client?mnemonic={mnemonic}"
    response = query_rest(env, api_key_id, api_key_secret, "GET", query)
    if not isinstance(response, list):
        raise RuntimeError("Unexpected client list response from REST API")

    client_ids = tuple(entry.get("id") for entry in response if entry.get("id"))
    if not client_ids:
        raise RuntimeError("No client IDs returned by REST API")
    return client_ids


def generate_password(
    secret: str,
    sending_time: str,
    msg_type: str,
    msg_seq_num: str,
    sender_comp_id: str,
    target_comp_id: str,
    username: str,
) -> str:
    """
    Generate the FIX logon password using the TrueX HMAC recipe.
    """
    payload = (
        f"{sending_time}{msg_type}{msg_seq_num}{sender_comp_id}{target_comp_id}{username}"
    )
    digest = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


@dataclass
class OrderStateSnapshot:
    key: str
    updated_at: datetime = field(default_factory=datetime.utcnow)
    previous_key: Optional[str] = None
    cl_ord_id: Optional[str] = None
    order_id: Optional[str] = None
    exec_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    status: Optional[str] = None
    exec_type: Optional[str] = None
    order_qty: Optional[str] = None
    price: Optional[str] = None
    leaves_qty: Optional[str] = None
    cum_qty: Optional[str] = None
    avg_px: Optional[str] = None
    last_qty: Optional[str] = None
    last_px: Optional[str] = None
    text: Optional[str] = None
    transact_time: Optional[str] = None


class OrderStateStore:
    """
    Thread-safe container for the latest order state per key.
    """

    def __init__(self) -> None:
        self._states: Dict[str, OrderStateSnapshot] = {}
        self._lock = threading.Lock()

    def upsert(self, snapshot: OrderStateSnapshot) -> None:
        with self._lock:
            if snapshot.previous_key and snapshot.previous_key != snapshot.key:
                self._states.pop(snapshot.previous_key, None)
            self._states[snapshot.key] = snapshot

    def all_states(self) -> Tuple[OrderStateSnapshot, ...]:
        with self._lock:
            return tuple(self._states.values())


class ConsoleRenderer:
    """
    Console renderer for both order states and recent log messages.
    """

    def __init__(self, event_queue: queue.Queue, stop_event: threading.Event):
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.store = OrderStateStore()
        self.logs: deque[str] = deque(maxlen=15)

    def run(self) -> None:
        """
        Pump events until the stop event is signalled.
        """
        while not self.stop_event.is_set():
            try:
                event_type, payload = self.event_queue.get(timeout=0.5)
            except queue.Empty:
                self._redraw()
                continue

            if event_type == "log" and isinstance(payload, str):
                self._handle_log(payload)
            elif event_type == "state" and isinstance(payload, OrderStateSnapshot):
                self.store.upsert(payload)
                if payload.text:
                    self._handle_log(
                        f"Exec {payload.exec_id or payload.key}: {payload.text}"
                    )
            elif event_type == "stop":
                self.stop_event.set()

            self._redraw()

        self._redraw()

    def _handle_log(self, message: str) -> None:
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")

    def _redraw(self) -> None:
        output = self._format_output()
        sys.stdout.write(ANSI_CLEAR)
        sys.stdout.write(output)
        sys.stdout.flush()

    def _format_output(self) -> str:
        lines = ["Drop Copy Order States", ""]
        lines.extend(self._format_states_table())
        lines.append("")
        lines.append("Recent Events")
        lines.append("-" * 80)
        if self.logs:
            lines.extend(self.logs)
        else:
            lines.append("(waiting for events)")
        return "\n".join(lines) + "\n"

    def _format_states_table(self) -> Tuple[str, ...]:
        states = sorted(
            self.store.all_states(),
            key=lambda s: (
                s.symbol or "",
                s.cl_ord_id or s.order_id or s.exec_id or "",
            ),
        )

        header = (
            f"{'Updated':<9} {'Symbol':<10} {'Side':<4} {'Status':<14} "
            f"{'ExecType':<10} {'Price':>10} {'Leaves':>10} {'Cum':>10} "
            f"{'AvgPx':>10} {'LastQty':>10} {'LastPx':>10} "
            f"{'ClOrdID':<20} {'ExecID':<20}"
        )
        rows = [header, "-" * len(header)]
        if not states:
            rows.append("(waiting for order states)")
            return tuple(rows)

        for state in states:
            rows.append(
                f"{state.updated_at.strftime('%H:%M:%S'):<9} "
                f"{(state.symbol or '-'):<10} "
                f"{(state.side or '-'):<4} "
                f"{(state.status or '-'):<14} "
                f"{(state.exec_type or '-'):<10} "
                f"{(state.price or '-'):>10} "
                f"{(state.leaves_qty or '-'):>10} "
                f"{(state.cum_qty or '-'):>10} "
                f"{(state.avg_px or '-'):>10} "
                f"{(state.last_qty or '-'):>10} "
                f"{(state.last_px or '-'):>10} "
                f"{self._truncate(state.cl_ord_id):<20} "
                f"{self._truncate(state.exec_id):<20}"
            )
        return tuple(rows)

    @staticmethod
    def _truncate(value: Optional[str], length: int = 20) -> str:
        if not value:
            return "-"
        if len(value) <= length:
            return value
        return f"{value[: length - 3]}..."


class DropCopyApp(fix.Application):
    """
    Minimal FIX application focused on the drop copy stream.
    """

    def __init__(
        self,
        event_queue: queue.Queue,
        api_key_id: str,
        api_key_secret: str,
        env: str,
        client_ids: Tuple[str, ...],
        reset_seq_num: bool = True,
        request_mass_status: bool = False,
    ):
        super().__init__()
        self.event_queue = event_queue
        self.api_key_id = api_key_id
        self.api_key_secret = api_key_secret
        self.env = env
        self.client_ids = client_ids
        self.reset_seq_num = reset_seq_num
        self.request_mass_status = request_mass_status
        self.sessions: Dict[str, fix.SessionID] = {}
        self._mass_status_requested = False

    def onCreate(self, session_id: fix.SessionID) -> None:
        self.sessions[session_id.toString()] = session_id
        self._log(f"Session created: {session_id}")

    def onLogon(self, session_id: fix.SessionID) -> None:
        self.sessions[session_id.toString()] = session_id
        self._log(f"Logon successful: {session_id}")
        self._send_drop_copy_request(session_id)

    def onLogout(self, session_id: fix.SessionID) -> None:
        self.sessions.pop(session_id.toString(), None)
        self._log(f"Logout: {session_id}")

    def toAdmin(self, message: fix.Message, session_id: fix.SessionID) -> None:
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        if msg_type.getValue() == fix.MsgType_Logon:
            self._prepare_logon(message)
        self._log(f"<TX< {message}")

    def fromAdmin(self, message: fix.Message, _: fix.SessionID) -> None:
        self._log(f">RX> {message}")

    def toApp(self, message: fix.Message, _: fix.SessionID) -> None:
        self._log(f"<TX< {message}")

    def fromApp(self, message: fix.Message, session_id: fix.SessionID) -> None:
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)
        msg_type_value = msg_type.getValue()

        if msg_type_value == fix.MsgType_ExecutionReport:
            snapshot = self._snapshot_from_execution_report(message)
            if snapshot:
                self.event_queue.put(("state", snapshot))
        elif msg_type_value == fix.MsgType_TradeCaptureReportRequestAck:
            self._handle_trade_capture_ack(message, session_id)
        elif msg_type_value == fix.MsgType_BusinessMessageReject:
            self._handle_business_reject(message)
        else:
            self._log(f"Unhandled message {msg_type_value} from {session_id}")
        self._log(f">RX> {message}")

    def _prepare_logon(self, message: fix.Message) -> None:
        sending_time = datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        msg_seq_num = (
            "1"
            if self.reset_seq_num
            else message.getHeader().getField(fix.MsgSeqNum()).getString()
        )
        header = message.getHeader()
        msg_type = header.getField(fix.MsgType()).getString()
        sender_comp_id = header.getField(fix.SenderCompID()).getString()
        target_comp_id = header.getField(fix.TargetCompID()).getString()
        password = generate_password(
            self.api_key_secret,
            sending_time,
            msg_type,
            msg_seq_num,
            sender_comp_id,
            target_comp_id,
            self.api_key_id,
        )

        header.setField(52, sending_time)
        if self.reset_seq_num:
            message.setField(fix.ResetSeqNumFlag(True))
        message.setField(fix.Username(self.api_key_id))
        message.setField(fix.Password(password))

    def _send_drop_copy_request(self, session_id: fix.SessionID) -> None:
        request = fix50sp2.TradeCaptureReportRequest()
        trade_request_id = str(uuid.uuid4())
        request.setField(fix.TradeRequestID(trade_request_id))
        request.setField(fix.TradeRequestType(fix.TradeRequestType_ALL_TRADES))
        request.setField(
            fix.SubscriptionRequestType(
                fix.SubscriptionRequestType_SNAPSHOT_PLUS_UPDATES
            )
        )

        for client_id in self.client_ids:
            party_group = fix50sp2.TradeCaptureReportRequest.NoPartyIDs()
            party_group.setField(fix.PartyID(client_id))
            party_group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
            request.addGroup(party_group)

        try:
            fix.Session.sendToTarget(request, session_id)
            self._log(
                "Sent drop copy subscription (35=AD) TradeRequestID="
                f"{trade_request_id} Clients={len(self.client_ids)}"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"Failed to send drop copy request: {exc}")

    def _handle_trade_capture_ack(
        self, message: fix.Message, session_id: fix.SessionID
    ) -> None:
        """Handle TradeCaptureReportRequestAck (35=AQ) and send mass status if requested."""
        trade_request_result = fix.TradeRequestResult()
        trade_request_status = fix.TradeRequestStatus()
        text_field = fix.Text()

        try:
            message.getField(trade_request_result)
            message.getField(trade_request_status)
        except fix.FieldNotFound:
            self._log("TradeCaptureReportRequestAck missing required fields")
            return

        result = trade_request_result.getValue()
        status = trade_request_status.getValue()

        try:
            message.getField(text_field)
            text = text_field.getValue()
        except fix.FieldNotFound:
            text = ""

        if result == 0 and status == 0:
            self._log(f"Drop copy subscription accepted: {text}")
            if self.request_mass_status and not self._mass_status_requested:
                self._send_mass_order_status_request(session_id)
        else:
            self._log(f"Drop copy subscription failed: result={result} status={status} {text}")

    def _handle_business_reject(self, message: fix.Message) -> None:
        """Handle BusinessMessageReject (35=j)."""
        text_field = fix.Text()
        ref_msg_type = fix.RefMsgType()

        try:
            message.getField(text_field)
            text = text_field.getValue()
        except fix.FieldNotFound:
            text = "(no text)"

        try:
            message.getField(ref_msg_type)
            ref_type = ref_msg_type.getValue()
        except fix.FieldNotFound:
            ref_type = "?"

        self._log(f"BusinessReject for MsgType={ref_type}: {text}")

    def _send_mass_order_status_request(self, session_id: fix.SessionID) -> None:
        """Send OrderMassStatusRequest (35=BG) to get current status of all active orders."""
        request = fix.Message()
        header = request.getHeader()
        header.setField(fix.MsgType(fix.MsgType_OrderMassStatusRequest))

        mass_status_req_id = str(uuid.uuid4())
        request.setField(fix.MassStatusReqID(mass_status_req_id))
        request.setField(fix.MassStatusReqType(7))  # 7 = Status for all orders

        group = fix.Group(453, 448)  # NoPartyIDs group
        for client_id in self.client_ids:
            group.setField(fix.PartyID(client_id))
            group.setField(fix.PartyRole(fix.PartyRole_CLIENT_ID))
            request.addGroup(group)

        try:
            fix.Session.sendToTarget(request, session_id)
            self._mass_status_requested = True
            self._log(
                f"Sent OrderMassStatusRequest (35=BG) MassStatusReqID="
                f"{mass_status_req_id} Clients={len(self.client_ids)}"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"Failed to send mass order status request: {exc}")

    def _snapshot_from_execution_report(
        self, message: fix.Message
    ) -> Optional[OrderStateSnapshot]:
        def get(field_cls: type[fix.Field]) -> Optional[str]:
            field = field_cls()
            try:
                message.getField(field)
            except fix.FieldNotFound:
                return None
            value = field.getValue()
            if hasattr(value, "strftime"):
                return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            return str(value)

        exec_id = get(fix.ExecID)
        order_id = get(fix.OrderID)
        cl_ord_id = get(fix.ClOrdID)
        orig_cl_ord_id = get(fix.OrigClOrdID)
        ord_status_raw = get(fix.OrdStatus)
        exec_type_raw = get(fix.ExecType)

        cancel_ord_statuses = {str(fix.OrdStatus_CANCELED), str(fix.OrdStatus_PENDING_CANCEL)}
        cancel_exec_types = {str(fix.ExecType_CANCELED), str(fix.ExecType_PENDING_CANCEL)}
        replace_ord_statuses = {
            str(fix.OrdStatus_REPLACED),
            str(fix.OrdStatus_PENDING_REPLACE),
        }
        replace_exec_types = {
            str(fix.ExecType_REPLACED),
            str(fix.ExecType_PENDING_REPLACE),
        }

        is_cancel_report = (
            ord_status_raw in cancel_ord_statuses or exec_type_raw in cancel_exec_types
        )
        is_replace_report = (
            ord_status_raw in replace_ord_statuses or exec_type_raw in replace_exec_types
        )

        key = cl_ord_id or order_id or exec_id
        previous_key: Optional[str] = None

        if is_cancel_report and orig_cl_ord_id:
            key = orig_cl_ord_id
        elif is_replace_report and orig_cl_ord_id:
            previous_key = orig_cl_ord_id
            if cl_ord_id:
                key = cl_ord_id
            else:
                key = orig_cl_ord_id

        key = key or orig_cl_ord_id or order_id or exec_id
        if not key:
            self._log("Execution report missing identifiers, ignoring.")
            return None

        effective_cl_ord_id = (
            orig_cl_ord_id if (is_cancel_report and orig_cl_ord_id) else cl_ord_id
        )

        snapshot = OrderStateSnapshot(
            key=key,
            updated_at=datetime.utcnow(),
            previous_key=previous_key,
            cl_ord_id=effective_cl_ord_id or key,
            order_id=order_id,
            exec_id=exec_id,
            symbol=get(fix.Symbol),
            side=self._map_side(get(fix.Side)),
            status=self._map_status(ord_status_raw),
            exec_type=self._map_exec_type(exec_type_raw),
            order_qty=get(fix.OrderQty),
            price=get(fix.Price),
            leaves_qty=get(fix.LeavesQty),
            cum_qty=get(fix.CumQty),
            avg_px=get(fix.AvgPx),
            last_qty=get(fix.LastQty),
            last_px=get(fix.LastPx),
            text=get(fix.Text),
            transact_time=get(fix.TransactTime),
        )
        return snapshot

    @staticmethod
    def _map_side(value: Optional[str]) -> Optional[str]:
        return {
            str(fix.Side_BUY): "BUY",
            str(fix.Side_SELL): "SELL",
        }.get(value) if value is not None else None

    @staticmethod
    def _map_status(value: Optional[str]) -> Optional[str]:
        mapping = {
            str(fix.OrdStatus_NEW): "NEW",
            str(fix.OrdStatus_PARTIALLY_FILLED): "PARTIAL",
            str(fix.OrdStatus_FILLED): "FILLED",
            str(fix.OrdStatus_CANCELED): "CANCELED",
            str(fix.OrdStatus_REPLACED): "REPLACED",
            str(fix.OrdStatus_PENDING_NEW): "PEND_NEW",
            str(fix.OrdStatus_PENDING_REPLACE): "PEND_REPL",
            str(fix.OrdStatus_PENDING_CANCEL): "PEND_CXL",
            str(fix.OrdStatus_REJECTED): "REJECTED",
            str(fix.OrdStatus_DONE_FOR_DAY): "DONE_DAY",
            str(fix.OrdStatus_EXPIRED): "EXPIRED",
        }
        return mapping.get(value) if value is not None else None

    @staticmethod
    def _map_exec_type(value: Optional[str]) -> Optional[str]:
        mapping = {
            str(fix.ExecType_NEW): "NEW",
            str(fix.ExecType_PARTIAL_FILL): "PARTIAL",
            str(fix.ExecType_FILL): "FILL",
            str(fix.ExecType_CANCELED): "CANCELED",
            str(fix.ExecType_REPLACED): "REPLACED",
            str(fix.ExecType_REJECTED): "REJECTED",
            str(fix.ExecType_PENDING_NEW): "PEND_NEW",
            str(fix.ExecType_PENDING_CANCEL): "PEND_CXL",
            str(fix.ExecType_PENDING_REPLACE): "PEND_REPL",
            str(fix.ExecType_DONE_FOR_DAY): "DONE_DAY",
            str(fix.ExecType_EXPIRED): "EXPIRED",
            str(fix.ExecType_ORDER_STATUS): "ORD_STATUS",
        }
        return mapping.get(value) if value is not None else None

    def _log(self, message: str) -> None:
        self.event_queue.put(("log", message))


def run_fix_session(
    config_path: str,
    fix_app: DropCopyApp,
    stop_event: threading.Event,
    event_queue: queue.Queue,
) -> None:
    """
    Spin up the QuickFIX initiator and keep it alive until stop_event is set.
    """
    initiator: Optional[fix.SocketInitiator] = None
    try:
        settings = fix.SessionSettings(config_path)
        store_factory = fix.MemoryStoreFactory()
        log_factory = fix.FileLogFactory(settings)
        initiator = fix.SocketInitiator(fix_app, store_factory, settings, log_factory)
        initiator.start()
        event_queue.put(("log", f"FIX initiator started using {config_path}"))

        while not stop_event.is_set():
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        event_queue.put(("log", f"FIX session error: {exc}"))
    finally:
        if initiator is not None:
            initiator.stop()
        event_queue.put(("log", "FIX initiator stopped"))
        event_queue.put(("stop", None))
        stop_event.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Listen to the TrueX drop copy stream and aggregate order states."
    )
    parser.add_argument(
        "--env",
        choices=CONFIG_BY_ENV.keys(),
        default="dev",
        help="Environment to connect to (determines default config path).",
    )
    parser.add_argument(
        "--mnemonic",
        default=None,
        help="Client mnemonic to receive drops for.",
    )
    parser.add_argument(
        "--config",
        help="Path to a FIX configuration file. Overrides --env default.",
    )
    parser.add_argument(
        "--no-reset-seq",
        action="store_true",
        help="Do not include ResetSeqNumFlag on logon.",
    )
    parser.add_argument(
        "--mass-status",
        action="store_true",
        help="Request mass order status (35=BG) after drop copy subscription is accepted.",
    )
    return parser.parse_args()


def resolve_config_path(env: str, explicit_path: Optional[str]) -> str:
    if explicit_path:
        return explicit_path
    return CONFIG_BY_ENV[env]


def main() -> None:
    args = parse_args()
    config_path = resolve_config_path(args.env, args.config)

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    api_key_id = os.getenv("TRUEX_KEY_ID")
    api_key_secret = os.getenv("TRUEX_KEY_SECRET")
    if not api_key_id or not api_key_secret:
        print("TRUEX_KEY_ID and TRUEX_KEY_SECRET must be set in the environment.")
        sys.exit(1)

    try:
        client_ids = get_client_ids(args.env, args.mnemonic, api_key_id, api_key_secret)
    except RuntimeError as exc:
        print(f"Failed to fetch client IDs: {exc}")
        sys.exit(1)

    print(f"Using {len(client_ids)} client IDs for drop copy subscription")

    event_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    fix_app = DropCopyApp(
        event_queue=event_queue,
        api_key_id=api_key_id,
        api_key_secret=api_key_secret,
        env=args.env,
        client_ids=client_ids,
        reset_seq_num=not args.no_reset_seq,
        request_mass_status=args.mass_status,
    )

    fix_thread = threading.Thread(
        target=run_fix_session,
        args=(config_path, fix_app, stop_event, event_queue),
        daemon=True,
    )
    fix_thread.start()

    renderer = ConsoleRenderer(event_queue, stop_event)
    try:
        renderer.run()
    except KeyboardInterrupt:
        stop_event.set()
        event_queue.put(("log", "Interrupted by user, shutting down..."))
    finally:
        fix_thread.join()


if __name__ == "__main__":
    main()
