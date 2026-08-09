from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.models import User
from lnbits.core.services.payments import create_invoice
from lnbits.decorators import check_user_exists
from lnbits.helpers import urlsafe_short_hash

from .crud import (
    create_debit,
    create_offer,
    create_relay,
    delete_debit,
    delete_offer,
    delete_relay,
    get_debit,
    get_debits,
    get_enabled_relays,
    get_offer,
    get_offers,
    get_or_create_node_key,
    get_relays,
    update_debit,
    update_offer,
)
from .models import (
    CheckoutRequest,
    CreateDebit,
    CreateOffer,
    CreateRelay,
    Debit,
    Offer,
    Relay,
    UpdateDebit,
    UpdateOffer,
    UpdateRelay,
)
from .node import resolve_offer_amount
from .nostr import (
    NDebit,
    NOffer,
    decode_noffer,
    encode_ndebit,
    encode_noffer,
    generate_keypair,
    pubkey_from_privkey,
)
from .nostr.bech32 import PRICE_TYPE_FIXED, PRICE_TYPE_SPONTANEOUS

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
