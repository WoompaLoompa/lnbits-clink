from datetime import datetime, timezone

from lnbits.helpers import urlsafe_short_hash
from pydantic import BaseModel, Field


class Offer(BaseModel):
    id: str = Field(default_factory=lambda: urlsafe_short_hash())
    wallet: str
    name: str | None = None
    noffer: str | None = None
    pubkey: str | None = None
    privkey: str | None = None
    amount_msat: int | None = None
    description: str | None = None
    relay: str | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateOffer(BaseModel):
    wallet: str
    name: str | None = None
    amount_msat: int | None = None
    description: str | None = None
    relay: str | None = None


class UpdateOffer(BaseModel):
    active: bool


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: urlsafe_short_hash())
    wallet: str
    name: str
    amount_msat: int
    frequency_number: int
    frequency_unit: str
    description: str | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreatePlan(BaseModel):
    wallet: str
    name: str
    amount_msat: int
    frequency_number: int
    frequency_unit: str
    description: str | None = None


class Subscription(BaseModel):
    id: str = Field(default_factory=lambda: urlsafe_short_hash())
    wallet: str
    plan_id: str
    payer_npub: str | None = None
    state: str = "active"
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateSubscription(BaseModel):
    wallet: str
    plan_id: str
    payer_npub: str | None = None


class Debit(BaseModel):
    id: str = Field(default_factory=lambda: urlsafe_short_hash())
    wallet: str
    ndebit: str | None = None
    service_pubkey: str | None = None
    amount_msat: int | None = None
    frequency_number: int | None = None
    frequency_unit: str | None = None
    budget_msat: int | None = None
    rules: str | None = None
    state: str = "pending"
    k1: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateDebit(BaseModel):
    wallet: str
    amount_msat: int | None = None
    frequency_number: int | None = None
    frequency_unit: str | None = None
    budget_msat: int | None = None
    rules: str | None = None
    state: str = "active"


class UpdateDebit(BaseModel):
    state: str


class NodeKey(BaseModel):
    wallet: str
    pubkey: str
    privkey: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DebitUsage(BaseModel):
    debit_id: str
    period_start: str
    spent_msat: int = 0


class K1(BaseModel):
    debit_id: str
    k1: str
    used_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CheckoutRequest(BaseModel):
    amount_sats: int | None = None


class PayRequest(BaseModel):
    wallet: str
    noffer: str
    amount_sats: int | None = None
    description: str | None = None


class ParseNofferRequest(BaseModel):
    noffer: str


class Relay(BaseModel):
    id: str = Field(default_factory=lambda: urlsafe_short_hash())
    url: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateRelay(BaseModel):
    url: str
    enabled: bool = True


class UpdateRelay(BaseModel):
    enabled: bool
