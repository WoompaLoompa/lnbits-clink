"""Unit tests for the CLINK funding source (ClinkWallet)."""

from clink.nostr import NOffer, encode_noffer
from clink.nostr.bech32 import PRICE_TYPE_FIXED, PRICE_TYPE_SPONTANEOUS
from clink.wallet import (
    NOFFER_ENV_VAR,
    ClinkWallet,
    _configured_noffer,
)

RELAY = "wss://relay.example.com"


def _service_pubkey():
    from clink.nostr import generate_keypair, pubkey_from_privkey

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


def test_configured_noffer_unset(monkeypatch):
    monkeypatch.delenv(NOFFER_ENV_VAR, raising=False)
    assert _configured_noffer() is None


def test_configured_noffer_blank(monkeypatch):
    monkeypatch.setenv(NOFFER_ENV_VAR, "   ")
    assert _configured_noffer() is None


def test_configured_noffer_set(monkeypatch):
    noffer = _noffer_str(_service_pubkey())
    monkeypatch.setenv(NOFFER_ENV_VAR, noffer)
    assert _configured_noffer() == noffer


def test_status_unconfigured(monkeypatch):
    monkeypatch.delenv(NOFFER_ENV_VAR, raising=False)
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message
    assert NOFFER_ENV_VAR in status.error_message
    assert status.balance_msat == 0


def test_status_invalid_noffer(monkeypatch):
    monkeypatch.setenv(NOFFER_ENV_VAR, "noffer1invalid")
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message


def test_status_configured(monkeypatch):
    monkeypatch.setenv(NOFFER_ENV_VAR, _noffer_str(_service_pubkey()))
    wallet = ClinkWallet()
    status = await_wallet_status(wallet)
    assert status.error_message is None
    assert status.balance_msat == 0


def test_create_invoice_unconfigured(monkeypatch):
    monkeypatch.delenv(NOFFER_ENV_VAR, raising=False)
    wallet = ClinkWallet()
    invoice = await_wallet_create_invoice(wallet, 1000)
    assert invoice.ok is False
    assert invoice.error_message
    assert NOFFER_ENV_VAR in invoice.error_message


def test_create_invoice_fixed_mismatch_rejects_before_network(monkeypatch):
    monkeypatch.setenv(
        NOFFER_ENV_VAR, _noffer_str(_service_pubkey(), PRICE_TYPE_FIXED, 1000)
    )
    wallet = ClinkWallet()
    invoice = await_wallet_create_invoice(wallet, 999)
    assert invoice.ok is False
    assert "Invalid Amount" in invoice.error_message


def test_pay_invoice_receive_only():
    wallet = ClinkWallet()
    payment = await_wallet_pay_invoice(wallet, "lnbc1test", 1000)
    assert payment.ok is False
    assert "receive-only" in payment.error_message


def test_invoice_status_pending():
    wallet = ClinkWallet()
    status = await_wallet_invoice_status(wallet, "any")
    assert status.paid is None


def await_wallet_status(wallet):
    import asyncio

    return asyncio.run(wallet.status())


def await_wallet_create_invoice(wallet, amount):
    import asyncio

    return asyncio.run(wallet.create_invoice(amount=amount, memo="test"))


def await_wallet_pay_invoice(wallet, bolt11, fee_limit_msat):
    import asyncio

    return asyncio.run(wallet.pay_invoice(bolt11, fee_limit_msat))


def await_wallet_invoice_status(wallet, checking_id):
    import asyncio

    return asyncio.run(wallet.get_invoice_status(checking_id))
