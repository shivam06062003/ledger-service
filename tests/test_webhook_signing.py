import pytest

from app.webhooks.signing import InvalidSignatureError, sign, verify_signature

SECRET = "whsec_test"
BODY = b'{"id":"evt_1","type":"transfer.created"}'
NOW = 1_800_000_000


def test_valid_signature_verifies() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)

    verify_signature(SECRET, header, BODY, now=NOW)


def test_tampered_body_is_rejected() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)

    with pytest.raises(InvalidSignatureError, match="mismatch"):
        verify_signature(SECRET, header, BODY.replace(b"transfer", b"refund"), now=NOW)


def test_wrong_secret_is_rejected() -> None:
    header = sign("whsec_attacker", BODY, timestamp=NOW)

    with pytest.raises(InvalidSignatureError):
        verify_signature(SECRET, header, BODY, now=NOW)


def test_replayed_old_request_is_rejected() -> None:
    header = sign(SECRET, BODY, timestamp=NOW - 301)

    with pytest.raises(InvalidSignatureError, match="tolerance"):
        verify_signature(SECRET, header, BODY, now=NOW)


def test_accepts_any_matching_signature_for_secret_rotation() -> None:
    old = sign("whsec_old", BODY, timestamp=NOW).split(",")[1]
    new = sign(SECRET, BODY, timestamp=NOW).split(",")[1]

    verify_signature(SECRET, f"t={NOW},{old},{new}", BODY, now=NOW)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=00", f"t={NOW}"])
def test_malformed_header_is_rejected(header: str) -> None:
    with pytest.raises(InvalidSignatureError, match="Malformed"):
        verify_signature(SECRET, header, BODY, now=NOW)
