from datetime import datetime, timezone
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.models import User
from lnbits.core.services.payments import create_invoice
from lnbits.decorators import check_user_exists
from lnbits.helpers import urlsafe_short_hash

from .crud import (
    create_debit,
    create_offer,
    create_plan,
    create_relay,
    create_subscription,
    delete_debit,
    delete_offer,
    delete_plan,
    delete_relay,
    delete_subscription,
    get_debit,
    get_debits,
    get_enabled_relays,
    get_offer,
    get_offers,
    get_or_create_node_key,
    get_plan,
    get_plans,
    get_relays,
    get_subscription,
    get_subscriptions,
    update_debit,
    update_offer,
    update_plan,
    update_subscription,
)
from .models import (
    CheckoutRequest,
    CreateDebit,
    CreateOffer,
    CreatePlan,
    CreateRelay,
    CreateSubscription,
    Debit,
    Offer,
    ParseNofferRequest,
    PayRequest,
    Plan,
    Relay,
    Subscription,
    UpdateDebit,
    UpdateOffer,
    UpdatePlan,
    UpdateRelay,
    UpdateSubscription,
)
from .node import resolve_offer_amount
from .nostr import (
    NDebit,
    NOffer,
    decode_ndebit,
    decode_noffer,
    encode_ndebit,
    encode_noffer,
    generate_keypair,
    pubkey_from_privkey,
)
from .nostr.bech32 import PRICE_TYPE_FIXED, PRICE_TYPE_SPONTANEOUS
from .pay import PayOfferError, pay_offer
from .subscriptions import add_frequency, renew_subscription

clink_ext_api = APIRouter()


def _wallet_owned(wallet_id: str, user: User) -> None:
    if wallet_id not in user.wallet_ids:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your wallet.")


async def _default_relay() -> str | None:
    relays = await get_enabled_relays()
    return relays[0].url if relays else None


@clink_ext_api.get("/api/v1/info", description="CLINK extension info")
async def api_info():
    relays = await get_enabled_relays()
    return {
        "extension": "clink",
        "listener": {"enabled": bool(relays)},
        "relays": [r.url for r in relays],
    }


# ---------------------------------------------------------------------------
# Relays
# ---------------------------------------------------------------------------


@clink_ext_api.get("/api/v1/relays", description="List configured relays")
async def api_relays(user: User = Depends(check_user_exists)):
    return [r.dict() for r in await get_relays()]


@clink_ext_api.post("/api/v1/relays", description="Add a relay")
async def api_relay_create(data: CreateRelay, user: User = Depends(check_user_exists)):
    url = data.url.strip().rstrip("/")
    if not (url.startswith("wss://") or url.startswith("ws://")):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Relay URL must start with ws:// or wss://",
        )
    existing = await get_relays()
    if any(r.url == url for r in existing):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Relay already added."
        )
    relay = Relay(url=url, enabled=data.enabled)
    await create_relay(relay)
    return relay.dict()


@clink_ext_api.delete("/api/v1/relays/{relay_id}", description="Remove a relay")
async def api_relay_delete(relay_id: str, user: User = Depends(check_user_exists)):
    await delete_relay(relay_id)


@clink_ext_api.put("/api/v1/relays/{relay_id}", description="Update a relay")
async def api_relay_update(
    relay_id: str, data: UpdateRelay, user: User = Depends(check_user_exists)
):
    from .crud import get_relay
    from .crud import update_relay as _update_relay

    relay = await get_relay(relay_id)
    if not relay:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Relay not found.")
    relay.enabled = data.enabled
    return (await _update_relay(relay)).dict()


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


@clink_ext_api.get("/api/v1/offers", description="List offers for a wallet")
async def api_offers(wallet: str = Query(...), user: User = Depends(check_user_exists)):
    _wallet_owned(wallet, user)
    return [o.dict() for o in await get_offers(wallet)]


@clink_ext_api.post("/api/v1/offers", description="Create a CLINK offer")
async def api_offer_create(data: CreateOffer, user: User = Depends(check_user_exists)):
    _wallet_owned(data.wallet, user)
    relay = data.relay or await _default_relay()
    if not relay:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Add a relay first, or pass one explicitly.",
        )
    offer_id = urlsafe_short_hash()
    privkey, _ = generate_keypair()
    pubkey = pubkey_from_privkey(privkey)
    price_type = PRICE_TYPE_FIXED if data.amount_msat else PRICE_TYPE_SPONTANEOUS
    price = (data.amount_msat or 0) // 1000 if data.amount_msat else None
    noffer = encode_noffer(
        NOffer(
            pubkey=pubkey,
            relay=relay,
            offer=offer_id,
            price_type=price_type,
            price=price,
        )
    )
    offer = Offer(
        id=offer_id,
        wallet=data.wallet,
        name=data.name,
        amount_msat=data.amount_msat,
        description=data.description,
        relay=relay,
        pubkey=pubkey,
        privkey=privkey,
        noffer=noffer,
    )
    await create_offer(offer)
    return offer.dict()


