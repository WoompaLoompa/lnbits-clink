"""Unit tests for the CLINK Pay Offers (outgoing) flow."""

import json

from clink.nostr import NOffer, decode_noffer, encode_noffer, pubkey_from_privkey
from clink.nostr.bech32 import PRICE_TYPE_FIXED, PRICE_TYPE_SPONTANEOUS
from clink.nostr.events import (
    CLINK_VERSION_TAG,
    KIND_OFFER_REQUEST,
    finalize,
)
from clink.nostr.nip44 import decrypt_with_keys, encrypt_with_keys
from clink.pay import (
    PayOfferError,
    build_offer_request,
    build_offer_request_payload,
    parse_offer_response,
    resolve_outgoing_amount,
)

RELAY = "wss://relay.example.com"


def _service_keypair():
    from clink.nostr import generate_keypair

    priv, _ = generate_keypair()
    return priv, pubkey_from_privkey(priv)


def _service_pubkey():
    _, pub = _service_keypair()
    return pub


def _noffer(service_pubkey, price_type=PRICE_TYPE_SPONTANEOUS, price=None):
    encoded = encode_noffer(
        NOffer(
            pubkey=service_pubkey,
            relay=RELAY,
            offer="o1",
            price_type=price_type,
            price=price,
        )
    )
    return decode_noffer(encoded)


def test_outgoing_fixed_exact():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    amount, error = resolve_outgoing_amount(decoded, 1000)
    assert error is None
    assert amount == 1000


def test_outgoing_fixed_absent_uses_price():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    amount, error = resolve_outgoing_amount(decoded, None)
    assert error is None
    assert amount == 1000


def test_outgoing_fixed_mismatch_rejects():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    amount, error = resolve_outgoing_amount(decoded, 999)
    assert amount is None
    assert error["code"] == 5
    assert error["range"] == {"min": 1000, "max": 1000}


def test_outgoing_fixed_without_price_rejects():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, None)
    amount, error = resolve_outgoing_amount(decoded, 1000)
    assert amount is None
    assert error["code"] == 1


def test_outgoing_spontaneous_requires_amount():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_SPONTANEOUS)
    amount, error = resolve_outgoing_amount(decoded, None)
    assert amount is None
    assert error["code"] == 5


def test_outgoing_spontaneous_uses_amount():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_SPONTANEOUS)
    amount, error = resolve_outgoing_amount(decoded, 5000)
    assert error is None
    assert amount == 5000


def test_outgoing_zero_amount_rejects():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_SPONTANEOUS)
    amount, error = resolve_outgoing_amount(decoded, 0)
    assert amount is None
    assert error["code"] == 5


def test_build_offer_request_payload():
    service_pub = _service_pubkey()
    decoded = _noffer(service_pub, PRICE_TYPE_SPONTANEOUS)
    payload = build_offer_request_payload(decoded, 100, "tip")
    assert payload == {"offer": "o1", "amount_sats": 100, "description": "tip"}
    assert build_offer_request_payload(decoded, 100) == {
        "offer": "o1",
        "amount_sats": 100,
    }


def test_build_offer_request_event():
    service_priv, service_pub = _service_keypair()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    event, payer_privkey = build_offer_request(decoded, 1000)
    assert event["kind"] == KIND_OFFER_REQUEST
    assert event["pubkey"] == pubkey_from_privkey(payer_privkey)
    assert ["p", service_pub] in event["tags"]
    assert CLINK_VERSION_TAG in event["tags"]
    payload = json.loads(
        decrypt_with_keys(event["content"], service_priv, event["pubkey"])
    )
    assert payload == {"offer": "o1", "amount_sats": 1000}


def test_parse_offer_response_roundtrip():
    service_priv, service_pub = _service_keypair()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    event, payer_privkey = build_offer_request(decoded, 1000)
    content = encrypt_with_keys(
        json.dumps({"bolt11": "lnbc1000n1..."}, separators=(",", ":")),
        service_priv,
        event["pubkey"],
    )
    response_event = finalize(
        service_priv,
        KIND_OFFER_REQUEST,
        content,
        [["p", event["pubkey"]], ["e", event["id"]], CLINK_VERSION_TAG],
    )
    parsed = parse_offer_response(response_event, payer_privkey, service_pub)
    assert parsed == {"bolt11": "lnbc1000n1..."}


def test_parse_offer_response_rejects_wrong_sender():
    service_pub = _service_pubkey()
    attacker_priv, _ = _service_keypair()
    decoded = _noffer(service_pub, PRICE_TYPE_FIXED, 1000)
    event, payer_privkey = build_offer_request(decoded, 1000)
    content = encrypt_with_keys(
        json.dumps({"bolt11": "lnbc1000n1..."}),
        attacker_priv,
        event["pubkey"],
    )
    response_event = finalize(
        attacker_priv,
        KIND_OFFER_REQUEST,
        content,
        [["p", event["pubkey"]], ["e", event["id"]], CLINK_VERSION_TAG],
    )
    try:
        parse_offer_response(response_event, payer_privkey, service_pub)
        raise AssertionError("expected PayOfferError")
    except PayOfferError:
        pass
