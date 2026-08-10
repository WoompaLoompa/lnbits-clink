"""Async Nostr relay client for CLINK over ``websockets``."""

import asyncio
import json
import logging
from collections.abc import Callable

from websockets import connect as ws_connect

logger = logging.getLogger(__name__)

MESSAGE_EVENT = "EVENT"
MESSAGE_REQUEST = "REQ"
MESSAGE_CLOSE = "CLOSE"
MESSAGE_NOTICE = "NOTICE"
MESSAGE_OK = "OK"


class RelayError(Exception):
    """Raised when a relay rejects an event or the connection fails."""


async def _json_send(ws, payload: list) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":")))


async def _json_recv(ws) -> list:
    raw = await ws.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


class RelayClient:
    """A single-relay Nostr connection.

    A long-lived subscription is managed via :meth:`subscribe`, which calls
    ``on_event`` for every matching ``EVENT`` message until :meth:`close`.
    """

    def __init__(self, url: str):
        self.url = url
        self.ws = None

    async def connect(self) -> None:
        self.ws = await ws_connect(self.url)

    async def publish(self, event: dict) -> bool:
        """Publish an event and wait for the relay's ``OK`` ack."""
        if self.ws is None:
            await self.connect()
        await _json_send(self.ws, [MESSAGE_EVENT, event])
        while True:
            msg = await _json_recv(self.ws)
            if msg[0] == MESSAGE_OK and msg[1] == event["id"]:
                if not msg[2]:
                    detail = msg[3] if len(msg) > 3 else "rejected"
                    raise RelayError(f"{self.url}: {detail}")
                return True
            if msg[0] == MESSAGE_NOTICE:
                logger.debug(f"notice from {self.url}: {msg[1]}")

    async def subscribe(
        self,
        sub_id: str,
        filters: dict,
        on_event,
        since: int | None = None,
        until: int | None = None,
    ) -> None:
        """Subscribe and dispatch matching events until :meth:`close`."""
        if self.ws is None:
            await self.connect()
        f: dict = dict(filters)
        if since is not None:
            f["since"] = since
        if until is not None:
            f["until"] = until
        await _json_send(self.ws, [MESSAGE_REQUEST, sub_id, f])
        while True:
            msg = await _json_recv(self.ws)
            if msg[0] == MESSAGE_EVENT and msg[1] == sub_id:
                event = msg[2]
                on_event(event)
            elif msg[0] == MESSAGE_EVENT:
                continue
            elif msg[0] == MESSAGE_NOTICE:
                logger.debug(f"notice from {self.url}: {msg[1]}")

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None


async def request_response(
    relays: list[str],
    request: dict,
    response_filter: dict,
    timeout: float = 30.0,
    match: Callable[[dict], bool] | None = None,
) -> dict:
    """Publish a CLINK request and wait for the response event.

    Subscriptions are opened on every relay *before* the request is
    published, so a fast response cannot be missed. The first event matching
    ``response_filter`` (and ``match``, when given) is returned; otherwise
    :class:`asyncio.TimeoutError`.
    """
    queue: asyncio.Queue[dict] = asyncio.Queue()
    tasks = [
        asyncio.create_task(
            _relay_waiter(url, request, response_filter, queue, match)
        )
        for url in relays
    ]
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _relay_waiter(
    url: str,
    request: dict,
    response_filter: dict,
    queue: asyncio.Queue,
    match: Callable[[dict], bool] | None = None,
) -> None:
    sub_id = "clink-" + request["id"][:16]
    try:
        async with ws_connect(url) as ws:
            f: dict = dict(response_filter)
            await _json_send(ws, [MESSAGE_REQUEST, sub_id, f])
            await _json_send(ws, [MESSAGE_EVENT, request])
            while True:
                msg = await _json_recv(ws)
                if msg[0] == MESSAGE_EVENT and msg[1] == sub_id:
                    event = msg[2]
                    if match is None or match(event):
                        queue.put_nowait(event)
                elif msg[0] == MESSAGE_NOTICE:
                    logger.debug(f"notice from {url}: {msg[1]}")
    except Exception as exc:
        logger.error(f"relay waiter {url} failed: {exc}")
