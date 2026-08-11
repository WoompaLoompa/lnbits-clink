"""CLINK funding source (LNbits backend wallet).

``ClinkWallet`` lets this LNbits instance be backed by a remote
Lightning.pub node over the CLINK protocol: BOLT11 invoices are requested
from the node's offer service (kind 21001) and handed to LNbits users to pay.

Configuration (each also readable from an environment variable):

- ``lnbits_clink_funding_noffer`` / ``LNBITS_CLINK_FUNDING_NOFFER``: a
  ``noffer1...`` string pointing at the node's offer service. Enough to
  receive funds (``create_invoice``).
- ``lnbits_clink_funding_account`` / ``LNBITS_CLINK_FUNDING_ACCOUNT``: a
  ``nprofile1...[:token]`` connection string for the Lightning.pub *user
  API* (kind 21000). Adds paying out (``pay_invoice``), a real node balance
  in ``status()`` and per-invoice state reporting.
"""

from __future__ import annotations

import asyncio
import os

from bolt11 import decode as bolt11_decode
from lnbits.settings import settings
from lnbits.wallets.base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)
from loguru import logger

from .account import (
    get_payment_state as account_get_payment_state,
)
from .account import (
    get_user_info,
)
from .account import (
    get_user_operations as account_get_user_operations,
)
from .account import (
    new_invoice as account_new_invoice,
)
from .account import (
    pay_invoice as account_pay_invoice,
)
from .crud import get_invoice_by_hash, save_invoice
from .models import Invoice
from .nostr.bech32 import decode_noffer
from .nostr.events import KIND_OFFER_REQUEST
from .nostr.relay import request_response
from .pay import (
    REQUEST_TIMEOUT_SECONDS,
    _relay_list,
    build_offer_request,
    parse_offer_response,
    resolve_outgoing_amount,
)

NOFFER_ENV_VAR = "LNBITS_CLINK_FUNDING_NOFFER"
ACCOUNT_ENV_VAR = "LNBITS_CLINK_FUNDING_ACCOUNT"

MAX_INCOMING_PAGES = 5
INCOMING_PAGE_SIZE = 100


def _setting_or_env(setting_name: str, env_var: str) -> str | None:
    value = getattr(settings, setting_name, None)
    if not value:
        value = os.environ.get(env_var, "")
    value = (value or "").strip()
    return value or None


def _configured_noffer() -> str | None:
    return _setting_or_env("lnbits_clink_funding_noffer", NOFFER_ENV_VAR)


def _configured_account() -> str | None:
    return _setting_or_env("lnbits_clink_funding_account", ACCOUNT_ENV_VAR)


def _config_error_message() -> str:
    return (
        f"CLINK funding source is not configured. Set {NOFFER_ENV_VAR} to a "
        f"noffer1... for receiving, and {ACCOUNT_ENV_VAR} to an "
        "nprofile1... connection string for paying out."
    )


