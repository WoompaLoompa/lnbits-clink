"""Unit tests for the CLINK node service logic."""

import json

from clink.node import parse_rules, period_bucket, resolve_offer_amount
from clink.nostr import NOffer, decode_noffer, encode_noffer
from clink.nostr.bech32 import (
    PRICE_TYPE_FIXED,
    PRICE_TYPE_SPONTANEOUS,
    PRICE_TYPE_VARIABLE,
)

PUBKEY = "71b70bace4867c80be6b083d449c37486f33790df0ea4bbe6e9ce617bee34b66"
RELAY = "wss://relay.example.com"


def make_noffer(price_type=PRICE_TYPE_SPONTANEOUS, price=None):
    encoded = encode_noffer(
        NOffer(
            pubkey=PUBKEY, relay=RELAY, offer="o1", price_type=price_type, price=price
        )
    )
    return decode_noffer(encoded)


def test_fixed_exact_amount():
    decoded = make_noffer(PRICE_TYPE_FIXED, 5000)
    amount, error = resolve_offer_amount(decoded, 5000)
    assert error is None
    assert amount == 5000


def test_fixed_amount_ignored_when_absent():
    decoded = make_noffer(PRICE_TYPE_FIXED, 5000)
    amount, error = resolve_offer_amount(decoded, None)
    assert error is None
    assert amount == 5000


def test_fixed_mismatch_rejects():
    decoded = make_noffer(PRICE_TYPE_FIXED, 5000)
    amount, error = resolve_offer_amount(decoded, 4999)
    assert amount is None
    assert error["code"] == 5
    assert error["range"] == {"min": 5000, "max": 5000}


def test_spontaneous_requires_amount():
    decoded = make_noffer(PRICE_TYPE_SPONTANEOUS)
    amount, error = resolve_offer_amount(decoded, None)
    assert amount is None
    assert error["code"] == 5
    assert error["range"]["min"] == 1


def test_spontaneous_uses_requested():
    decoded = make_noffer(PRICE_TYPE_SPONTANEOUS)
    amount, error = resolve_offer_amount(decoded, 12345)
    assert error is None
    assert amount == 12345


def test_variable_defaults_to_price():
    decoded = make_noffer(PRICE_TYPE_VARIABLE, 700)
    amount, error = resolve_offer_amount(decoded, None)
    assert error is None
    assert amount == 700


def test_variable_uses_requested():
    decoded = make_noffer(PRICE_TYPE_VARIABLE, 700)
    amount, error = resolve_offer_amount(decoded, 800)
    assert error is None
    assert amount == 800


def test_period_bucket_day():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    assert period_bucket(1, "day", now) == "2026-08-08"


def test_period_bucket_week():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    bucket = period_bucket(1, "week", now)
    assert bucket.startswith("2026-W")
    assert "-W3" in bucket


def test_period_bucket_month():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    assert period_bucket(1, "month", now) == "2026-08"


def test_period_bucket_unknown_unit():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    try:
        period_bucket(1, "year", now)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_parse_rules_valid_json():
    rules = json.dumps({"allowed_pubkeys": ["abc"], "min_msat": 1000})
    assert parse_rules(rules) == {"allowed_pubkeys": ["abc"], "min_msat": 1000}


def test_parse_rules_bad_input():
    assert parse_rules(None) == {}
    assert parse_rules("not json") == {}
    assert parse_rules('"just a string"') == {}
