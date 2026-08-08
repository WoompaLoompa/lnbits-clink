import pytest
from clink.nostr import (
    NDebit,
    NOffer,
    decode_ndebit,
    decode_noffer,
    encode_ndebit,
    encode_noffer,
)
from clink.nostr.bech32 import (
    PRICE_TYPE_FIXED,
    PRICE_TYPE_SPONTANEOUS,
    generate_k1,
)

PUBKEY = "71b70bace4867c80be6b083d449c37486f33790df0ea4bbe6e9ce617bee34b66"
RELAY = "wss://relay.example.com"
K1 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

NOFFER_ENCODED = (
    "noffer1qszqqqqraqpszqqzp9hkven9wf0kzcnrqythwumn8ghj7un9d3shjtn90psk6urvv5"
    "hxxmmdqqs8rdct4njgvlyqhe4ss02ynsm5smen0yxlp6jthehfeeshhm35kes9yppf9"
)
NDEBIT_ENCODED = (
    "ndebit1qgy8xar0wfj47dpjqythwumn8ghj7un9d3shjtn90psk6urvv5hxxmmdqqs8rdct4nj"
    "gvlyqhe4ss02ynsm5smen0yxlp6jthehfeeshhm35kesh4xxz5"
)
NDEBIT_SESSION_ENCODED = (
    "ndebit1qvsqzg69v7y6hn00qy352euf40x77qfrg4ncn27dauqjx3t83x4ummczppehgmmjv40"
    "ngvspzamhxue69uhhyetvv9ujuetcv9khqmr99e3k7mgqypcmwzavujr8eq97dvyr63yuxayx7"
    "vmephcw5ja7d6wwv9a7ud9kvg6dys8"
)


def test_noffer_interop_roundtrip():
    offer = NOffer(
        pubkey=PUBKEY,
        relay=RELAY,
        offer="offer_abc",
        price_type=PRICE_TYPE_FIXED,
        price=1000,
    )
    assert encode_noffer(offer) == NOFFER_ENCODED
    decoded = decode_noffer(NOFFER_ENCODED)
    assert decoded.pubkey == PUBKEY
    assert decoded.relay == RELAY
    assert decoded.offer == "offer_abc"
    assert decoded.price_type == PRICE_TYPE_FIXED
    assert decoded.price == 1000


def test_ndebit_interop_roundtrip():
    debit = NDebit(pubkey=PUBKEY, relay=RELAY, pointer="store_42")
    assert encode_ndebit(debit) == NDEBIT_ENCODED
    decoded = decode_ndebit(NDEBIT_ENCODED)
    assert decoded.pubkey == PUBKEY
    assert decoded.relay == RELAY
    assert decoded.pointer == "store_42"
    assert decoded.k1 is None
    assert not decoded.is_session


def test_ndebit_session_k1_interop_roundtrip():
    debit = NDebit(pubkey=PUBKEY, relay=RELAY, pointer="store_42", k1=K1)
    assert encode_ndebit(debit) == NDEBIT_SESSION_ENCODED
    decoded = decode_ndebit(NDEBIT_SESSION_ENCODED)
    assert decoded.pubkey == PUBKEY
    assert decoded.relay == RELAY
    assert decoded.pointer == "store_42"
    assert decoded.k1 == K1
    assert decoded.is_session


def test_offer_defaults_to_spontaneous():
    offer = NOffer(pubkey=PUBKEY, relay=RELAY, offer="tip_jar")
    assert offer.price_type == PRICE_TYPE_SPONTANEOUS
    assert offer.price is None
    encoded = encode_noffer(offer)
    decoded = decode_noffer(encoded)
    assert decoded.offer == "tip_jar"
    assert decoded.is_spontaneous


def test_roundtrip_variable_price():
    offer = NOffer(pubkey=PUBKEY, relay=RELAY, offer="fx", price_type=1, price=500)
    assert decode_noffer(encode_noffer(offer)).price == 500


def test_roundtrip_ndebit_without_pointer():
    debit = NDebit(pubkey=PUBKEY, relay=RELAY)
    assert decode_ndebit(encode_ndebit(debit)).pointer is None


def test_generate_k1_format():
    k1 = generate_k1()
    assert len(k1) == 64
    assert len(bytes.fromhex(k1)) == 32


def test_bad_noffer_raises():
    with pytest.raises(ValueError):
        decode_noffer("noffer1qqqqqq")
    with pytest.raises(ValueError):
        decode_noffer("npub1qqqqqqqq")
