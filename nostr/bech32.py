"""Bech32 (NIP-19 style TLV) encode/decode for CLINK offers and debits.

CLINK defines two static codes:

- ``noffer1...``: 0 = service pubkey, 1 = relay, 2 = offer id,
  3 = price type (0/1/2), 4 = price (optional, 4-byte BE uint32).
- ``ndebit1...``: 0 = node service pubkey, 1 = relay, 2 = pointer (optional),
  3 = session ``k1`` (optional, 32 raw bytes).

The TLV layout follows NIP-19: ``type (1 byte) | length (1 byte) | value``.
"""

import secrets
from dataclasses import dataclass, field

from bech32 import bech32_decode, bech32_encode, convertbits

NOFFER_HRP = "noffer"
NDEBIT_HRP = "ndebit"
NPROFILE_HRP = "nprofile"

PRICE_TYPE_FIXED = 0
PRICE_TYPE_VARIABLE = 1
PRICE_TYPE_SPONTANEOUS = 2


@dataclass
class NProfile:
    pubkey: str
    relays: list[str] = field(default_factory=list)

    @property
    def relay(self) -> str | None:
        return self.relays[0] if self.relays else None


@dataclass
class NOffer:
    pubkey: str
    relay: str
    offer: str
    price_type: int = PRICE_TYPE_SPONTANEOUS
    price: int | None = None

    @property
    def is_fixed(self) -> bool:
        return self.price_type == PRICE_TYPE_FIXED

    @property
    def is_variable(self) -> bool:
        return self.price_type == PRICE_TYPE_VARIABLE

    @property
    def is_spontaneous(self) -> bool:
        return self.price_type == PRICE_TYPE_SPONTANEOUS


@dataclass
class NDebit:
    pubkey: str
    relay: str
    pointer: str | None = None
    k1: str | None = field(default=None, repr=False)

    @property
    def is_session(self) -> bool:
        return self.k1 is not None


def _convert_bytes_to_words(data: bytes) -> list[int]:
    words = convertbits(list(data), 8, 5, True)
    if words is None:
        raise ValueError("invalid byte conversion")
    return words


