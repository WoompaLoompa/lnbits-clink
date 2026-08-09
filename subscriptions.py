"""CLINK Subscriptions: recurring charges via kind 21002 debit requests.

A subscription links a recurring :class:`~clink.models.Plan` to a payer's
static ``ndebit1...`` pointer. A permanent poller watches active
subscriptions and, whenever a billing period has elapsed, sends a kind 21002
direct-payment request to the payer's node service carrying a fresh BOLT11
invoice. On ``{"res": "ok"}`` the period is advanced and the attempt counter
resets; on failure the counter grows until the subscription is cancelled.
"""

from __future__ import annotations

import asyncio
import calendar
import json
from datetime import datetime, timedelta, timezone

from lnbits.core.services.payments import create_invoice
from loguru import logger

from .crud import get_active_subscriptions, get_plan, update_subscription
from .models import Subscription
from .nostr import (
    decrypt_with_keys,
    encrypt_with_keys,
    generate_keypair,
    verify_event,
)
from .nostr.bech32 import decode_ndebit
from .nostr.events import CLINK_VERSION_TAG, KIND_DEBIT_REQUEST, finalize
from .nostr.relay import request_response
from .pay import _relay_list

SUBSCRIPTIONS_TASK_NAME = "clink_subscriptions"
POLL_INTERVAL_SECONDS = 60
MAX_RENEWAL_ATTEMPTS = 3
RENEWAL_TIMEOUT_SECONDS = 30


class SubscriptionError(Exception):
    """Raised when a debit request cannot be sent or answered."""


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def add_frequency(start: datetime, number: int, unit: str) -> datetime:
    """Add ``number`` of ``unit``s (day/week/month) to ``start``."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if unit == "day":
        return start + timedelta(days=number)
    if unit == "week":
        return start + timedelta(weeks=number)
    if unit == "month":
        month = start.month - 1 + number
        year = start.year + month // 12
        month = month % 12 + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)
    raise ValueError(f"unsupported frequency unit: {unit}")


def is_due(sub: Subscription, now: datetime | None = None) -> bool:
    """Whether an active subscription needs a renewal attempt."""
    if sub.state != "active":
        return False
    end = _utc(sub.current_period_end)
    if end is None:
        return True
    return end <= (now or datetime.now(timezone.utc))


def failed_renewal_state(attempts: int) -> str:
    """State after a failed renewal, based on attempts already recorded."""
    return "cancelled" if attempts + 1 >= MAX_RENEWAL_ATTEMPTS else "active"


def build_debit_request(
    decoded, amount_sats: int, bolt11: str, description: str | None = None
) -> tuple[dict, str]:
    """Build and sign a kind 21002 direct-payment request event.

    Uses a fresh ephemeral keypair per attempt (as the CLINK SDK does) so
    renewals are not linked to a persistent identity. Returns
    ``(event, payer_privkey)``.
    """
    payer_privkey, _ = generate_keypair()
    payload: dict = {"amount_sats": amount_sats, "bolt11": bolt11}
    if decoded.pointer:
        payload["pointer"] = decoded.pointer
    if description:
        payload["description"] = description
    content = encrypt_with_keys(
        json.dumps(payload, separators=(",", ":")), payer_privkey, decoded.pubkey
    )
    event = finalize(
        payer_privkey,
        KIND_DEBIT_REQUEST,
        content,
        [["p", decoded.pubkey], CLINK_VERSION_TAG],
    )
    return event, payer_privkey


def parse_debit_response(
    event: dict, payer_privkey: str, recipient_pubkey: str
) -> dict:
    """Verify, decrypt and parse a kind 21002 response from the node service."""
    if not verify_event(event):
        raise SubscriptionError("Invalid response signature.")
    if event.get("pubkey") != recipient_pubkey:
        raise SubscriptionError("Response came from an unexpected pubkey.")
    try:
        content = event.get("content", "")
        return json.loads(decrypt_with_keys(content, payer_privkey, recipient_pubkey))
    except Exception as exc:
        raise SubscriptionError(f"Cannot decrypt the debit response: {exc}") from exc


_inflight: set[str] = set()


async def renew_subscription(sub: Subscription) -> Subscription:
    """Attempt to bill one period of ``sub`` and advance it on success."""
    if sub.id in _inflight:
        return sub
    _inflight.add(sub.id)
    try:
        return await _renew(sub)
    finally:
        _inflight.discard(sub.id)


async def _renew(sub: Subscription) -> Subscription:
    plan = await get_plan(sub.plan_id)
    if not plan or not plan.active:
        return await _fail_subscription(sub, "Plan is inactive or missing.")
    try:
        decoded = decode_ndebit(sub.ndebit or "")
    except ValueError as exc:
        return await _fail_subscription(sub, f"Invalid ndebit: {exc}")
    amount_sats = (plan.amount_msat or 0) // 1000
    if amount_sats <= 0:
        return await _fail_subscription(sub, "Plan amount must be at least 1 sat.")
    try:
        payment = await create_invoice(
            wallet_id=sub.wallet,
            amount=amount_sats,
            memo=(plan.name or "CLINK subscription")[:640],
            extension="clink",
            extra={"clink": {"subscription_id": sub.id}},
            external_id=sub.id,
        )
    except Exception as exc:
        logger.warning(
            f"clink: invoice creation failed for subscription {sub.id}: {exc}"
        )
        return await _fail_subscription(sub, f"Invoice creation failed: {exc}")

    event, payer_privkey = build_debit_request(
        decoded, amount_sats, payment.bolt11, plan.name
    )
    response_filter = {
        "kinds": [KIND_DEBIT_REQUEST],
        "#e": [event["id"]],
        "#p": [event["pubkey"]],
    }
    relays = await _relay_list(decoded.relay)
    try:
        response_event = await request_response(
            relays, event, response_filter, timeout=RENEWAL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return await _fail_subscription(
            sub, "No response from the payer node (timeout)."
        )
    response = parse_debit_response(response_event, payer_privkey, decoded.pubkey)
    if response.get("res") != "ok":
        message = response.get("error") or "Payer node denied the debit request."
        return await _fail_subscription(sub, message)

    now = datetime.now(timezone.utc)
    start = _utc(sub.current_period_end) or now
    end = add_frequency(start, plan.frequency_number, plan.frequency_unit)
    if end <= now:
        start = now
        end = add_frequency(now, plan.frequency_number, plan.frequency_unit)
    sub.current_period_start = start
    sub.current_period_end = end
    sub.attempts = 0
    sub.last_paid_at = now
    sub.last_error = None
    sub.state = "active"
    sub.updated_at = now
    await update_subscription(sub)
    logger.info(f"clink: subscription {sub.id} renewed, next payment {end.isoformat()}")
    return sub


async def _fail_subscription(sub: Subscription, error: str) -> Subscription:
    sub.attempts += 1
    sub.last_error = error
    sub.updated_at = datetime.now(timezone.utc)
    sub.state = failed_renewal_state(sub.attempts)
    if sub.state == "cancelled":
        logger.warning(
            f"clink: subscription {sub.id} cancelled after {sub.attempts} "
            f"failed renewals: {error}"
        )
    await update_subscription(sub)
    return sub


async def clink_subscriptions() -> None:
    """Permanent task: poll due subscriptions and renew them."""
    logger.info("clink: subscription poller started")
    while True:
        try:
            for sub in await get_active_subscriptions():
                if is_due(sub):
                    await renew_subscription(sub)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"clink: subscription poll error: {exc}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