@clink_ext_api.put("/api/v1/offers/{offer_id}", description="Update an offer")
async def api_offer_update(
    offer_id: str, data: UpdateOffer, user: User = Depends(check_user_exists)
):
    offer = await get_offer(offer_id)
    if not offer:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Offer not found.")
    _wallet_owned(offer.wallet, user)
    offer.active = data.active
    offer = await update_offer(offer)
    return offer.dict()


@clink_ext_api.delete("/api/v1/offers/{offer_id}", description="Delete an offer")
async def api_offer_delete(offer_id: str, user: User = Depends(check_user_exists)):
    offer = await get_offer(offer_id)
    if not offer:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Offer not found.")
    _wallet_owned(offer.wallet, user)
    await delete_offer(offer_id)


# ---------------------------------------------------------------------------
# Debits
# ---------------------------------------------------------------------------


@clink_ext_api.get("/api/v1/debits", description="List debit pointers for a wallet")
async def api_debits(wallet: str = Query(...), user: User = Depends(check_user_exists)):
    _wallet_owned(wallet, user)
    return [d.dict() for d in await get_debits(wallet)]


@clink_ext_api.post("/api/v1/debits", description="Create a debit pointer")
async def api_debit_create(data: CreateDebit, user: User = Depends(check_user_exists)):
    _wallet_owned(data.wallet, user)
    relay = await _default_relay()
    if not relay:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Add a relay first.",
        )
    node_key = await get_or_create_node_key(data.wallet)
    debit_id = urlsafe_short_hash()
    ndebit = encode_ndebit(
        NDebit(pubkey=node_key.pubkey, relay=relay, pointer=debit_id)
    )
    service_pubkey = None
    if data.rules:
        try:
            import json

            rules = json.loads(data.rules)
            if isinstance(rules, dict) and rules.get("allowed_pubkeys"):
                service_pubkey = rules["allowed_pubkeys"][0]
        except (ValueError, TypeError):
            pass
    debit = Debit(
        id=debit_id,
        wallet=data.wallet,
        ndebit=ndebit,
        service_pubkey=service_pubkey,
        amount_msat=data.amount_msat,
        frequency_number=data.frequency_number,
        frequency_unit=data.frequency_unit,
        budget_msat=data.budget_msat,
        rules=data.rules,
        state=data.state,
    )
    await create_debit(debit)
    return debit.dict()


@clink_ext_api.put("/api/v1/debits/{debit_id}", description="Update a debit pointer")
async def api_debit_update(
    debit_id: str, data: UpdateDebit, user: User = Depends(check_user_exists)
):
    debit = await get_debit(debit_id)
    if not debit:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Debit not found.")
    _wallet_owned(debit.wallet, user)
    debit.state = data.state
    debit = await update_debit(debit)
    return debit.dict()


@clink_ext_api.delete("/api/v1/debits/{debit_id}", description="Delete a debit pointer")
async def api_debit_delete(debit_id: str, user: User = Depends(check_user_exists)):
    debit = await get_debit(debit_id)
    if not debit:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Debit not found.")
    _wallet_owned(debit.wallet, user)
    await delete_debit(debit_id)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


@clink_ext_api.get("/api/v1/plans", description="List plans for a wallet")
async def api_plans(wallet: str = Query(...), user: User = Depends(check_user_exists)):
    _wallet_owned(wallet, user)
    return [p.dict() for p in await get_plans(wallet)]


@clink_ext_api.post("/api/v1/plans", description="Create a recurring plan")
async def api_plan_create(data: CreatePlan, user: User = Depends(check_user_exists)):
    _wallet_owned(data.wallet, user)
    if (data.amount_msat or 0) <= 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Amount must be positive."
        )
    if data.frequency_unit not in ("day", "week", "month"):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Frequency unit must be day, week or month.",
        )
    if data.frequency_number < 1:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Frequency must be at least 1."
        )
    plan = Plan(
        wallet=data.wallet,
        name=data.name,
        amount_msat=data.amount_msat,
        frequency_number=data.frequency_number,
        frequency_unit=data.frequency_unit,
        description=data.description,
    )
    await create_plan(plan)
    return plan.dict()


@clink_ext_api.put("/api/v1/plans/{plan_id}", description="Update a plan")
async def api_plan_update(
    plan_id: str, data: UpdatePlan, user: User = Depends(check_user_exists)
):
    plan = await get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Plan not found.")
    _wallet_owned(plan.wallet, user)
    plan.active = data.active
    plan = await update_plan(plan)
    return plan.dict()


