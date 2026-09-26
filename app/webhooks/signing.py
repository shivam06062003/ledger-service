"""Webhook signatures (the same scheme Stripe uses).

Header:  Ledger-Signature: t=1727366400,v1=5f2b...e1

v1 = hex(HMAC-SHA256(secret, f"{t}.{raw_body}"))

- HMAC proves the payload came from us and wasn't modified: only we and the
  receiver know the secret.
- Signing the timestamp together with the body lets receivers reject old
  captured requests (replay attacks): verify rejects anything outside the
  tolerance window.
- Receivers must verify against the RAW request bytes, before any JSON parsing,
  since re-serialising can change whitespace or key order.

This module has no app dependencies, so receivers can copy it as-is.
"""

import hashlib
import hmac
import time

SIGNATURE_HEADER = "Ledger-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


class InvalidSignatureError(Exception):
    pass


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute_signature(secret, ts, body)}"


def verify_signature(
    secret: str,
    header: str,
    body: bytes,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: int | None = None,
) -> None:
    """Raise InvalidSignatureError unless `header` is a valid, fresh signature of `body`."""
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1":
            # Several v1 values may be present, e.g. during secret rotation.
            signatures.append(value)
    if timestamp is None or not signatures:
        raise InvalidSignatureError("Malformed signature header")

    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise InvalidSignatureError("Timestamp outside the tolerance window")

    expected = compute_signature(secret, timestamp, body)
    # compare_digest takes the same time whether the first or last character
    # differs, so an attacker can't recover the signature byte-by-byte by timing.
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise InvalidSignatureError("Signature mismatch")