def _encode_tlvs(tlvs: list[tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for tlv_type, value in tlvs:
        if len(value) > 255:
            raise ValueError(f"TLV {tlv_type} value too long")
        out.append(tlv_type)
        out.append(len(value))
        out += value
    return bytes(out)


def _bech32_encode(hrp: str, tlvs: list[tuple[int, bytes]]) -> str:
    # @shocknet/clink-sdk emits TLVs in descending type order; parseTLV is
    # order-agnostic on decode but matching it byte-for-byte aids interop.
    ordered = sorted(tlvs, key=lambda item: item[0], reverse=True)
    return bech32_encode(hrp, _convert_bytes_to_words(_encode_tlvs(ordered)))


def encode_noffer(offer: NOffer) -> str:
    """Encode an offer pointer into a ``noffer1...`` string."""
    pubkey = bytes.fromhex(offer.pubkey)
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    if offer.price_type not in (
        PRICE_TYPE_FIXED,
        PRICE_TYPE_VARIABLE,
        PRICE_TYPE_SPONTANEOUS,
    ):
        raise ValueError("price_type must be 0, 1 or 2")
    tlvs = [
        (0, pubkey),
        (1, offer.relay.encode("utf-8")),
        (2, offer.offer.encode("utf-8")),
        (3, bytes([offer.price_type])),
    ]
    if offer.price is not None:
        if not 0 <= offer.price <= 0xFFFFFFFF:
            raise ValueError("price must fit in a uint32")
        tlvs.append((4, offer.price.to_bytes(4, "big")))
    return _bech32_encode(NOFFER_HRP, tlvs)


def encode_ndebit(debit: NDebit) -> str:
    """Encode a debit pointer into an ``ndebit1...`` string."""
    pubkey = bytes.fromhex(debit.pubkey)
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    tlvs: list[tuple[int, bytes]] = [
        (0, pubkey),
        (1, debit.relay.encode("utf-8")),
    ]
    if debit.pointer:
        tlvs.append((2, debit.pointer.encode("utf-8")))
    if debit.k1:
        k1 = bytes.fromhex(debit.k1)
        if len(k1) != 32:
            raise ValueError("k1 must be 32 bytes")
        tlvs.append((3, k1))
    return _bech32_encode(NDEBIT_HRP, tlvs)


def generate_k1() -> str:
    """Generate a fresh 32-byte session identifier as lowercase hex."""
    return secrets.token_hex(32)


def encode_nprofile(profile: NProfile) -> str:
    """Encode a NIP-19 ``nprofile1...`` string from pubkey + relays."""
    pubkey = bytes.fromhex(profile.pubkey)
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    tlvs: list[tuple[int, bytes]] = [(0, pubkey)]
    for relay in profile.relays:
        tlvs.append((1, relay.encode("utf-8")))
    return _bech32_encode(NPROFILE_HRP, tlvs)


def _bech32_decode(
    hrp: str, encoded: str, multi: set[int] | None = None
) -> dict[int, bytes | list[bytes]]:
    """Decode a bech32 ``hrp`` string into its TLVs.

    ``multi`` lists TLV types that may appear more than once; those values are
    returned as lists (NIP-19 ``nprofile`` allows several relay entries).
    """
    multi = multi or set()
    if not encoded.startswith(hrp + "1"):
        raise ValueError(f"not a {hrp} string")
    decoded_hrp, words = bech32_decode(encoded)
    if decoded_hrp != hrp or words is None:
        raise ValueError(f"invalid {hrp} encoding")
    data = convertbits(words, 5, 8, False)
    if data is None:
        raise ValueError(f"invalid {hrp} data")
    tlvs: dict[int, bytes | list[bytes]] = {}
    pos = 0
    while pos < len(data):
        tlv_type = data[pos]
        tlv_len = data[pos + 1]
        value = bytes(data[pos + 2 : pos + 2 + tlv_len])
        if tlv_type in tlvs and tlv_type not in multi:
            raise ValueError(f"duplicate TLV type {tlv_type}")
        if tlv_type in multi:
            tlvs.setdefault(tlv_type, []).append(value)
        else:
            tlvs[tlv_type] = value
        pos += 2 + tlv_len
    return tlvs


def decode_noffer(encoded: str) -> NOffer:
    """Decode a ``noffer1...`` string into an :class:`NOffer`."""
    tlvs = _bech32_decode(NOFFER_HRP, encoded)
    pubkey = tlvs.get(0)
    relay = tlvs.get(1)
    offer_id = tlvs.get(2)
    if not pubkey or len(pubkey) != 32:
        raise ValueError("noffer missing pubkey")
    if not relay or not offer_id:
        raise ValueError("noffer missing relay or offer id")
    price_type = int(tlvs[3][0]) if tlvs.get(3) else PRICE_TYPE_SPONTANEOUS
    price = None
    if tlvs.get(4):
        price = int.from_bytes(tlvs[4], "big")
    return NOffer(
        pubkey=pubkey.hex(),
        relay=relay.decode("utf-8"),
        offer=offer_id.decode("utf-8"),
        price_type=price_type,
        price=price,
    )


def decode_nprofile(encoded: str) -> NProfile:
    """Decode a NIP-19 ``nprofile1...`` string into an :class:`NProfile`."""
    tlvs = _bech32_decode(NPROFILE_HRP, encoded, multi={1})
    pubkey = tlvs.get(0)
    if not pubkey or len(pubkey) != 32:
        raise ValueError("nprofile missing pubkey")
    relays = [relay.decode("utf-8") for relay in tlvs.get(1, [])]
    return NProfile(pubkey=pubkey.hex(), relays=relays)


def decode_ndebit(encoded: str) -> NDebit:
    """Decode an ``ndebit1...`` string into an :class:`NDebit`."""
    tlvs = _bech32_decode(NDEBIT_HRP, encoded)
    pubkey = tlvs.get(0)
    relay = tlvs.get(1)
    if not pubkey or len(pubkey) != 32:
        raise ValueError("ndebit missing pubkey")
    if not relay:
        raise ValueError("ndebit missing relay")
    pointer = tlvs.get(2)
    k1 = tlvs.get(3)
    if k1 and len(k1) != 32:
        raise ValueError("ndebit k1 must be 32 bytes")
    return NDebit(
        pubkey=pubkey.hex(),
        relay=relay.decode("utf-8"),
        pointer=pointer.decode("utf-8") if pointer else None,
        k1=k1.hex() if k1 else None,
    )
