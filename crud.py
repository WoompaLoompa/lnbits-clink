from lnbits.db import Database

from .models import (
    K1,
    Debit,
    DebitUsage,
    Invoice,
    NodeKey,
    Offer,
    Plan,
    Relay,
    Subscription,
)

db = Database("ext_clink")


async def create_offer(data: Offer) -> Offer:
    await db.insert("clink.offers", data)
    return data


async def get_offer(offer_id: str) -> Offer | None:
    return await db.fetchone(
        "SELECT * FROM clink.offers WHERE id = :id", {"id": offer_id}, Offer
    )


async def get_offers(wallet_id: str) -> list[Offer]:
    return await db.fetchall(
        "SELECT * FROM clink.offers WHERE wallet = :wallet ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Offer,
    )


async def get_offers_by_pubkey(pubkey: str) -> list[Offer]:
    return await db.fetchall(
        "SELECT * FROM clink.offers WHERE pubkey = :pubkey ORDER BY created_at DESC",
        {"pubkey": pubkey},
        Offer,
    )


async def update_offer(data: Offer) -> Offer | None:
    await db.update("clink.offers", data)
    return await get_offer(data.id)


async def delete_offer(offer_id: str) -> None:
    await db.execute("DELETE FROM clink.offers WHERE id = :id", {"id": offer_id})


async def create_plan(data: Plan) -> Plan:
    await db.insert("clink.plans", data)
    return data


async def get_plan(plan_id: str) -> Plan | None:
    return await db.fetchone(
        "SELECT * FROM clink.plans WHERE id = :id", {"id": plan_id}, Plan
    )


async def get_plans(wallet_id: str) -> list[Plan]:
    return await db.fetchall(
        "SELECT * FROM clink.plans WHERE wallet = :wallet ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Plan,
    )


async def update_plan(data: Plan) -> Plan | None:
    await db.update("clink.plans", data)
    return await get_plan(data.id)


async def delete_plan(plan_id: str) -> None:
    await db.execute("DELETE FROM clink.plans WHERE id = :id", {"id": plan_id})


async def create_subscription(data: Subscription) -> Subscription:
    await db.insert("clink.subscriptions", data)
    return data


async def get_subscription(subscription_id: str) -> Subscription | None:
    return await db.fetchone(
        "SELECT * FROM clink.subscriptions WHERE id = :id",
        {"id": subscription_id},
        Subscription,
    )


async def get_subscriptions(wallet_id: str) -> list[Subscription]:
    return await db.fetchall(
        "SELECT * FROM clink.subscriptions WHERE wallet = :wallet "
        "ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Subscription,
    )


async def get_active_subscriptions() -> list[Subscription]:
    return await db.fetchall(
        "SELECT * FROM clink.subscriptions WHERE state = 'active' "
        "ORDER BY created_at DESC",
        None,
        Subscription,
    )


async def update_subscription(data: Subscription) -> Subscription | None:
    await db.update("clink.subscriptions", data)
    return await get_subscription(data.id)


async def delete_subscription(subscription_id: str) -> None:
    await db.execute(
        "DELETE FROM clink.subscriptions WHERE id = :id", {"id": subscription_id}
    )


async def create_debit(data: Debit) -> Debit:
    await db.insert("clink.debits", data)
    return data


async def get_debit(debit_id: str) -> Debit | None:
    return await db.fetchone(
        "SELECT * FROM clink.debits WHERE id = :id", {"id": debit_id}, Debit
    )


async def get_debits(wallet_id: str) -> list[Debit]:
    return await db.fetchall(
        "SELECT * FROM clink.debits WHERE wallet = :wallet ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Debit,
    )


async def update_debit(data: Debit) -> Debit | None:
    await db.update("clink.debits", data)
    return await get_debit(data.id)


async def delete_debit(debit_id: str) -> None:
    await db.execute("DELETE FROM clink.debits WHERE id = :id", {"id": debit_id})


async def get_or_create_node_key(wallet_id: str) -> NodeKey:
    node_key = await db.fetchone(
        "SELECT * FROM clink.node_keys WHERE wallet = :wallet",
        {"wallet": wallet_id},
        NodeKey,
    )
    if node_key:
        return node_key
    privkey, pubkey = await _generate_node_keypair()
    node_key = NodeKey(wallet=wallet_id, pubkey=pubkey, privkey=privkey)
    await create_node_key(node_key)
    return node_key


async def create_node_key(data: NodeKey) -> NodeKey:
    await db.insert("clink.node_keys", data)
    return data


async def get_node_keys() -> list[NodeKey]:
    return await db.fetchall("SELECT * FROM clink.node_keys", None, NodeKey)


async def _generate_node_keypair() -> tuple[str, str]:
    from .nostr.keys import generate_keypair, pubkey_from_privkey

    privkey, _ = generate_keypair()
    return privkey, pubkey_from_privkey(privkey)


async def get_debit_usage(debit_id: str, period_start: str) -> DebitUsage | None:
    return await db.fetchone(
        "SELECT * FROM clink.debit_usage "
        "WHERE debit_id = :debit_id AND period_start = :period_start",
        {"debit_id": debit_id, "period_start": period_start},
        DebitUsage,
    )


async def create_debit_usage(data: DebitUsage) -> DebitUsage:
    await db.insert("clink.debit_usage", data)
    return data


async def update_debit_usage(debit_id: str, period_start: str, spent_msat: int) -> None:
    await db.execute(
        "UPDATE clink.debit_usage SET spent_msat = :spent_msat "
        "WHERE debit_id = :debit_id AND period_start = :period_start",
        {
            "debit_id": debit_id,
            "period_start": period_start,
            "spent_msat": spent_msat,
        },
    )


async def get_k1(debit_id: str, k1: str) -> K1 | None:
    return await db.fetchone(
        "SELECT * FROM clink.k1s WHERE debit_id = :debit_id AND k1 = :k1",
        {"debit_id": debit_id, "k1": k1},
        K1,
    )


async def create_k1(data: K1) -> K1:
    await db.insert("clink.k1s", data)
    return data


async def create_relay(data: Relay) -> Relay:
    await db.insert("clink.relays", data)
    return data


async def get_relays() -> list[Relay]:
    return await db.fetchall(
        "SELECT * FROM clink.relays ORDER BY created_at DESC", None, Relay
    )


async def get_enabled_relays() -> list[Relay]:
    return await db.fetchall(
        "SELECT * FROM clink.relays WHERE enabled = 1 ORDER BY created_at DESC",
        None,
        Relay,
    )


async def get_relay(relay_id: str) -> Relay | None:
    return await db.fetchone(
        "SELECT * FROM clink.relays WHERE id = :id", {"id": relay_id}, Relay
    )


async def update_relay(data: Relay) -> Relay | None:
    await db.update("clink.relays", data)
    return await get_relay(data.id)


async def delete_relay(relay_id: str) -> None:
    await db.execute("DELETE FROM clink.relays WHERE id = :id", {"id": relay_id})


async def save_invoice(data: Invoice) -> Invoice:
    await db.insert("clink.invoices", data)
    return data


async def get_invoice_by_hash(payment_hash: str) -> Invoice | None:
    return await db.fetchone(
        "SELECT * FROM clink.invoices WHERE payment_hash = :payment_hash",
        {"payment_hash": payment_hash},
        Invoice,
    )


async def get_invoice_by_bolt11(bolt11: str) -> Invoice | None:
    return await db.fetchone(
        "SELECT * FROM clink.invoices WHERE bolt11 = :bolt11",
        {"bolt11": bolt11},
        Invoice,
    )
