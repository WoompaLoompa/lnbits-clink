"""NIP-44 v2 encrypted payloads.

Reference: https://github.com/nostr-protocol/nips/blob/master/44.md
Audited implementation spec: https://github.com/paulmillr/nip44

Version 2: secp256k1 ECDH, HKDF-SHA256, custom padding, ChaCha20,
HMAC-SHA256, base64.

The conversation key is the same from both directions:
``get_conversation_key(a, B) == get_conversation_key(b, A)``.
"""

import base64
import hashlib
import hmac
import math
import secrets

from coincurve import PrivateKey, PublicKey
from Cryptodome.Cipher import ChaCha20

MIN_PLAINTEXT_LENGTH = 1
MAX_PLAINTEXT_LENGTH = 65535
MIN_ENCODED_LENGTH = 132
MAX_ENCODED_LENGTH = 87472
MIN_PAYLOAD_LENGTH = 99
MAX_PAYLOAD_LENGTH = 65603
KDF_SALT = b"nip44-v2"
VERSION = 2


def _hmac_sha256(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    okm = bytearray()
    t = b""
    counter = 1
    while len(okm) < length:
        t = _hmac_sha256(prk, t + info + bytes([counter]))
        okm += t
        counter += 1
    return bytes(okm[:length])


def calc_padded_len(unpadded_len: int) -> int:
    """Calculate the padded length for a plaintext of ``unpadded_len`` bytes."""
    if unpadded_len <= 32:
        return 32
    next_power = 1 << (math.floor(math.log2(unpadded_len - 1)) + 1)
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * (math.floor((unpadded_len - 1) / chunk) + 1)


def _pad(plaintext: bytes) -> bytes:
    unpadded_len = len(plaintext)
    if not MIN_PLAINTEXT_LENGTH <= unpadded_len <= MAX_PLAINTEXT_LENGTH:
        raise ValueError("invalid plaintext length")
    prefix = unpadded_len.to_bytes(2, "big")
    suffix = bytes(calc_padded_len(unpadded_len) - unpadded_len)
    return prefix + plaintext + suffix


def _unpad(padded: bytes) -> bytes:
    unpadded_len = int.from_bytes(padded[0:2], "big")
    unpadded = padded[2 : 2 + unpadded_len]
    if (
        unpadded_len == 0
        or len(unpadded) != unpadded_len
        or len(padded) != 2 + calc_padded_len(unpadded_len)
    ):
        raise ValueError("invalid padding")
    return unpadded


def get_conversation_key(private_key_hex: str, public_key_hex: str) -> bytes:
    """Derive the long-term conversation key between two Nostr keys."""
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    pub = PublicKey(bytes.fromhex("02" + public_key_hex))
    shared_x = pub.multiply(priv.secret).format()[1:]
    return _hmac_sha256(KDF_SALT, shared_x)


def get_message_keys(
    conversation_key: bytes, nonce: bytes
) -> tuple[bytes, bytes, bytes]:
    """Derive the per-message ``(chacha_key, chacha_nonce, hmac_key)``."""
    if len(conversation_key) != 32:
        raise ValueError("invalid conversation_key length")
    if len(nonce) != 32:
        raise ValueError("invalid nonce length")
    keys = _hkdf_expand(conversation_key, nonce, 76)
    return keys[0:32], keys[32:44], keys[44:76]


def encrypt(plaintext: str, conversation_key: bytes, nonce: bytes) -> str:
    """Encrypt ``plaintext`` into a base64 NIP-44 v2 payload."""
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    padded = _pad(plaintext.encode("utf-8"))
    cipher = ChaCha20.new(key=chacha_key, nonce=chacha_nonce)
    ciphertext = cipher.encrypt(padded)
    mac = _hmac_sha256(hmac_key, nonce + ciphertext)
    return base64.b64encode(bytes([VERSION]) + nonce + ciphertext + mac).decode("ascii")


def decrypt(payload: str, conversation_key: bytes) -> str:
    """Decrypt and authenticate a base64 NIP-44 v2 ``payload``."""
    plen = len(payload)
    if plen == 0 or payload[0] == "#":
        raise ValueError("unknown version")
    if not MIN_ENCODED_LENGTH <= plen <= MAX_ENCODED_LENGTH:
        raise ValueError("invalid payload size")
    data = base64.b64decode(payload)
    dlen = len(data)
    if not MIN_PAYLOAD_LENGTH <= dlen <= MAX_PAYLOAD_LENGTH:
        raise ValueError("invalid data size")
    version = data[0]
    if version != VERSION:
        raise ValueError(f"unknown version {version}")
    nonce = data[1:33]
    ciphertext = data[33 : dlen - 32]
    mac = data[dlen - 32 : dlen]
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    calculated_mac = _hmac_sha256(hmac_key, nonce + ciphertext)
    if not hmac.compare_digest(calculated_mac, mac):
        raise ValueError("invalid MAC")
    cipher = ChaCha20.new(key=chacha_key, nonce=chacha_nonce)
    padded_plaintext = cipher.decrypt(ciphertext)
    return _unpad(padded_plaintext).decode("utf-8")


def encrypt_with_keys(plaintext: str, private_key_hex: str, public_key_hex: str) -> str:
    """Encrypt ``plaintext`` for ``public_key_hex`` using a fresh random nonce."""
    return encrypt(
        plaintext,
        get_conversation_key(private_key_hex, public_key_hex),
        secrets.token_bytes(32),
    )


def decrypt_with_keys(payload: str, private_key_hex: str, public_key_hex: str) -> str:
    """Decrypt a payload received from ``public_key_hex``."""
    return decrypt(payload, get_conversation_key(private_key_hex, public_key_hex))
