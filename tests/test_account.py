"""Unit tests for the Lightning.pub user API client (clink.account)."""

import asyncio
import json

import pytest
from clink.account import (
    AccountError,
    account_request,
    derive_keys,
    get_payment_state,
    get_user_info,
    new_invoice,
    parse_account,
    pay_invoice,
)
from clink.nostr import NProfile, encode_nprofile, generate_keypair, pubkey_from_privkey
from clink.nostr.events import KIND_USER_API, finalize
from clink.nostr.nip44v1 import decrypt as decrypt_v1
from clink.nostr.nip44v1 import encrypt as encrypt_v1
from clink.nostr.nip44v1 import get_conversation_key

RELAY = "wss://relay.example.com"


def _node_keypair():
    priv, _ = generate_keypair()
    return priv, pubkey_from_privkey(priv)


def _account_str(node_pub):
    return encode_nprofile(NProfile(pubkey=node_pub, relays=[RELAY]))


def _decrypt_request(request_event, requester_priv, node_pub):
    conv = get_conversation_key(requester_priv, node_pub)
    return json.loads(decrypt_v1(request_event["content"], conv))


def _build_response(request_event, requester_priv, node_pub, node_priv, payload):
    conv = get_conversation_key(requester_priv, node_pub)
    content = encrypt_v1(json.dumps(payload, separators=(",", ":")), conv)
    return finalize(
        node_priv,
        KIND_USER_API,
        content,
        [["p", request_event["pubkey"]]],
    )


def _account_fixture(monkeypatch):
    node_priv, node_pub = _node_keypair()
    account = _account_str(node_pub)

    async def fake_relay_list(hint):
        return [hint]

    monkeypatch.setattr("clink.account._relay_list", fake_relay_list)
    return account, node_priv, node_pub


def _install_responder(monkeypatch, responder):
    async def fake_request_response(
        relays, request, response_filter, timeout, match=None
    ):
        return responder(request)

    monkeypatch.setattr("clink.account.request_response", fake_request_response)


def test_parse_account_variants():
    _, node_pub = _node_keypair()
    account = _account_str(node_pub)
    profile, token = parse_account(account)
    assert profile.pubkey == node_pub
    assert profile.relay == RELAY
    assert token is None

    profile, token = parse_account(f"{account}:tok3n_ab-2")
    assert token == "tok3n_ab-2"

    with pytest.raises(ValueError):
        parse_account(f"{account}:bad token!")
    with pytest.raises(ValueError):
        parse_account("npub1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq")


def test_derive_keys_deterministic():
    _, node_pub = _node_keypair()
    account = _account_str(node_pub)
    priv1, pub1 = derive_keys(account)
    priv2, pub2 = derive_keys(account)
    assert priv1 == priv2
    assert pub1 == pub2
    assert pubkey_from_privkey(priv1) == pub1


def test_account_request_ok(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)
    captured = {}

    def responder(request):
        captured["request"] = request
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        captured["payload"] = request_payload
        assert request_payload["rpcName"] == "GetUserInfo"
        assert request_payload["authIdentifier"] == request["pubkey"]
        assert request["tags"][0] == ["p", node_pub]
        return _build_response(
            request,
            requester_priv,
            node_pub,
            node_priv,
            {
                "status": "OK",
                "userId": "user_1",
                "balance": 1234,
                "requestId": request_payload["requestId"],
            },
        )

    _install_responder(monkeypatch, responder)

    info = asyncio.run(account_request(account, "GetUserInfo"))
    assert info["userId"] == "user_1"
    assert info["balance"] == 1234


def test_account_request_error_status(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)

    def responder(request):
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        return _build_response(
            request,
            requester_priv,
            node_pub,
            node_priv,
            {
                "status": "ERROR",
                "reason": "insufficient balance",
                "requestId": request_payload["requestId"],
            },
        )

    _install_responder(monkeypatch, responder)

    with pytest.raises(AccountError) as exc_info:
        asyncio.run(account_request(account, "PayInvoice", body={"invoice": "lnbc1x"}))
    assert exc_info.value.reason == "insufficient balance"
    assert "insufficient balance" in str(exc_info.value)


def test_account_request_timeout(monkeypatch):
    account, _node_priv, _node_pub = _account_fixture(monkeypatch)

    def responder(request):
        raise asyncio.TimeoutError()

    _install_responder(monkeypatch, responder)

    with pytest.raises(AccountError):
        asyncio.run(account_request(account, "GetUserInfo"))


