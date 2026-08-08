import time

from clink.nostr import build_event, verify_event
from clink.nostr.events import (
    CLINK_VERSION_TAG,
    finalize,
    get_e_tag,
    get_p_tag,
)


def _pubkey(priv_hex: str) -> str:
    from coincurve import PrivateKey

    return PrivateKey(bytes.fromhex(priv_hex)).public_key.format().hex()[2:]


def test_build_and_verify_event():
    priv = "11" * 32
    pub = _pubkey(priv)
    tags = [["p", "aa" * 32], CLINK_VERSION_TAG]
    event = finalize(
        priv,
        kind=21001,
        content="encrypted-payload",
        tags=tags,
        created_at=1700000000,
    )
    assert event["id"]
    assert event["sig"]
    assert event["pubkey"] == pub
    assert verify_event(event)


def test_verify_rejects_tampered_event():
    priv = "22" * 32
    event = finalize(priv, kind=21002, content="x", tags=[])
    event["content"] = "tampered"
    assert not verify_event(event)


def test_get_p_and_e_tags():
    event = build_event(
        pubkey="aa" * 32,
        kind=21001,
        content="",
        tags=[["p", "bb" * 32], ["e", "cc" * 32], CLINK_VERSION_TAG],
    )
    assert get_p_tag(event) == "bb" * 32
    assert get_e_tag(event) == "cc" * 32


def test_finalize_uses_current_time_when_not_given():
    priv = "33" * 32
    before = int(time.time())
    event = finalize(priv, kind=21001, content="", tags=[])
    after = int(time.time())
    assert before <= event["created_at"] <= after
