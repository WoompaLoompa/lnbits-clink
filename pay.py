"""CLINK Pay Offers (outgoing): payer-side flow for kind 21001.

Given a ``noffer1...`` string this module decodes it, sends an encrypted
kind 21001 request to the service's relay, waits for the encrypted invoice
response and pays it with ``pay_invoice``.

The payer uses a fresh ephemeral keypair per request (as recommended by the
spec) so payments are not linked to a primary Nostr identity.
"""

from __future__ import annotations

import asyncio
import json

from lnbits.core.services.payments import pay_invoice
from loguru import logger

from .crud import get_enabled_relays
from .node import (
    OFFER_ERROR_INVALID,
    OFFER_ERROR_INVALID_AMOUNT,
    OFFER_ERROR_TEMPORARY,
    await_preimage,
)
from .nostr import (
    decrypt_with_keys,
    encrypt_with_keys,
    generate_keypair,
    verify_event,
)
from .nostr.bech32 import (
    PRICE_TYPE_FIXED,
    PRICE_TYPE_SPONTANEOUS,
    decode_noffer,
)
from .nostr.events import CLINK_VERSION_TAG, KIND_OFFER_REQUEST, finalize
from .nostr.relay import request_response

REQUEST_TIMEOUT_SECONDS = 30
PREIMAGE_TIMEOUT_SECONDS = 15


class PayOfferError(Exception):
    """Raised when an offer request fails; carries the error payload."""

    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {"error": message}


def resolve_outgoing_amount(
    decoded, amount_sats: int | None
) -> tuple[int | None, dict | None]:
    """Resolve the amount in sats a payer must send for a ``noffer``.

    Returns ``(amount_sats, None)`` on success or ``(None, error_payload)``.
    """
    price_type = decoded.price_type if decoded else PRICE_TYPE_SPONTANEOUS
    fixed_price = decoded.price if decoded else None
    if price_type == PRICE_TYPE_FIXED:
        if fixed_price is None:
            return None, {"error": "Invalid Offer", "code": OFFER_ERROR_INVALID}
        if amount_sats is not None and int(amount_sats) != fixed_price:
            return None, {
                "error": "Invalid Amount",
                "code": OFFER_ERROR_INVALID_AMOUNT,
                "range": {"min": fixed_price, "max": fixed_price},
            }
        return fixed_price, None
    if amount_sats is None or int(amount_sats) <= 0:
        return None, {
            "error": "Invalid Amount",
            "code": OFFER_ERROR_INVALID_AMOUNT,
            "range": {"min": 1, "max": None},
        }
    return int(amount_sats), None


def build_offer_request_payload(
    decoded, amount_sats: int, description: str | None = None
) -> dict:
    """Build the decrypted JSON payload for a kind 21001 offer request."""
    payload: dict = {"offer": decoded.offer, "amount_sats": amount_sats}
    if description:
        payload["description"] = description
    return payload


def build_offer_request(
    decoded, amount_sats: int, description: str | None = None
) -> tuple[dict, str]:
    """Build and sign a kind 21001 offer request event.

    Returns ``(event, payer_privkey)`` where ``payer_privkey`` is the fresh
    ephemeral keypair used to encrypt and sign the request.
    """
    payer_privkey, _ = generate_keypair()
    payload = build_offer_request_payload(decoded, amount_sats, description)
    content = encrypt_with_keys(
        json.dumps(payload, separators=(",", ":")), payer_privkey, decoded.pubkey
    )
    event = finalize(
        payer_privkey,
        KIND_OFFER_REQUEST,
        content,
        [["p", decoded.pubkey], CLINK_VERSION_TAG],
    )
    return event, payer_privkey


def parse_offer_response(
    event: dict, payer_privkey: str, recipient_pubkey: str
) -> dict:
    """Verify, decrypt and parse a kind 21001 response from the offer service."""
    if not verify_event(event):
        raise PayOfferError("Invalid response signature.")
    if event.get("pubkey") != recipient_pubkey:
        raise PayOfferError("Response came from an unexpected pubkey.")
    try:
        content = event.get("content", "")
        return json.loads(decrypt_with_keys(content, payer_privkey, recipient_pubkey))
    except Exception as exc:
        raise PayOfferError(f"Cannot decrypt the offer response: {exc}") from exc


async def _relay_list(hint: str | None) -> list[str]:
    relays: list[str] = []
    if hint:
        relays.append(hint)
    enabled = await get_enabled_relays()
    relays.extend(r.url for r in enabled)
    return list(dict.fromkeys(relays))


async def pay_offer(
    noffer: str,
    wallet_id: str,
    amount_sats: int | None = None,
    description: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Pay a CLINK offer from a ``noffer1...`` string using ``wallet_id``."""
    decoded = decode_noffer(noffer)
    amount, error = resolve_outgoing_amount(decoded, amount_sats)
    if error:
        raise PayOfferError(error["error"], error)

    event, payer_privkey = build_offer_request(decoded, amount, description)
    response_filter = {
        "kinds": [KIND_OFFER_REQUEST],
        "#e": [event["id"]],
        "#p": [event["pubkey"]],
    }
    relays = await _relay_list(decoded.relay)
    try:
        response_event = await request_response(
            relays, event, response_filter, timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        raise PayOfferError("No response from the offer service (timeout).") from exc

    response = parse_offer_response(response_event, payer_privkey, decoded.pubkey)
    if response.get("error"):
        raise PayOfferError(response["error"], response)
    bolt11 = response.get("bolt11")
    if not bolt11:
        raise PayOfferError(
            "Offer response did not include an invoice.",
            {"error": "Temporary Failure", "code": OFFER_ERROR_TEMPORARY},
        )

    try:
        payment = await pay_invoice(
            wallet_id=wallet_id,
            payment_request=bolt11,
            max_sat=amount + 1,
            description=description or "",
            extra={"clink": {"offer_id": decoded.offer}},
        )
    except Exception as exc:
        logger.warning(f"clink: pay offer failed for wallet {wallet_id}: {exc}")
        raise PayOfferError(f"Payment failed: {exc}") from exc

    result: dict = {
        "status": "ok",
        "bolt11": bolt11,
        "payment_hash": payment.payment_hash,
        "offer_id": decoded.offer,
        "amount_sats": amount,
    }
    preimage = await await_preimage(
        wallet_id, payment.payment_hash, timeout=PREIMAGE_TIMEOUT_SECONDS
    )
    if preimage:
        result["preimage"] = preimage
    return result
