"""CLINK node service: listens on relays and processes offers/debits.

Implements the receiving-service side of the CLINK protocol:

- kind 21001 (offer) requests are matched to locally-stored offers, turned
  into BOLT11 invoices via ``create_invoice`` and answered with
  ``{"bolt11": ...}`` (or an error payload).
- kind 21002 (debit) requests are matched to locally-stored debit pointers,
  checked against rules/budgets/session ``k1`` and settled with
  ``pay_invoice``, answering with ``{"res": "ok", "preimage": ...}`` or a GFY
  payload.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from bolt11 import decode as bolt11_decode
from lnbits.core.crud.payments import get_wallet_payment
from lnbits.core.services.payments import create_invoice, pay_invoice
from lnbits.settings import settings
from loguru import logger

from .crud import (
    create_debit_usage,
    create_k1,
    get_debit_usage,
    get_debits,
    get_enabled_relays,
    get_k1,
    get_node_keys,
    get_offers_by_pubkey,
    update_debit_usage,
)
from .models import K1, Debit, DebitUsage
from .nostr import (
    RelayClient,
    decrypt_with_keys,
    encrypt_with_keys,
    verify_event,
)
from .nostr.bech32 import (
    PRICE_TYPE_FIXED,
    PRICE_TYPE_SPONTANEOUS,
    PRICE_TYPE_VARIABLE,
    decode_noffer,
)
from .nostr.events import (
    CLINK_VERSION_TAG,
    KIND_DEBIT_REQUEST,
    KIND_OFFER_REQUEST,
    finalize,
    get_p_tag,
)

LISTENER_TASK_NAME = "clink_listener"
LISTENER_SUB_ID = "clink_listener"

# Debit requests older than this (seconds) are answered with GFY(3).
REQUEST_MAX_AGE_SECONDS = 30

# GFY codes (clink-debits spec).
GFY_DENIED = 1
GFY_TEMPORARY = 2
GFY_EXPIRED = 3
GFY_RATE_LIMITED = 4
GFY_INVALID_AMOUNT = 5
GFY_INVALID_REQUEST = 6

# Offer error codes (clink-offers spec).
OFFER_ERROR_INVALID = 1
OFFER_ERROR_TEMPORARY = 2
OFFER_ERROR_EXPIRED = 3
OFFER_ERROR_UNSUPPORTED = 4
OFFER_ERROR_INVALID_AMOUNT = 5


async def clink_listener() -> None:
    """Permanent task: keep one relay subscription alive per enabled relay."""
    logger.info("clink: listener task started")
    while True:
        relays = await get_enabled_relays()
        if not relays:
            await asyncio.sleep(10)
            continue
        try:
            await asyncio.gather(*(_listen_on_relay(r.url) for r in relays))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"clink: listener error: {exc}")
        await asyncio.sleep(2)


async def _listen_on_relay(url: str) -> None:
    while True:
        try:
            client = RelayClient(url)
            await client.connect()
            logger.info(f"clink: listening on {url}")
            await client.subscribe(
                LISTENER_SUB_ID,
                {"kinds": [KIND_OFFER_REQUEST, KIND_DEBIT_REQUEST]},
                _dispatch_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"clink: relay {url} error: {exc}")
        finally:
            await asyncio.sleep(5)


_dispatch_tasks: set[asyncio.Task] = set()


def _dispatch_event(event: dict) -> None:
    task = asyncio.create_task(handle_event(event))
    _dispatch_tasks.add(task)
    task.add_done_callback(_dispatch_tasks.discard)


async def handle_event(event: dict) -> None:
    """Route an incoming CLINK event to the appropriate handler."""
    try:
        if event.get("kind") not in (KIND_OFFER_REQUEST, KIND_DEBIT_REQUEST):
            return
        if not verify_event(event):
            logger.debug("clink: dropped event with invalid signature")
            return
        if event.get("kind") == KIND_OFFER_REQUEST:
            await handle_offer_request(event)
        else:
            await handle_debit_request(event)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"clink: failed handling event {event.get('id', '?')}: {exc}")


def _has_clink_version(event: dict) -> bool:
    return any(
        tag and len(tag) > 1 and tag[0] == "clink_version"
        for tag in event.get("tags", [])
    )


# ---------------------------------------------------------------------------
# Offers (kind 21001)
# ---------------------------------------------------------------------------


async def handle_offer_request(event: dict) -> None:
    if not _has_clink_version(event):
        return
    target = get_p_tag(event)
    if not target:
        return
    offers = await get_offers_by_pubkey(target)
    if not offers:
        return
    requestor = event.get("pubkey", "")
    for offer in offers:
        try:
            payload = json.loads(
                decrypt_with_keys(event.get("content", ""), offer.privkey, requestor)
            )
        except Exception:
            continue
        response = await build_offer_response(offer, payload)
        relays = [offer.relay] if offer.relay else None
        await send_response(
            KIND_OFFER_REQUEST, offer.privkey, requestor, event["id"], response, relays
        )
        return


async def build_offer_response(offer, payload: dict) -> dict:
    """Validate a kind 21001 request and produce the response payload."""
    if not offer.active:
        return {"error": "Offer has expired.", "code": OFFER_ERROR_EXPIRED}
    try:
        decoded = decode_noffer(offer.noffer) if offer.noffer else None
    except ValueError:
        decoded = None
    offer_id = decoded.offer if decoded else offer.id
    if payload.get("offer") != offer_id:
        return {"error": "Invalid Offer", "code": OFFER_ERROR_INVALID}
    if payload.get("zap"):
        return {"error": "Unsupported Feature", "code": OFFER_ERROR_UNSUPPORTED}
    amount, error = resolve_offer_amount(decoded, payload.get("amount_sats"))
    if error:
        return error
    memo = (
        payload.get("description") or offer.description or offer.name or "CLINK offer"
    )
    try:
        payment = await create_invoice(
            wallet_id=offer.wallet,
            amount=amount,
            memo=memo[:640],
            unhashed_description=(
                offer.description.encode("utf-8") if offer.description else None
            ),
            expiry=payload.get("expires_in_seconds") or None,
            extension="clink",
            extra={"clink": {"offer_id": offer.id}},
            external_id=offer.id,
        )
    except Exception as exc:
        logger.warning(f"clink: invoice creation failed for offer {offer.id}: {exc}")
        return {"error": "Temporary Failure", "code": OFFER_ERROR_TEMPORARY}
    return {"bolt11": payment.bolt11}


def resolve_offer_amount(decoded, req_amount) -> tuple[int | None, dict | None]:
    """Resolve the invoice amount in sats for an offer request.

    Returns ``(amount_sats, None)`` on success or ``(None, error_payload)``.
    """
    max_sats = settings.lnbits_max_incoming_payment_amount_sats
    price_type = decoded.price_type if decoded else PRICE_TYPE_SPONTANEOUS
    fixed_price = decoded.price if decoded else None
    if price_type == PRICE_TYPE_FIXED:
        if fixed_price is None:
            return None, {"error": "Temporary Failure", "code": OFFER_ERROR_TEMPORARY}
        if req_amount is not None and int(req_amount) != fixed_price:
            return None, {
                "error": "Invalid Amount",
                "code": OFFER_ERROR_INVALID_AMOUNT,
                "range": {"min": fixed_price, "max": fixed_price},
            }
        return fixed_price, None
    if price_type == PRICE_TYPE_VARIABLE:
        return (fixed_price if req_amount is None else int(req_amount)), None
    if req_amount is None:
        return None, {
            "error": "Invalid Amount",
            "code": OFFER_ERROR_INVALID_AMOUNT,
            "range": {"min": 1, "max": max_sats},
        }
    return int(req_amount), None


# ---------------------------------------------------------------------------
# Debits (kind 21002)
# ---------------------------------------------------------------------------


async def handle_debit_request(event: dict) -> None:
    if not _has_clink_version(event):
        return
    target = get_p_tag(event)
    if not target:
        return
    node_keys = await get_node_keys()
    node_key = next((nk for nk in node_keys if nk.pubkey == target), None)
    if not node_key:
        return
    requestor = event.get("pubkey", "")
    try:
        payload = json.loads(
            decrypt_with_keys(event.get("content", ""), node_key.privkey, requestor)
        )
    except Exception:
        logger.debug("clink: cannot decrypt debit request")
        return
    age_ms = (int(time.time()) - event.get("created_at", 0)) * 1000
    if age_ms > REQUEST_MAX_AGE_SECONDS * 1000:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_EXPIRED,
            "Expired Request",
            delta_ms=age_ms,
            max_delta_ms=REQUEST_MAX_AGE_SECONDS * 1000,
        )
        return
    pointer = payload.get("pointer")
    debits = await get_debits(node_key.wallet)
    debit = None
    if pointer:
        debit = next((d for d in debits if d.id == pointer), None)
    elif len(debits) == 1:
        debit = debits[0]
    if debit is None:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: unknown pointer",
        )
        return
    if debit.state != "active":
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_DENIED,
            "Request Denied",
        )
        return
    k1 = payload.get("k1")
    if k1 and not isinstance(k1, str):
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: k1 must be a string",
        )
        return
    rules = parse_rules(debit.rules)
    allowed = rules.get("allowed_pubkeys")
    if allowed and requestor not in allowed:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_DENIED,
            "Request Denied",
        )
        return
    if k1:
        if await get_k1(debit.id, k1):
            await send_gfy(
                KIND_DEBIT_REQUEST,
                node_key,
                requestor,
                event,
                GFY_INVALID_REQUEST,
                "K1 already processed",
            )
            return
        try:
            await create_k1(K1(debit_id=debit.id, k1=k1))
        except Exception:
            await send_gfy(
                KIND_DEBIT_REQUEST,
                node_key,
                requestor,
                event,
                GFY_INVALID_REQUEST,
                "K1 already processed",
            )
            return
    if payload.get("bolt11"):
        await handle_direct_payment(debit, node_key, requestor, event, payload, rules)
    elif payload.get("frequency"):
        await handle_budget_request(debit, node_key, requestor, event, payload, rules)
    else:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: nothing to do",
        )


async def handle_direct_payment(
    debit: Debit,
    node_key,
    requestor: str,
    event: dict,
    payload: dict,
    rules: dict,
) -> None:
    """Pay a bolt11 invoice from a kind 21002 direct payment request."""
    try:
        invoice = bolt11_decode(payload["bolt11"])
    except Exception:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: cannot decode bolt11",
        )
        return
    amount_msat = invoice.amount_msat or 0
    stated = payload.get("amount_sats")
    if stated is not None:
        stated_msat = int(stated) * 1000
        if amount_msat and stated_msat != amount_msat:
            await send_gfy(
                KIND_DEBIT_REQUEST,
                node_key,
                requestor,
                event,
                GFY_INVALID_AMOUNT,
                "Invalid Amount",
                range={
                    "min": 1,
                    "max": settings.lnbits_max_incoming_payment_amount_sats,
                },
            )
            return
        amount_msat = stated_msat
    if amount_msat <= 0:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_AMOUNT,
            "Invalid Amount",
            range={"min": 1, "max": settings.lnbits_max_incoming_payment_amount_sats},
        )
        return
    if debit.amount_msat and amount_msat != debit.amount_msat:
        fixed = debit.amount_msat // 1000
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_AMOUNT,
            "Invalid Amount",
            range={"min": fixed, "max": fixed},
        )
        return
    min_msat = rules.get("min_msat")
    max_msat = rules.get("max_msat")
    if min_msat is not None and amount_msat < min_msat:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_AMOUNT,
            "Invalid Amount",
            range={"min": min_msat // 1000, "max": (max_msat or min_msat) // 1000},
        )
        return
    if max_msat is not None and amount_msat > max_msat:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_AMOUNT,
            "Invalid Amount",
            range={"min": (min_msat or 1) // 1000, "max": max_msat // 1000},
        )
        return
    bucket: str | None = None
    if debit.budget_msat:
        unit = debit.frequency_unit or "month"
        number = debit.frequency_number or 1
        bucket = period_bucket(number, unit, datetime.now(timezone.utc))
        usage = await get_debit_usage(debit.id, bucket)
        spent = usage.spent_msat if usage else 0
        if spent + amount_msat > debit.budget_msat:
            await send_gfy(
                KIND_DEBIT_REQUEST,
                node_key,
                requestor,
                event,
                GFY_INVALID_AMOUNT,
                "Invalid Amount",
                range={
                    "min": 1,
                    "max": max(1, (debit.budget_msat - spent) // 1000),
                },
            )
            return
        await set_debit_usage(debit.id, bucket, spent + amount_msat)
    try:
        payment = await pay_invoice(
            wallet_id=debit.wallet,
            payment_request=payload["bolt11"],
            max_sat=(amount_msat // 1000) + 1,
            description=payload.get("description") or "",
            extra={"clink": {"debit_id": debit.id}},
            extension="clink",
        )
    except Exception as exc:
        logger.warning(f"clink: debit payment failed for {debit.id}: {exc}")
        if bucket:
            usage = await get_debit_usage(debit.id, bucket)
            if usage:
                await update_debit_usage(
                    debit.id, bucket, max(0, usage.spent_msat - amount_msat)
                )
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_TEMPORARY,
            f"Temporary Failure: {exc}",
        )
        return
    preimage = await await_preimage(debit.wallet, payment.payment_hash)
    response = {"res": "ok"}
    if preimage:
        response["preimage"] = preimage
    await send_response(
        KIND_DEBIT_REQUEST,
        node_key.privkey,
        requestor,
        event["id"],
        response,
    )


async def handle_budget_request(
    debit: Debit,
    node_key,
    requestor: str,
    event: dict,
    payload: dict,
    rules: dict,
) -> None:
    """Acknowledge a budget request if it fits the configured pointer."""
    frequency = payload.get("frequency")
    amount_sats = payload.get("amount_sats")
    if not isinstance(frequency, dict):
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: frequency must be an object",
        )
        return
    unit = frequency.get("unit")
    number = frequency.get("number")
    if unit not in ("day", "week", "month") or not isinstance(number, int):
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: bad frequency",
        )
        return
    if debit.frequency_unit and debit.frequency_unit != unit:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_REQUEST,
            "Invalid Request: frequency does not match pointer",
        )
        return
    amount_msat = int(amount_sats) * 1000 if amount_sats is not None else 0
    budget = debit.budget_msat
    if budget is None:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_DENIED,
            "Request Denied: no budget configured",
        )
        return
    if amount_msat > budget:
        await send_gfy(
            KIND_DEBIT_REQUEST,
            node_key,
            requestor,
            event,
            GFY_INVALID_AMOUNT,
            "Invalid Amount",
            range={"min": 1, "max": budget // 1000},
        )
        return
    await send_response(
        KIND_DEBIT_REQUEST,
        node_key.privkey,
        requestor,
        event["id"],
        {"res": "ok"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_rules(rules: str | None) -> dict:
    if not rules:
        return {}
    try:
        data = json.loads(rules)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def period_bucket(number: int, unit: str, now: datetime) -> str:
    if unit == "day":
        return now.strftime("%Y-%m-%d")
    if unit == "week":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if unit == "month":
        return now.strftime("%Y-%m")
    raise ValueError(f"unsupported frequency unit: {unit}")


async def set_debit_usage(debit_id: str, period_start: str, spent_msat: int) -> None:
    usage = await get_debit_usage(debit_id, period_start)
    if usage:
        await update_debit_usage(debit_id, period_start, spent_msat)
    else:
        await create_debit_usage(
            DebitUsage(
                debit_id=debit_id, period_start=period_start, spent_msat=spent_msat
            )
        )


async def await_preimage(
    wallet_id: str, payment_hash: str, timeout: float = 30.0
) -> str | None:
    """Wait for a paid invoice's preimage, or None on failure/timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        payment = await get_wallet_payment(wallet_id, payment_hash)
        if payment and payment.preimage:
            return payment.preimage
        if payment and payment.failed:
            return None
        if loop.time() > deadline:
            return None
        await asyncio.sleep(1)