class ClinkWallet(Wallet):
    """LNbits funding source backed by a remote Lightning.pub node."""

    async def cleanup(self):
        pass

    async def status(self) -> StatusResponse:
        account = _configured_account()
        if account:
            try:
                info = await get_user_info(account)
                balance_sats = int(info.get("balance") or 0)
                return StatusResponse(None, balance_sats * 1000)
            except Exception as exc:
                logger.warning(f"clink: status (account) failed: {exc}")
                return StatusResponse(f"CLINK account error: {exc}", 0)
        noffer = _configured_noffer()
        if noffer is None:
            return StatusResponse(_config_error_message(), 0)
        try:
            decode_noffer(noffer)
        except Exception as exc:
            return StatusResponse(f"CLINK funding source: invalid noffer ({exc}).", 0)
        return StatusResponse(None, 0)

    async def create_invoice(
        self,
        amount: int,
        memo: str | None = None,
        description_hash: bytes | None = None,
        unhashed_description: bytes | None = None,
        **kwargs,
    ) -> InvoiceResponse:
        account = _configured_account()
        if account:
            return await self._create_invoice_account(account, amount, memo)
        noffer = _configured_noffer()
        if noffer:
            return await self._create_invoice_offer(noffer, amount, memo)
        return InvoiceResponse(False, error_message=_config_error_message())

    async def _create_invoice_offer(
        self, noffer: str, amount: int, memo: str | None
    ) -> InvoiceResponse:
        try:
            decoded = decode_noffer(noffer)
            amount_sats, error = resolve_outgoing_amount(decoded, amount)
            if error:
                return InvoiceResponse(
                    False, error_message=error.get("error", "Invalid amount.")
                )
            event, payer_privkey = build_offer_request(decoded, amount_sats, memo)
            response_filter = {
                "kinds": [KIND_OFFER_REQUEST],
                "#e": [event["id"]],
                "#p": [event["pubkey"]],
            }
            relays = await _relay_list(decoded.relay)
            response_event = await request_response(
                relays, event, response_filter, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response = parse_offer_response(
                response_event, payer_privkey, decoded.pubkey
            )
            if response.get("error"):
                return InvoiceResponse(False, error_message=response["error"])
            bolt11 = response.get("bolt11")
            if not bolt11:
                return InvoiceResponse(
                    False,
                    error_message="CLINK funding source: offer response did not "
                    "include an invoice.",
                )
            return await self._invoice_response(bolt11, amount_sats, direction="in")
        except asyncio.TimeoutError:
            return InvoiceResponse(
                False,
                error_message="CLINK funding source: no response from the node "
                "(timeout).",
            )
        except Exception as exc:
            logger.warning(f"clink: create_invoice (offer) failed: {exc}")
            return InvoiceResponse(False, error_message=str(exc))

    async def _create_invoice_account(
        self, account: str, amount: int, memo: str | None
    ) -> InvoiceResponse:
        try:
            bolt11 = await account_new_invoice(account, amount, memo=memo)
            return await self._invoice_response(bolt11, amount, direction="in")
        except asyncio.TimeoutError:
            return InvoiceResponse(
                False,
                error_message="CLINK funding source: no response from the node "
                "(timeout).",
            )
        except Exception as exc:
            logger.warning(f"clink: create_invoice (account) failed: {exc}")
            return InvoiceResponse(False, error_message=str(exc))

    async def _invoice_response(
        self, bolt11: str, amount_sats: int, direction: str
    ) -> InvoiceResponse:
        decoded = bolt11_decode(bolt11)
        checking_id = str(decoded.payment_hash)
        try:
            await save_invoice(
                Invoice(
                    payment_hash=checking_id,
                    bolt11=bolt11,
                    direction=direction,
                    amount_msat=amount_sats * 1000,
                )
            )
        except Exception:
            pass
        return InvoiceResponse(True, checking_id=checking_id, payment_request=bolt11)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        account = _configured_account()
        if account is None:
            return PaymentResponse(
                ok=False,
                error_message=(
                    "CLINK funding source is receive-only without a Lightning.pub "
                    f"account. Set {ACCOUNT_ENV_VAR} to an nprofile1... connection "
                    "string to enable paying out."
                ),
            )
        try:
            result = await account_pay_invoice(account, bolt11)
        except asyncio.TimeoutError:
            return PaymentResponse(
                ok=False,
                error_message="CLINK funding source: no response from the node "
                "(timeout).",
            )
        except Exception as exc:
            logger.warning(f"clink: pay_invoice (account) failed: {exc}")
            return PaymentResponse(ok=False, error_message=str(exc))

        preimage = result.get("preimage")
        amount_paid = result.get("amount_paid")
        service_fee = result.get("service_fee") or 0
        network_fee = result.get("network_fee") or 0
        checking_id = str(bolt11_decode(bolt11).payment_hash)
        try:
            await save_invoice(
                Invoice(
                    payment_hash=checking_id,
                    bolt11=bolt11,
                    direction="out",
                    amount_msat=(amount_paid or 0) * 1000,
                    operation_id=result.get("operation_id"),
                )
            )
        except Exception:
            pass
        # The node only responds without error once the payment settled; for
        # invoices minted on the same node (internal payments) it returns an
        # empty preimage, so an empty preimage is not a failure.
        return PaymentResponse(
            ok=True,
            checking_id=checking_id,
            fee_msat=(service_fee + network_fee) * 1000,
            preimage=preimage or None,
        )

    async def _status_for(self, checking_id: str) -> PaymentStatus:
        account = _configured_account()
        if account is None:
            return PaymentPendingStatus()
        invoice = await get_invoice_by_hash(checking_id)
        if invoice is None or not invoice.bolt11:
            return PaymentPendingStatus()
        try:
            state = await account_get_payment_state(account, invoice.bolt11)
        except Exception:
            return PaymentPendingStatus()
        paid_at = state.get("paid_at_unix")
        if not paid_at:
            return PaymentPendingStatus()
        fee = (state.get("service_fee") or 0) + (state.get("network_fee") or 0)
        return PaymentSuccessStatus(
            fee_msat=fee * 1000,
        )

    async def _incoming_status_for(self, checking_id: str) -> PaymentStatus:
        account = _configured_account()
        if account is None:
            return PaymentPendingStatus()
        invoice = await get_invoice_by_hash(checking_id)
        if invoice is None or not invoice.bolt11:
            return PaymentPendingStatus()
        cursor_id, cursor_ts = 0, 0
        for _ in range(MAX_INCOMING_PAGES):
            try:
                operations = await account_get_user_operations(
                    account,
                    cursor=(cursor_id, cursor_ts),
                    max_size=INCOMING_PAGE_SIZE,
                )
            except Exception:
                return PaymentPendingStatus()
            status = self._paid_incoming_for(operations, invoice.bolt11)
            if status is not None:
                return status
            incoming = operations.get("latestIncomingInvoiceOperations") or {}
            to_index = incoming.get("toIndex") or {}
            next_id, next_ts = to_index.get("id", 0), to_index.get("ts", 0)
            if (next_id, next_ts) == (cursor_id, cursor_ts):
                return PaymentPendingStatus()
            cursor_id, cursor_ts = next_id, next_ts
        return PaymentPendingStatus()

    @staticmethod
    def _paid_incoming_for(operations: dict, bolt11: str) -> PaymentStatus | None:
        incoming = operations.get("latestIncomingInvoiceOperations") or {}
        for op in incoming.get("operations") or []:
            if op.get("identifier") != bolt11:
                continue
            paid_at = op.get("paidAtUnix")
            if not paid_at:
                return None
            fee = (op.get("service_fee") or 0) + (op.get("network_fee") or 0)
            return PaymentSuccessStatus(fee_msat=fee * 1000)
        return None

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return await self._incoming_status_for(checking_id)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        return await self._status_for(checking_id)
