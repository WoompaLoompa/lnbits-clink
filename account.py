"""Lightning.pub user API (CLINK kind 21000) client.

A Lightning.pub *account* turns a CLINK funding source from receive-only into
a full wallet: it can mint invoices (``NewInvoice``), pay out
(``PayInvoice``), report its balance (``GetUserInfo``) and track payment state
(``GetPaymentState``) over the Nostr user API.

The account is configured as a connection string:

``nprofile1...`` or ``nprofile1...:token``

The ``nprofile`` carries the node pubkey and relay; the optional ``:token``
suffix is the admin enroll token and is currently unused by the wallet (kept
for future admin flows).

Signing/encryption identity: the user API authenticates the *event pubkey*,
and the node auto-creates a user for any new pubkey on first request
(``GetOrCreateNostrAppUser``). This wallet therefore derives a deterministic
keypair from the connection string, so a given admin field value always maps
to the same node user.

Request envelope (NIP-44 v1 encrypted, kind 21000, ``p`` tag to the node)::

    {"rpcName", "authIdentifier": <own pubkey>, "requestId", "body": {...}}

Response: kind 21000 from the node, v1-encrypted ``{status, ...payload,
requestId}``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets

from coincurve import PrivateKey
from loguru import logger

from .nostr.bech32 import NProfile, decode_nprofile
from .nostr.events import CLINK_VERSION_TAG, KIND_USER_API, finalize, verify_event
from .nostr.nip44v1 import decrypt as decrypt_v1
from .nostr.nip44v1 import encrypt as encrypt_v1
from .nostr.nip44v1 import get_conversation_key
from .nostr.relay import request_response
from .pay import REQUEST_TIMEOUT_SECONDS, _relay_list

TOKEN_CHARSET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"
)


class AccountError(Exception):
    """Raised when a Lightning.pub user API call fails."""

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason


def parse_account(account: str) -> tuple[NProfile, str | None]:
    """Parse a connection string into ``(nprofile, token)``.

    Accepts ``nprofile1...`` and ``nprofile1...:token``. The token charset is
    restricted to URL-safe characters (as in the wallet apps).
    """
    account = account.strip()
    if ":" in account:
        nprofile_str, token = account.split(":", 1)
    else:
        nprofile_str, token = account, None
    if not nprofile_str.startswith("nprofile1"):
        raise ValueError("account must start with nprofile1...")
    if token is not None and (not token or any(c not in TOKEN_CHARSET for c in token)):
        raise ValueError("invalid account token")
    return decode_nprofile(nprofile_str), token


def derive_keys(account: str) -> tuple[str, str]:
    """Deterministically derive ``(priv, pub)`` hex keys from the account.

    A given connection string always maps to the same node user.
    """
    digest = hashlib.sha256(account.encode("utf-8")).digest()
    priv = PrivateKey(digest)
    return priv.to_hex(), priv.public_key.format().hex()[2:]


async def account_request(
    account: str,
    rpc_name: str,
    body: dict | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Send a kind 21000 user API request and return the decoded response."""
    parsed, _ = parse_account(account)
    if not parsed.pubkey:
        raise AccountError("account is missing the node pubkey")
    priv, pub = derive_keys(account)
    conversation_key = get_conversation_key(priv, parsed.pubkey)
    request_id = secrets.token_hex(8)
    envelope: dict = {
        "rpcName": rpc_name,
        "authIdentifier": pub,
        "requestId": request_id,
    }
    if body is not None:
        envelope["body"] = body
    content = encrypt_v1(
        json.dumps(envelope, separators=(",", ":")), conversation_key
    )
    event = finalize(
        priv,
        KIND_USER_API,
        content,
        [["p", parsed.pubkey], CLINK_VERSION_TAG],
    )
    response_filter = {
        "kinds": [KIND_USER_API],
        "#p": [pub],
        "since": event["created_at"] - 5,
    }
    relays = await _relay_list(parsed.relay)
    try:
        response_event = await request_response(
            relays,
            event,
            response_filter,
            timeout=timeout,
            match=lambda e: e.get("pubkey") == parsed.pubkey,
        )
    except asyncio.TimeoutError as exc:
        raise AccountError(
            f"No response from the node for {rpc_name} (timeout)."
        ) from exc

    if not verify_event(response_event):
        raise AccountError("Invalid response signature.")
    try:
        result = json.loads(decrypt_v1(response_event["content"], conversation_key))
    except Exception as exc:
        raise AccountError(f"Cannot decrypt the node response: {exc}") from exc
    if result.get("requestId") != request_id:
        raise AccountError("Response requestId does not match.")
    if result.get("status") == "ERROR":
        raise AccountError(
            f"Node returned an error for {rpc_name}: {result.get('reason')}",
            result.get("reason"),
        )
    return result


async def get_user_info(account: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    """Return the node user info (``balance`` in sats, fees, noffer, ...)."""
    return await account_request(account, "GetUserInfo", timeout=timeout)


async def new_invoice(
    account: str,
    amount_sats: int,
    memo: str | None = None,
    expiry: int | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> str:
    """Mint a BOLT11 invoice on the node and return it."""
    body: dict = {"amountSats": int(amount_sats)}
    if memo:
        body["memo"] = memo
    if expiry is not None:
        body["expiry"] = int(expiry)
    result = await account_request(account, "NewInvoice", body=body, timeout=timeout)
    bolt11 = result.get("invoice")
    if not bolt11:
        raise AccountError("NewInvoice response did not include an invoice.")
    return bolt11


async def pay_invoice(
    account: str,
    bolt11: str,
    amount_sats: int | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Pay a BOLT11 invoice from the node user's balance."""
    body: dict = {"invoice": bolt11}
    if amount_sats is not None:
        body["amount"] = int(amount_sats)
    result = await account_request(account, "PayInvoice", body=body, timeout=timeout)
    return result


async def get_payment_state(
    account: str, bolt11: str, timeout: float = REQUEST_TIMEOUT_SECONDS
) -> dict:
    """Query the state of a previously created/paid invoice."""
    result = await account_request(
        account, "GetPaymentState", body={"invoice": bolt11}, timeout=timeout
    )
    return result


def log_account_error(rpc_name: str, exc: Exception) -> None:
    """Log a user API failure with the account's own key redacted."""
    logger.warning(f"clink: account {rpc_name} failed: {exc}")
