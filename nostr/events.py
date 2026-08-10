"""CLINK Nostr events: building, signing and verifying kinds 21001/21002."""

import time

from coincurve import PrivateKey
from lnbits.utils.nostr import json_dumps, sign_event
from lnbits.utils.nostr import verify_event as _verify_event

from .keys import private_key_from_hex

CLINK_VERSION = "1"
KIND_OFFER_REQUEST = 21001
KIND_DEBIT_REQUEST = 21002
KIND_USER_API = 21000

CLINK_VERSION_TAG = ["clink_version", CLINK_VERSION]


def build_event(
    pubkey: str,
    kind: int,
    content: str,
    tags: list[list[str]],
    created_at: int | None = None,
) -> dict:
    """Build an unsigned Nostr event dict (no ``id``/``sig`` yet)."""
    return {
        "id": "",
        "pubkey": pubkey,
        "created_at": created_at if created_at is not None else int(time.time()),
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": "",
    }


def sign(event: dict, private_key_hex: str) -> dict:
    """Sign an unsigned event in place and return it."""
    priv: PrivateKey = private_key_from_hex(private_key_hex)
    return sign_event(event, event["pubkey"], priv)


def finalize(
    private_key_hex: str,
    kind: int,
    content: str,
    tags: list[list[str]],
    created_at: int | None = None,
) -> dict:
    """Build and sign an event in one step."""
    pubkey = private_key_from_hex(private_key_hex).public_key.format().hex()[2:]
    event = build_event(pubkey, kind, content, tags, created_at)
    return sign(event, private_key_hex)


def verify(event: dict) -> bool:
    """Verify an event's signature and id using ``lnbits.utils.nostr``."""
    return _verify_event(event)


verify_event = verify


def serialize_event(event: dict) -> str:
    """NIP-01 canonical serialization (id-hashing form)."""
    return json_dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ]
    )


def get_p_tag(event: dict) -> str | None:
    for tag in event.get("tags", []):
        if tag and tag[0] == "p" and len(tag) > 1:
            return tag[1]
    return None


def get_e_tag(event: dict) -> str | None:
    for tag in event.get("tags", []):
        if tag and tag[0] == "e" and len(tag) > 1:
            return tag[1]
    return None
