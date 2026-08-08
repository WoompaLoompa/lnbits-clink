from lnbits.db import Database

from .models import Debit, Offer, Plan, Relay, Subscription

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
        "SELECT * FROM clink.offers WHERE wallet = :wallet " "ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Offer,
    )


async def update_offer(offer_id: str, **kwargs) -> Offer | None:
    await db.update(
        "clink.offers", **kwargs, where="id = :id", where_values={"id": offer_id}
    )
    return await get_offer(offer_id)


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
        "SELECT * FROM clink.plans WHERE wallet = :wallet " "ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Plan,
    )


async def update_plan(plan_id: str, **kwargs) -> Plan | None:
    await db.update(
        "clink.plans", **kwargs, where="id = :id", where_values={"id": plan_id}
    )
    return await get_plan(plan_id)


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


async def update_subscription(subscription_id: str, **kwargs) -> Subscription | None:
    await db.update(
        "clink.subscriptions",
        **kwargs,
        where="id = :id",
        where_values={"id": subscription_id},
    )
    return await get_subscription(subscription_id)


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
        "SELECT * FROM clink.debits WHERE wallet = :wallet " "ORDER BY created_at DESC",
        {"wallet": wallet_id},
        Debit,
    )


async def update_debit(debit_id: str, **kwargs) -> Debit | None:
    await db.update(
        "clink.debits", **kwargs, where="id = :id", where_values={"id": debit_id}
    )
    return await get_debit(debit_id)


async def delete_debit(debit_id: str) -> None:
    await db.execute("DELETE FROM clink.debits WHERE id = :id", {"id": debit_id})


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


async def delete_relay(relay_id: str) -> None:
    await db.execute("DELETE FROM clink.relays WHERE id = :id", {"id": relay_id})