async def send_gfy(
    kind: int,
    node_key,
    requestor: str,
    event: dict,
    code: int,
    error: str,
    **extra,
) -> None:
    payload = {"res": "GFY", "code": code, "error": error}
    payload.update(extra)
    await send_response(kind, node_key.privkey, requestor, event["id"], payload)


async def send_response(
    kind: int,
    sender_privkey: str,
    recipient_pubkey: str,
    request_id: str,
    payload: dict,
    relays: list[str] | None = None,
) -> None:
    """Encrypt a response payload and publish it to the relays."""
    try:
        content = encrypt_with_keys(
            json.dumps(payload, separators=(",", ":")), sender_privkey, recipient_pubkey
        )
        tags = [["p", recipient_pubkey], ["e", request_id], CLINK_VERSION_TAG]
        event = finalize(sender_privkey, kind, content, tags)
        await publish_event(event, relays)
    except Exception as exc:
        logger.warning(f"clink: failed to send response: {exc}")


async def publish_event(event: dict, relays: list[str] | None = None) -> None:
    """Publish an event to the given relays (or all enabled ones)."""
    if relays is None:
        enabled = await get_enabled_relays()
        relays = [r.url for r in enabled]
    if not relays:
        return
    tasks = [asyncio.create_task(_publish_one(url, event)) for url in set(relays)]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _publish_one(url: str, event: dict) -> None:
    try:
        client = RelayClient(url)
        await client.connect()
        try:
            await asyncio.wait_for(client.publish(event), timeout=10)
        finally:
            await client.close()
    except Exception as exc:
        logger.warning(f"clink: publish to {url} failed: {exc}")
