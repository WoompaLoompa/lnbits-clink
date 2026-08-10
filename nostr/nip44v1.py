"""NIP-44 v1 encrypted payloads (the Lightning.pub user API).

Reference implementation: ``Lightning.Pub/src/services/nostr/nip44v1.ts``
which relies on ``@stablelib/xchacha20``.

Version 1: secp256k1 ECDH, ``sha256(x)`` conversation key, XChaCha20 stream
cipher (no authentication), base64.

The conversation key is the same from both directions:
``get_conversation_key(a, B) == get_conversation_key(b, A)``.

XChaCha20 differs from the ChaCha20 used by NIP-44 v2 in three ways:

- the conversation key is ``sha256(ecdh_x)`` instead of ``hmac("nip44-v2", x)``;
- the nonce is 24 bytes and a per-message ``HChaCha20`` subkey is derived;
- there is no padding, no MAC and no AAD: the plaintext is simply XOR-ed
  with the keystream.
"""

import base64
import hashlib
import json
import secrets
import struct

from coincurve import PrivateKey, PublicKey

VERSION = 1
NONCE_LENGTH = 24
NONCE16_LENGTH = 16

_CONSTANTS = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


def get_conversation_key(private_key_hex: str, public_key_hex: str) -> bytes:
    """Derive the long-term conversation key between two Nostr keys."""
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    pub = PublicKey(bytes.fromhex("02" + public_key_hex))
    shared_x = pub.multiply(priv.secret).format()[1:]
    return hashlib.sha256(shared_x).digest()


def _quarter_round(state, a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 16) | (state[d] >> 16)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 12) | (state[b] >> 20)) & 0xFFFFFFFF
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 8) | (state[d] >> 24)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 7) | (state[b] >> 25)) & 0xFFFFFFFF


def _rounds(state) -> None:
    for _ in range(10):
        _quarter_round(state, 0, 4, 8, 12)
        _quarter_round(state, 1, 5, 9, 13)
        _quarter_round(state, 2, 6, 10, 14)
        _quarter_round(state, 3, 7, 11, 15)
        _quarter_round(state, 0, 5, 10, 15)
        _quarter_round(state, 1, 6, 11, 12)
        _quarter_round(state, 2, 7, 8, 13)
        _quarter_round(state, 3, 4, 9, 14)


def hchacha20(key: bytes, nonce: bytes) -> bytes:
    """HChaCha20 one-way function used to extend the 24-byte nonce."""
    if len(key) != 32 or len(nonce) != NONCE16_LENGTH:
        raise ValueError("HChaCha20 key must be 32 bytes and nonce 16 bytes")
    words = struct.unpack("<8I", key) + struct.unpack("<4I", nonce)
    state = [
        _CONSTANTS[0],
        _CONSTANTS[1],
        _CONSTANTS[2],
        _CONSTANTS[3],
        words[0],
        words[1],
        words[2],
        words[3],
        words[4],
        words[5],
        words[6],
        words[7],
        words[8],
        words[9],
        words[10],
        words[11],
    ]
    _rounds(state)
    return struct.pack(
        "<8I", state[0], state[1], state[2], state[3], state[12], state[13],
        state[14], state[15],
    )


def _chacha20_block(key: bytes, counter: int, nonce12: bytes) -> bytes:
    if len(key) != 32 or len(nonce12) != 12:
        raise ValueError("ChaCha20 key must be 32 bytes and nonce 12 bytes")
    words = struct.unpack("<8I", key)
    nonce = struct.unpack("<3I", nonce12)
    state = [
        _CONSTANTS[0],
        _CONSTANTS[1],
        _CONSTANTS[2],
        _CONSTANTS[3],
        words[0],
        words[1],
        words[2],
        words[3],
        words[4],
        words[5],
        words[6],
        words[7],
        counter & 0xFFFFFFFF,
        nonce[0],
        nonce[1],
        nonce[2],
    ]
    working = list(state)
    _rounds(working)
    return struct.pack(
        "<16I",
        *((s + w) & 0xFFFFFFFF for s, w in zip(state, working, strict=True)),
    )


def _xchacha20_keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    if len(key) != 32 or len(nonce) != NONCE_LENGTH:
        raise ValueError("XChaCha20 key must be 32 bytes and nonce 24 bytes")
    subkey = hchacha20(key, nonce[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce[16:]
    out = bytearray(data)
    counter = 0
    for offset in range(0, len(data), 64):
        block = _chacha20_block(subkey, counter, nonce12)
        for i, _ in enumerate(data[offset : offset + 64]):
            out[offset + i] ^= block[i]
        counter += 1
    return bytes(out)


def encrypt(
    plaintext: str, conversation_key: bytes, nonce: bytes | None = None
) -> str:
    """Encrypt ``plaintext`` into a base64 NIP-44 v1 payload."""
    if len(conversation_key) != 32:
        raise ValueError("invalid conversation_key length")
    nonce = nonce or secrets.token_bytes(NONCE_LENGTH)
    if len(nonce) != NONCE_LENGTH:
        raise ValueError("nonce must be 24 bytes")
    ciphertext = _xchacha20_keystream_xor(
        conversation_key, nonce, plaintext.encode("utf-8")
    )
    return base64.b64encode(bytes([VERSION]) + nonce + ciphertext).decode("ascii")


def _decode_payload(payload: str) -> tuple[bytes, bytes]:
    """Return ``(nonce, ciphertext)`` from a JSON or binary payload."""
    if payload.startswith("{") and payload.endswith("}"):
        parsed = json.loads(payload)
        if parsed.get("v") != VERSION:
            raise ValueError(f"unsupported encryption version {parsed.get('v')}")
        nonce = base64.b64decode(parsed["nonce"])
        ciphertext = base64.b64decode(parsed["ciphertext"])
    else:
        data = base64.b64decode(payload)
        if not data or data[0] != VERSION:
            raise ValueError("unsupported encryption version")
        nonce = data[1 : 1 + NONCE_LENGTH]
        ciphertext = data[1 + NONCE_LENGTH :]
    if len(nonce) != NONCE_LENGTH:
        raise ValueError("invalid nonce length")
    return nonce, ciphertext


def decrypt(payload: str, conversation_key: bytes) -> str:
    """Decrypt a NIP-44 v1 ``payload`` (binary or JSON encoded)."""
    if len(conversation_key) != 32:
        raise ValueError("invalid conversation_key length")
    nonce, ciphertext = _decode_payload(payload)
    return _xchacha20_keystream_xor(conversation_key, nonce, ciphertext).decode(
        "utf-8"
    )


def encrypt_with_keys(plaintext: str, private_key_hex: str, public_key_hex: str) -> str:
    """Encrypt ``plaintext`` for ``public_key_hex`` using a fresh random nonce."""
    return encrypt(
        plaintext,
        get_conversation_key(private_key_hex, public_key_hex),
    )


def decrypt_with_keys(payload: str, private_key_hex: str, public_key_hex: str) -> str:
    """Decrypt a payload received from ``public_key_hex``."""
    return decrypt(payload, get_conversation_key(private_key_hex, public_key_hex))
