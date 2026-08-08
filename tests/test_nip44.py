import hashlib
import json
import pathlib

import pytest
from clink.nostr import decrypt, encrypt, get_conversation_key
from clink.nostr.nip44 import calc_padded_len, decrypt_with_keys, encrypt_with_keys

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parent / "vectors" / "nip44.vectors.json"
)
VECTORS_SHA256 = "269ed0f69e4c192512cc779e78c555090cebc7c785b609e338a62afc3ce25040"


@pytest.fixture(scope="module")
def vectors():
    raw = VECTORS_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == VECTORS_SHA256
    return json.loads(raw)["v2"]


def pubkey_from_secret(sec: str) -> str:
    from coincurve import PrivateKey

    return PrivateKey(bytes.fromhex(sec)).public_key.format().hex()[2:]


def test_vectors_checksum():
    raw = VECTORS_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == VECTORS_SHA256


def test_get_conversation_key(vectors):
    for i, v in enumerate(vectors["valid"]["get_conversation_key"]):
        assert (
            get_conversation_key(v["sec1"], v["pub2"]).hex() == v["conversation_key"]
        ), f"vector {i}"


def test_get_message_keys(vectors):
    from clink.nostr.nip44 import get_message_keys

    conv = bytes.fromhex(vectors["valid"]["get_message_keys"]["conversation_key"])
    for v in vectors["valid"]["get_message_keys"]["keys"]:
        ck, cn, hk = get_message_keys(conv, bytes.fromhex(v["nonce"]))
        assert ck.hex() == v["chacha_key"]
        assert cn.hex() == v["chacha_nonce"]
        assert hk.hex() == v["hmac_key"]


def test_calc_padded_len(vectors):
    for unpadded, expected in vectors["valid"]["calc_padded_len"]:
        assert calc_padded_len(unpadded) == expected


def test_encrypt_decrypt_vectors(vectors):
    for i, v in enumerate(vectors["valid"]["encrypt_decrypt"]):
        pub2 = pubkey_from_secret(v["sec2"])
        ck = get_conversation_key(v["sec1"], pub2)
        assert ck.hex() == v["conversation_key"], f"conv key {i}"
        nonce = bytes.fromhex(v["nonce"])
        assert encrypt(v["plaintext"], ck, nonce) == v["payload"], f"payload {i}"
        pub1 = pubkey_from_secret(v["sec1"])
        ck2 = get_conversation_key(v["sec2"], pub1)
        assert decrypt(v["payload"], ck2) == v["plaintext"], f"plaintext {i}"


def test_encrypt_decrypt_long_msg_vectors(vectors):
    for i, v in enumerate(vectors["valid"]["encrypt_decrypt_long_msg"]):
        ck = bytes.fromhex(v["conversation_key"])
        nonce = bytes.fromhex(v["nonce"])
        plaintext = (v["pattern"] * v["repeat"]).encode()
        assert hashlib.sha256(plaintext).hexdigest() == v["plaintext_sha256"], i
        payload = encrypt(plaintext.decode("utf-8"), ck, nonce)
        assert hashlib.sha256(payload.encode()).hexdigest() == v["payload_sha256"], i
        assert decrypt(payload, ck).encode() == plaintext, i


def test_invalid_plaintext_lengths(vectors):
    from clink.nostr.nip44 import _pad

    for length in vectors["invalid"]["encrypt_msg_lengths"]:
        with pytest.raises(ValueError):
            _pad(bytes(length))


def test_invalid_conversation_keys(vectors):
    for v in vectors["invalid"]["get_conversation_key"]:
        with pytest.raises(ValueError):
            get_conversation_key(v["sec1"], v["pub2"])


def test_invalid_decrypts(vectors):
    for v in vectors["invalid"]["decrypt"]:
        with pytest.raises(ValueError):
            decrypt(v["payload"], bytes.fromhex(v["conversation_key"]))


def test_conversation_key_symmetry():
    priv_a = "3" * 64
    priv_b = "4" * 64
    pub_a = pubkey_from_secret(priv_a)
    pub_b = pubkey_from_secret(priv_b)
    assert get_conversation_key(priv_a, pub_b) == get_conversation_key(priv_b, pub_a)


def test_clink_sdk_conversation_key_vector():
    # @shocknet/clink-sdk test-vectors/interop.json fixed_conversation_key_vector
    assert (
        get_conversation_key(
            "0" * 63 + "1",
            "c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
        ).hex()
        == "c41c775356fd92eadc63ff5a0dc1da211b268cbea22316767095b2871ea1412d"
    )


def test_roundtrip_random_nonces():
    priv_a = "5" * 64
    priv_b = "6" * 64
    pub_b = pubkey_from_secret(priv_b)
    payload = encrypt_with_keys("hello clink", priv_a, pub_b)
    assert decrypt_with_keys(payload, priv_b, pubkey_from_secret(priv_a)) == (
        "hello clink"
    )


def test_payload_rejects_mangled_mac():
    priv_a = "7" * 64
    priv_b = "8" * 64
    pub_b = pubkey_from_secret(priv_b)
    payload = encrypt_with_keys("tamper me", priv_a, pub_b)
    ck = get_conversation_key(priv_b, pubkey_from_secret(priv_a))
    tampered = payload[:-4] + ("AAAA" if not payload.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        decrypt(tampered, ck)