@clink_ext_api.delete("/api/v1/plans/{plan_id}", description="Delete a plan")
async def api_plan_delete(plan_id: str, user: User = Depends(check_user_exists)):
    plan = await get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Plan not found.")
    _wallet_owned(plan.wallet, user)
    await delete_plan(plan_id)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@clink_ext_api.get(
    "/api/v1/subscriptions", description="List subscriptions for a wallet"
)
async def api_subscriptions(
    wallet: str = Query(...), user: User = Depends(check_user_exists)
):
    _wallet_owned(wallet, user)
    subs = await get_subscriptions(wallet)
    plans = {p.id: p for p in await get_plans(wallet)}
    return [
        {
            **s.dict(),
            "plan_name": plans[s.plan_id].name if s.plan_id in plans else None,
        }
        for s in subs
    ]


@clink_ext_api.post("/api/v1/subscriptions", description="Create a subscription")
async def api_subscription_create(
    data: CreateSubscription, user: User = Depends(check_user_exists)
):
    _wallet_owned(data.wallet, user)
    plan = await get_plan(data.plan_id)
    if not plan:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Plan not found.")
    if plan.wallet != data.wallet:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Plan does not belong to this wallet.",
        )
    try:
        decode_ndebit(data.ndebit)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
        ) from exc
    now = datetime.now(timezone.utc)
    sub = Subscription(
        wallet=data.wallet,
        plan_id=data.plan_id,
        ndebit=data.ndebit,
        payer_npub=data.payer_npub,
        state="active",
        current_period_start=now,
        current_period_end=add_frequency(
            now, plan.frequency_number, plan.frequency_unit
        ),
    )
    await create_subscription(sub)
    return sub.dict()


@clink_ext_api.put(
    "/api/v1/subscriptions/{subscription_id}", description="Update a subscription"
)
async def api_subscription_update(
    subscription_id: str,
    data: UpdateSubscription,
    user: User = Depends(check_user_exists),
):
    sub = await get_subscription(subscription_id)
    if not sub:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Subscription not found."
        )
    _wallet_owned(sub.wallet, user)
    if data.state not in ("active", "paused", "cancelled"):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="State must be active, paused or cancelled.",
        )
    sub.state = data.state
    if data.state == "active":
        sub.attempts = 0
        sub.last_error = None
    sub.updated_at = datetime.now(timezone.utc)
    sub = await update_subscription(sub)
    return sub.dict()


@clink_ext_api.delete(
    "/api/v1/subscriptions/{subscription_id}", description="Delete a subscription"
)
async def api_subscription_delete(
    subscription_id: str, user: User = Depends(check_user_exists)
):
    sub = await get_subscription(subscription_id)
    if not sub:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Subscription not found."
        )
    _wallet_owned(sub.wallet, user)
    await delete_subscription(subscription_id)


@clink_ext_api.post(
    "/api/v1/subscriptions/{subscription_id}/renew",
    description="Bill the current period of a subscription now",
)
async def api_subscription_renew(
    subscription_id: str, user: User = Depends(check_user_exists)
):
    sub = await get_subscription(subscription_id)
    if not sub:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Subscription not found."
        )
    _wallet_owned(sub.wallet, user)
    if sub.state != "active":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Subscription is not active."
        )
    sub = await renew_subscription(sub)
    return sub.dict()


# ---------------------------------------------------------------------------
# Pay (outgoing offers)
# ---------------------------------------------------------------------------


@clink_ext_api.post(
    "/api/v1/pay/parse", description="Parse a noffer string without paying"
)
async def api_pay_parse(data: ParseNofferRequest):
    try:
        decoded = decode_noffer(data.noffer)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "pubkey": decoded.pubkey,
        "relay": decoded.relay,
        "offer": decoded.offer,
        "price_type": decoded.price_type,
        "price": decoded.price,
    }


@clink_ext_api.post("/api/v1/pay", description="Pay a CLINK offer")
async def api_pay(data: PayRequest, user: User = Depends(check_user_exists)):
    _wallet_owned(data.wallet, user)
    try:
        result = await pay_offer(
            noffer=data.noffer,
            wallet_id=data.wallet,
            amount_sats=data.amount_sats,
            description=data.description,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
        ) from exc
    except PayOfferError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY, detail=exc.payload
        ) from exc
    return result


# ---------------------------------------------------------------------------
# Checkout (public)
# ---------------------------------------------------------------------------


@clink_ext_api.post(
    "/api/v1/checkout/{offer_id}", description="Create an invoice for an offer"
)
async def api_checkout(offer_id: str, data: CheckoutRequest | None = None):
    offer = await get_offer(offer_id)
    if not offer or not offer.active:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Offer not found.")
    try:
        decoded = decode_noffer(offer.noffer) if offer.noffer else None
    except ValueError:
        decoded = None
    amount, error = resolve_offer_amount(decoded, data.amount_sats if data else None)
    if error:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=error.get("error", "Invalid Amount"),
        )
    payment = await create_invoice(
        wallet_id=offer.wallet,
        amount=amount,
        memo=offer.name or "CLINK checkout",
        extension="clink",
        external_id=offer.id,
    )
    return {"bolt11": payment.bolt11, "payment_hash": payment.payment_hash}
