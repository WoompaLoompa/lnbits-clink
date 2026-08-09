"""Unit tests for the CLINK subscription engine."""

import json
from datetime import datetime, timezone

import pytest
from clink.models import Subscription
from clink.nostr import (
    NDebit,
    decrypt_with_keys,
    encrypt_with_keys,
    generate_keypair,
    pubkey_from_privkey,
    verify_event,
)
from clink.nostr.events import CLINK_VERSION_TAG, KIND_DEBIT_REQUEST, finalize
from clink.subscriptions import (
    MAX_RENEWAL_ATTEMPTS,
    SubscriptionError,
    add_frequency,
    build_debit_request,
    failed_renewal_state,
    is_due,
    parse_debit_response,
)

SERVICE_PRIV, SERVICE_PUB = generate_keypair()
PAYER_PRIV, PAYER_PUB = generate_keypair()
RELAY = "wss://relay.example.com"


def make_ndebit(pointer="ptr-1"):
    return NDebit(pubkey=SERVICE_PUB, relay=RELAY, pointer=pointer)


def make_sub(**overrides):
    fields = {
        "wallet": "w1",
        "plan_id": "p1",
        "ndebit": "ndebit1...",
        "state": "active",
    }
    fields.update(overrides)
    return Subscription(**fields)


# ---------------------------------------------------------------------------
# add_frequency
# ---------------------------------------------------------------------------


def test_add_frequency_day():
    start = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    assert add_frequency(start, 1, "day") == datetime(
        2026, 8, 9, 12, 0, tzinfo=timezone.utc
    )


def test_add_frequency_week():
    start = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    assert add_frequency(start, 1, "week") == datetime(
        2026, 8, 15, 12, 0, tzinfo=timezone.utc
    )


def test_add_frequency_month():
    start = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert add_frequency(start, 1, "month") == datetime(
        2026, 2, 15, 12, 0, tzinfo=timezone.utc
    )


def test_add_frequency_month_clamps_day():
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert add_frequency(start, 1, "month") == datetime(
        2026, 2, 28, tzinfo=timezone.utc
    )


def test_add_frequency_month_rolls_year():
    start = datetime(2026, 12, 15, tzinfo=timezone.utc)
    assert add_frequency(start, 1, "month") == datetime(
        2027, 1, 15, tzinfo=timezone.utc
    )


def test_add_frequency_month_multiple():
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert add_frequency(start, 2, "month") == datetime(
        2026, 3, 31, tzinfo=timezone.utc
    )


def test_add_frequency_unknown_unit():
    start = datetime(2026, 8, 8, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        add_frequency(start, 1, "year")


# ---------------------------------------------------------------------------
# is_due / failed_renewal_state
# ---------------------------------------------------------------------------


def test_is_due_active_expired():
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    sub = make_sub(current_period_end=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert is_due(sub, now)


def test_is_due_active_future():
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    sub = make_sub(current_period_end=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert not is_due(sub, now)


def test_is_due_paused_ignored():
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    sub = make_sub(
        state="paused",
        current_period_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert not is_due(sub, now)


def test_is_due_no_period_never_billed():
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    assert is_due(make_sub(current_period_end=None), now)


def test_failed_renewal_state():
    assert failed_renewal_state(0) == "active"
    assert failed_renewal_state(MAX_RENEWAL_ATTEMPTS - 1) == "cancelled"
    assert failed_renewal_state(MAX_RENEWAL_ATTEMPTS) == "cancelled"


# ---------------------------------------------------------------------------
# build_debit_request
# ---------------------------------------------------------------------------


def test_build_debit_request_payload_and_tags():
    decoded = make_ndebit()
    event, payer_privkey = build_debit_request(decoded, 1000, "lnbc...", "Monthly plan")
    assert verify_event(event)
    assert event["kind"] == KIND_DEBIT_REQUEST
    assert ["p", SERVICE_PUB] in event["tags"]
    assert CLINK_VERSION_TAG in event["tags"]
    assert event["pubkey"] == pubkey_from_privkey(payer_privkey)

    payload = json.loads(
        decrypt_with_keys(
            event["content"], SERVICE_PRIV, pubkey_from_privkey(payer_privkey)
        )
    )
    assert payload == {
        "amount_sats": 1000,
        "bolt11": "lnbc...",
        "pointer": "ptr-1",
        "description": "Monthly plan",
    }


def test_build_debit_request_omits_pointer():
    decoded = make_ndebit(pointer=None)
    event, payer_privkey = build_debit_request(decoded, 500, "lnbc...")
    payload = json.loads(
        decrypt_with_keys(
            event["content"], SERVICE_PRIV, pubkey_from_privkey(payer_privkey)
        )
    )
    assert "pointer" not in payload
    assert payload["amount_sats"] == 500


def test_build_debit_request_no_description():
    decoded = make_ndebit()
    event, payer_privkey = build_debit_request(decoded, 500, "lnbc...")
    payload = json.loads(
        decrypt_with_keys(
            event["content"], SERVICE_PRIV, pubkey_from_privkey(payer_privkey)
        )
    )
    assert "description" not in payload


# ---------------------------------------------------------------------------
# parse_debit_response
# ---------------------------------------------------------------------------


def make_response(payload: dict, sender_priv: str = SERVICE_PRIV) -> dict:
    content = encrypt_with_keys(
        json.dumps(payload, separators=(",", ":")), sender_priv, PAYER_PUB
    )
    return finalize(
        sender_priv,
        KIND_DEBIT_REQUEST,
        content,
        [["p", PAYER_PUB], ["e", "req-1"], CLINK_VERSION_TAG],
    )


def test_parse_debit_response_ok():
    response = make_response({"res": "ok", "preimage": "abc123"})
    parsed = parse_debit_response(response, PAYER_PRIV, SERVICE_PUB)
    assert parsed["res"] == "ok"
    assert parsed["preimage"] == "abc123"


def test_parse_debit_response_gfy():
    response = make_response({"res": "GFY", "code": 1, "error": "Request Denied"})
    parsed = parse_debit_response(response, PAYER_PRIV, SERVICE_PUB)
    assert parsed["res"] == "GFY"
    assert parsed["code"] == 1


def test_parse_debit_response_wrong_sender():
    third_priv, _ = generate_keypair()
    response = make_response({"res": "ok"}, third_priv)
    with pytest.raises(SubscriptionError):
        parse_debit_response(response, PAYER_PRIV, SERVICE_PUB)


def test_parse_debit_response_bad_signature():
    response = make_response({"res": "ok"})
    response["content"] = "tampered"
    with pytest.raises(SubscriptionError):
        parse_debit_response(response, PAYER_PRIV, SERVICE_PUB)
