"""Key helpers for CLINK, reusing LNbits' Nostr utilities."""

from coincurve import PrivateKey
from lnbits.utils.nostr import (
    generate_keypair,
    normalize_private_key,
    normalize_public_key,
)

__all__ = [
    "generate_keypair",
    "normalize_private_key",
    "normalize_public_key",
    "private_key_from_hex",
    "pubkey_from_privkey",
    "public_key_from_hex",
]


def private_key_from_hex(hex_secret: str) -> PrivateKey:
    return PrivateKey(bytes.fromhex(hex_secret))


def pubkey_from_privkey(hex_secret: str) -> str:
    """Return the 64-char x-only (NIP-01) public key for a hex private key."""
    return private_key_from_hex(hex_secret).public_key.format().hex()[2:]


def public_key_from_hex(hex_key: str) -> bytes:
    """Return the 33-byte compressed representation of a Nostr public key."""
    from coincurve import PublicKey

    return PublicKey(bytes.fromhex("02" + hex_key)).format()