def test_account_request_skips_stale_response(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)

    async def fake_request_response(relays, request, response_filter, timeout, match=None):
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        correct = _build_response(
            request,
            requester_priv,
            node_pub,
            node_priv,
            {
                "status": "OK",
                "userId": "user_1",
                "balance": 777,
                "requestId": request_payload["requestId"],
            },
        )
        stale = _build_response(
            request,
            requester_priv,
            node_pub,
            node_priv,
            {
                "status": "OK",
                "userId": "user_1",
                "balance": 111,
                "requestId": "0" * 16,
            },
        )
        assert match is not None
        assert match(stale) is False
        assert match(correct) is True
        return correct

    monkeypatch.setattr("clink.account.request_response", fake_request_response)

    info = asyncio.run(account_request(account, "GetUserInfo"))
    assert info["balance"] == 777


def test_user_api_helpers(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)

    class _FakeDecoded:
        amount_msat = 1000

    monkeypatch.setattr("clink.account.bolt11_decode", lambda bolt11: _FakeDecoded())

    def responder(request):
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        rpc_name = request_payload["rpcName"]
        if rpc_name == "GetUserInfo":
            result = {
                "status": "OK",
                "balance": 5000,
                "requestId": request_payload["requestId"],
            }
        elif rpc_name == "NewInvoice":
            result = {
                "status": "OK",
                "invoice": "lnbc100n1test",
                "requestId": request_payload["requestId"],
            }
        elif rpc_name == "PayInvoice":
            result = {
                "status": "OK",
                "preimage": "ab" * 32,
                "amount_paid": 100,
                "operation_id": "op_1",
                "service_fee": 1,
                "network_fee": 2,
                "latest_balance": 4899,
                "requestId": request_payload["requestId"],
            }
        else:
            result = {
                "status": "OK",
                "paid_at_unix": 1710000000,
                "amount": 100,
                "service_fee": 1,
                "network_fee": 2,
                "operation_id": "op_1",
                "requestId": request_payload["requestId"],
            }
        return _build_response(request, requester_priv, node_pub, node_priv, result)

    _install_responder(monkeypatch, responder)

    info = asyncio.run(get_user_info(account))
    assert info["balance"] == 5000

    bolt11 = asyncio.run(new_invoice(account, 1000, memo="test"))
    assert bolt11 == "lnbc100n1test"

    pay = asyncio.run(pay_invoice(account, "lnbc1x", amount_sats=100))
    assert pay["preimage"] == "ab" * 32
    assert pay["amount_paid"] == 100

    state = asyncio.run(get_payment_state(account, "lnbc1x"))
    assert state["paid_at_unix"] == 1710000000


def test_pay_invoice_fixed_amount_sends_zero(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)
    sent_body = {}

    def responder(request):
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        sent_body.update(request_payload.get("body") or {})
        result = {
            "status": "OK",
            "preimage": "cd" * 32,
            "requestId": request_payload["requestId"],
        }
        return _build_response(request, requester_priv, node_pub, node_priv, result)

    _install_responder(monkeypatch, responder)

    class _FakeDecoded:
        amount_msat = 11000

    monkeypatch.setattr("clink.account.bolt11_decode", lambda bolt11: _FakeDecoded())

    pay = asyncio.run(pay_invoice(account, "lnbc110n1whatever"))
    assert pay["preimage"] == "cd" * 32
    assert sent_body == {"invoice": "lnbc110n1whatever", "amount": 0}


def test_pay_invoice_amountless_uses_explicit_amount(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)
    sent_body = {}

    def responder(request):
        requester_priv, _ = derive_keys(account)
        request_payload = _decrypt_request(request, requester_priv, node_pub)
        sent_body.update(request_payload.get("body") or {})
        result = {
            "status": "OK",
            "preimage": "ef" * 32,
            "requestId": request_payload["requestId"],
        }
        return _build_response(request, requester_priv, node_pub, node_priv, result)

    _install_responder(monkeypatch, responder)

    class _FakeDecoded:
        amount_msat = None

    monkeypatch.setattr("clink.account.bolt11_decode", lambda bolt11: _FakeDecoded())

    pay = asyncio.run(pay_invoice(account, "lnbc1x", amount_sats=7))
    assert pay["preimage"] == "ef" * 32
    assert sent_body == {"invoice": "lnbc1x", "amount": 7}


def test_pay_invoice_amountless_without_amount_rejected(monkeypatch):
    account, node_priv, node_pub = _account_fixture(monkeypatch)

    class _FakeDecoded:
        amount_msat = None

    monkeypatch.setattr("clink.account.bolt11_decode", lambda bolt11: _FakeDecoded())

    with pytest.raises(AccountError, match="amountless"):
        asyncio.run(pay_invoice(account, "lnbc1whatever"))
