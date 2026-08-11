"""Unit tests for the CLINK funding source (ClinkWallet)."""

import asyncio

from clink.nostr import NOffer, encode_noffer, generate_keypair, pubkey_from_privkey
from clink.nostr.bech32 import PRICE_TYPE_FIXED, PRICE_TYPE_SPONTANEOUS
from clink.wallet import (
    ACCOUNT_ENV_VAR,
    NOFFER_ENV_VAR,
    ClinkWallet,
    _configured_account,
    _configured_noffer,
)

try:
    from lnbits.settings import settings as _settings
except ImportError:
    _settings = None

RELAY = "wss://relay.example.com"


def _service_pubkey():
    priv, _ = generate_keypair()
    return pubkey_from_privkey(priv)


def _noffer_str(pubkey, price_type=PRICE_TYPE_SPONTANEOUS, price=None):
    return encode_noffer(
        NOffer(
            pubkey=pubkey,
            relay=RELAY,
            offer="o1",
            price_type=price_type,
            price=price,
        )
    )


def _account_str(pubkey):
    from clink.nostr import NProfile, encode_nprofile

    return encode_nprofile(NProfile(pubkey=pubkey, relays=[RELAY]))


def _patch_setting(monkeypatch, name, value):
    if _settings is not None and hasattr(_settings, name):
        monkeypatch.setattr(_settings, name, value or "")


def _patch_noffer(monkeypatch, value):
    monkeypatch.setenv(NOFFER_ENV_VAR, value or "")
    _patch_setting(monkeypatch, "lnbits_clink_funding_noffer", value)


def _patch_account(monkeypatch, value):
    monkeypatch.setenv(ACCOUNT_ENV_VAR, value or "")
    _patch_setting(monkeypatch, "lnbits_clink_funding_account", value)


def test_configured_noffer_unset(monkeypatch):
    _patch_noffer(monkeypatch, None)
    assert _configured_noffer() is None


def test_configured_noffer_blank(monkeypatch):
    _patch_noffer(monkeypatch, "   ")
    assert _configured_noffer() is None


def test_configured_noffer_set(monkeypatch):
    noffer = _noffer_str(_service_pubkey())
    _patch_noffer(monkeypatch, noffer)
    assert _configured_noffer() == noffer


def test_configured_account_unset(monkeypatch):
    _patch_account(monkeypatch, None)
    assert _configured_account() is None


def test_configured_account_set(monkeypatch):
    account = _account_str(_service_pubkey())
    _patch_account(monkeypatch, account)
    assert _configured_account() == account


def test_status_unconfigured(monkeypatch):
    _patch_noffer(monkeypatch, None)
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message
    assert NOFFER_ENV_VAR in status.error_message
    assert status.balance_msat == 0


def test_status_invalid_noffer(monkeypatch):
    _patch_noffer(monkeypatch, "noffer1invalid")
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message


def test_status_configured(monkeypatch):
    _patch_noffer(monkeypatch, _noffer_str(_service_pubkey()))
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message is None
    assert status.balance_msat == 0


def test_status_account_balance(monkeypatch):
    _patch_account(monkeypatch, _account_str(_service_pubkey()))

    async def fake_get_user_info(account):
        return {"userId": "u1", "balance": 1234}

    monkeypatch.setattr("clink.wallet.get_user_info", fake_get_user_info)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message is None
    assert status.balance_msat == 1_234_000


def test_status_account_error(monkeypatch):
    _patch_account(monkeypatch, _account_str(_service_pubkey()))

    async def fake_get_user_info(account):
        raise RuntimeError("node down")

    monkeypatch.setattr("clink.wallet.get_user_info", fake_get_user_info)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message
    assert status.balance_msat == 0


def test_create_invoice_unconfigured(monkeypatch):
    _patch_noffer(monkeypatch, None)
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    invoice = await_wallet_create_invoice(wallet, 1000)
    assert invoice.ok is False
    assert invoice.error_message
    assert NOFFER_ENV_VAR in invoice.error_message


def test_create_invoice_fixed_mismatch_rejects_before_network(monkeypatch):
    _patch_noffer(
        monkeypatch, _noffer_str(_service_pubkey(), PRICE_TYPE_FIXED, 1000)
    )
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    invoice = await_wallet_create_invoice(wallet, 999)
    assert invoice.ok is False
    assert "Invalid Amount" in invoice.error_message


def test_pay_invoice_receive_only(monkeypatch):
    _patch_noffer(monkeypatch, None)
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    payment = await_wallet_pay_invoice(wallet, "lnbc1test", 1000)
    assert payment.ok is False
    assert "receive-only" in payment.error_message
    assert ACCOUNT_ENV_VAR in payment.error_message


def test_invoice_status_pending(monkeypatch):
    _patch_account(monkeypatch, None)
    wallet = ClinkWallet()
    status = await_wallet_invoice_status(wallet, "any")
    assert status.paid is None


def test_invoice_status_paid_via_operations(monkeypatch):
    from clink.models import Invoice
    from clink.wallet import get_invoice_by_hash

    bolt11 = "lnbc210n1p4testinvoice"
    monkeypatch.setattr(
        "clink.wallet.get_invoice_by_hash",
        _fake_get_invoice_by_hash(Invoice(payment_hash="h", bolt11=bolt11)),
    )

    async def fake_get_user_operations(account, cursor=(0, 0), max_size=100):
        assert cursor == (0, 0)
        return {
            "latestIncomingInvoiceOperations": {
                "fromIndex": {"ts": 1, "id": 1},
                "toIndex": {"ts": 1, "id": 1},
                "operations": [
                    {
                        "identifier": bolt11,
                        "paidAtUnix": 123,
                        "service_fee": 0,
                        "network_fee": 1,
                    }
                ],
            }
        }

    monkeypatch.setattr("clink.wallet.account_get_user_operations", fake_get_user_operations)
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    status = await_wallet_invoice_status(wallet, "h")
    assert status.paid is True
    assert status.fee_msat == 1000


