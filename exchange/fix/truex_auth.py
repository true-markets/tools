"""
FIX logon credentials for the TrueX gateway.

Two kinds of API key are accepted at logon (35=A):

- ES256 key pairs issued by the web UI (console.truemarkets.co organization
  keys, or personal keys from Settings -> API keys). These are downloaded as a
  JSON file holding the key ID and a P-256 private JWK. Password (554) is the
  raw 64 byte r||s signature, unpadded base64url encoded.
- Legacy HMAC keys (TRUEX_KEY_ID / TRUEX_KEY_SECRET). Password (554) is
  base64(HMAC-SHA-256(secret, message)).

Both sign the same message:
    SendingTime + MsgType + MsgSeqNum + SenderCompID + TargetCompID + Username
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

P256_COORD_LEN = 32


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def logon_payload(
    sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id, username
) -> str:
    return (
        f"{sending_time}{msg_type}{msg_seq_num}"
        f"{sender_comp_id}{target_comp_id}{username}"
    )


class LogonSigner:
    """Holds an API key and produces the Password (554) for a logon."""

    def __init__(
        self,
        key_id: str,
        hmac_secret: Optional[str] = None,
        private_key: Optional[ec.EllipticCurvePrivateKey] = None,
    ):
        if (hmac_secret is None) == (private_key is None):
            raise ValueError("Provide exactly one of an HMAC secret or an ES256 private key")
        self.key_id = key_id
        self.hmac_secret = hmac_secret
        self.private_key = private_key

    @property
    def algorithm(self) -> str:
        return "ES256" if self.private_key is not None else "HMAC-SHA256"

    @property
    def is_hmac(self) -> bool:
        return self.hmac_secret is not None

    @classmethod
    def from_key_file(cls, path: str) -> "LogonSigner":
        """Load the JSON key file downloaded from the web UI."""
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            data = json.load(f)

        key_id = data.get("key_id")
        jwk = data.get("private_key")
        algorithm = data.get("algorithm", "ES256")
        if not key_id or not isinstance(jwk, dict):
            raise ValueError(f"{path}: expected 'key_id' and 'private_key' fields")
        if algorithm != "ES256":
            raise ValueError(f"{path}: unsupported algorithm '{algorithm}', expected ES256")
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
            raise ValueError(f"{path}: private_key must be an EC P-256 JWK")
        if not jwk.get("d"):
            raise ValueError(f"{path}: private_key is missing 'd'")

        d = int.from_bytes(_b64url_decode(jwk["d"]), "big")
        private_key = ec.derive_private_key(d, ec.SECP256R1())

        # Catch a corrupted file now rather than as a logon reject.
        if jwk.get("x") and jwk.get("y"):
            public = private_key.public_key().public_numbers()
            if (
                public.x != int.from_bytes(_b64url_decode(jwk["x"]), "big")
                or public.y != int.from_bytes(_b64url_decode(jwk["y"]), "big")
            ):
                raise ValueError(f"{path}: public key (x, y) does not match private key 'd'")

        return cls(key_id=key_id, private_key=private_key)

    @classmethod
    def from_args_or_env(cls, key_file: Optional[str]) -> "LogonSigner":
        """
        Resolve credentials: --key-file, then TRUEX_KEY_FILE, then the legacy
        TRUEX_KEY_ID / TRUEX_KEY_SECRET HMAC pair.
        """
        key_file = key_file or os.getenv("TRUEX_KEY_FILE")
        if key_file:
            return cls.from_key_file(key_file)

        key_id = os.getenv("TRUEX_KEY_ID")
        secret = os.getenv("TRUEX_KEY_SECRET")
        if key_id and secret:
            return cls(key_id=key_id, hmac_secret=secret)

        raise ValueError(
            "No API key configured: pass --key-file (or set TRUEX_KEY_FILE) for an "
            "ES256 key, or set TRUEX_KEY_ID and TRUEX_KEY_SECRET for an HMAC key."
        )

    def sign(self, message: str) -> str:
        message_bytes = message.encode("utf-8")
        if self.hmac_secret is not None:
            digest = hmac.new(
                self.hmac_secret.encode("utf-8"), message_bytes, hashlib.sha256
            ).digest()
            return base64.b64encode(digest).decode("utf-8")

        der = self.private_key.sign(message_bytes, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(P256_COORD_LEN, "big") + s.to_bytes(P256_COORD_LEN, "big")
        return _b64url_encode(raw)

    def logon_password(
        self, sending_time, msg_type, msg_seq_num, sender_comp_id, target_comp_id
    ) -> str:
        return self.sign(
            logon_payload(
                sending_time,
                msg_type,
                msg_seq_num,
                sender_comp_id,
                target_comp_id,
                self.key_id,
            )
        )
