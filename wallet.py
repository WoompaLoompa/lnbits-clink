"""CLINK funding source (LNbits backend wallet).

``ClinkWallet`` lets this LNbits instance be backed by a remote
Lightning.pub node over the CLINK protocol: BOLT11 invoices are requested
from the node's offer service (kind 21001) and handed to LNbits users to pay.

The node is configured with a ``noffer1...`` string in the environment
variable ``LNBITS_CLINK_FUNDING_NOFFER``.

Receive-first scope: ``create_invoice`` is fully wired (the noffer alone is
enough to receive). Paying out and balance/status reporting require a
Lightning.pub *account* on the node (connection string + user API), which is
planned for a follow-up milestone; until then ``pay_invoice`` fails with a
clear message and invoice completion is reported as pending.
"""

from __future__ import annotations

import asyncio
import os

from bolt11 import decode as bolt11_decode
from lnbits.wallets.base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)
from loguru import logger

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


def _configured_noffer() -> str | None:
    """Return the configured ``noffer1...`` or ``None`` if unset/empty."""
    noffer = os.environ.get(NOFFER_ENV_VAR, "").strip()
    return noffer or None


class ClinkWallet(Wallet):
    """LNbits funding source backed by a remote Lightning.pub node offer."""

    async def cleanup(self):
        pass

    async def status(self) -> StatusResponse:
        noffer = _configured_noffer()
        if noffer is None:
            return StatusResponse(
                f"CLINK funding source is not configured. Set {NOFFER_ENV_VAR} "
                "to a noffer1... pointing at your Lightning.pub node.",
                0,
            )
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
        noffer = _configured_noffer()
        if noffer is None:
            return InvoiceResponse(
                False,
                error_message=(
                    "CLINK funding source is not configured. " f"Set {NOFFER_ENV_VAR}."
                ),
            )
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
            checking_id = str(bolt11_decode(bolt11).payment_hash)
            return InvoiceResponse(
                True, checking_id=checking_id, payment_request=bolt11
            )
        except asyncio.TimeoutError:
            return InvoiceResponse(
                False,
                error_message="CLINK funding source: no response from the node "
                "(timeout).",
            )
        except Exception as exc:
            logger.warning(f"clink: create_invoice failed: {exc}")
            return InvoiceResponse(False, error_message=str(exc))

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        return PaymentResponse(
            ok=False,
            error_message=(
                "CLINK funding source is receive-only for now. Paying out requires "
                "a Lightning.pub node account (next milestone)."
            ),
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        return PaymentPendingStatus()