def test_invoice_status_pages_until_found(monkeypatch):
    from clink.models import Invoice

    bolt11 = "lnbc210n1p4testinvoice2"
    monkeypatch.setattr(
        "clink.wallet.get_invoice_by_hash",
        _fake_get_invoice_by_hash(Invoice(payment_hash="h", bolt11=bolt11)),
    )

    calls = {"n": 0}

    async def fake_get_user_operations(account, cursor=(0, 0), max_size=100):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "latestIncomingInvoiceOperations": {
                    "fromIndex": {"ts": 1, "id": 1},
                    "toIndex": {"ts": 2, "id": 2},
                    "operations": [
                        {"identifier": "lnbc1other", "paidAtUnix": 1, "service_fee": 0, "network_fee": 0}
                    ],
                }
            }
        assert cursor == (2, 2)
        return {
            "latestIncomingInvoiceOperations": {
                "fromIndex": {"ts": 3, "id": 3},
                "toIndex": {"ts": 3, "id": 3},
                "operations": [
                    {"identifier": bolt11, "paidAtUnix": 3, "service_fee": 0, "network_fee": 0}
                ],
            }
        }

    monkeypatch.setattr("clink.wallet.account_get_user_operations", fake_get_user_operations)
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    status = await_wallet_invoice_status(wallet, "h")
    assert status.paid is True
    assert calls["n"] == 2


def test_invoice_status_no_account_row(monkeypatch):
    from clink.models import Invoice

    bolt11 = "lnbc210n1p4testinvoice3"
    monkeypatch.setattr(
        "clink.wallet.get_invoice_by_hash",
        _fake_get_invoice_by_hash(Invoice(payment_hash="h", bolt11=bolt11)),
    )

    async def fake_get_user_operations(account, cursor=(0, 0), max_size=100):
        return {"latestIncomingInvoiceOperations": {"fromIndex": {"ts": 0, "id": 0}, "toIndex": {"ts": 0, "id": 0}, "operations": []}}

    monkeypatch.setattr("clink.wallet.account_get_user_operations", fake_get_user_operations)
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    status = await_wallet_invoice_status(wallet, "h")
    assert status.paid is None


def test_pay_invoice_success_without_preimage(monkeypatch):
    bolt11 = "lnbc210n1p4internal"
    monkeypatch.setattr(
        "clink.wallet.bolt11_decode",
        lambda value: type("D", (), {"payment_hash": "h1"})(),
    )

    async def fake_pay_invoice(account, bolt11_):
        return {
            "preimage": "",
            "amount_paid": 11,
            "service_fee": 10,
            "network_fee": 0,
            "operation_id": "OUTGOING_INVOICE-1",
        }

    monkeypatch.setattr("clink.wallet.account_pay_invoice", fake_pay_invoice)
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    payment = await_wallet_pay_invoice(wallet, bolt11, 1000)
    assert payment.ok is True
    assert payment.checking_id == "h1"
    assert payment.fee_msat == 10000
    assert payment.preimage is None


def test_payment_status_outgoing_uses_get_payment_state(monkeypatch):
    from clink.models import Invoice

    bolt11 = "lnbc210n1p4outgoing"
    monkeypatch.setattr(
        "clink.wallet.get_invoice_by_hash",
        _fake_get_invoice_by_hash(Invoice(payment_hash="h", bolt11=bolt11)),
    )

    async def fake_get_payment_state(account, bolt11_):
        assert bolt11_ == bolt11
        return {"paid_at_unix": 42, "service_fee": 2, "network_fee": 3}

    monkeypatch.setattr("clink.wallet.account_get_payment_state", fake_get_payment_state)
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    status = asyncio.run(wallet.get_payment_status("h"))
    assert status.paid is True
    assert status.fee_msat == 5000


def test_create_invoice_prefers_account(monkeypatch):
    from lnbits.wallets.base import InvoiceResponse as LnbInvoiceResponse

    _patch_noffer(monkeypatch, _noffer_str(_service_pubkey()))
    _patch_account(monkeypatch, _account_str(_service_pubkey()))
    wallet = ClinkWallet()
    called = {"account": False, "offer": False}

    async def fake_account(self, account, amount, memo=None):
        called["account"] = True
        return LnbInvoiceResponse(True, checking_id="a", payment_request="lnbc1a")

    async def fake_offer(self, noffer, amount, memo=None):
        called["offer"] = True
        return LnbInvoiceResponse(True, checking_id="b", payment_request="lnbc1b")

    monkeypatch.setattr(ClinkWallet, "_create_invoice_account", fake_account)
    monkeypatch.setattr(ClinkWallet, "_create_invoice_offer", fake_offer)
    invoice = await_wallet_create_invoice(wallet, 1000)
    assert invoice.ok is True
    assert called["account"] is True
    assert called["offer"] is False


def _fake_get_invoice_by_hash(invoice):
    async def fake(checking_id):
        return invoice

    return fake


def await_wallet_status(wallet):
    return asyncio.run(wallet.status())


def await_wallet_create_invoice(wallet, amount):
    return asyncio.run(wallet.create_invoice(amount=amount, memo="test"))


def await_wallet_pay_invoice(wallet, bolt11, fee_limit_msat):
    return asyncio.run(wallet.pay_invoice(bolt11, fee_limit_msat))


def await_wallet_invoice_status(wallet, checking_id):
    return asyncio.run(wallet.get_invoice_status(checking_id))
