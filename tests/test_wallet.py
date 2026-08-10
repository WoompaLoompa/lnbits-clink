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


def await_wallet_status(wallet):
    return asyncio.run(wallet.status())


def await_wallet_create_invoice(wallet, amount):
    return asyncio.run(wallet.create_invoice(amount=amount, memo="test"))


def await_wallet_pay_invoice(wallet, bolt11, fee_limit_msat):
    return asyncio.run(wallet.pay_invoice(bolt11, fee_limit_msat))


def await_wallet_invoice_status(wallet, checking_id):
    return asyncio.run(wallet.get_invoice_status(checking_id))
