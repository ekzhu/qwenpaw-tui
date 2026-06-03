# -*- coding: utf-8 -*-
"""HTTP/SSE transport: drive a running ``qwenpaw app`` server over the network.

Used for ``paw --remote http(s)://host[:port]``. Unlike the ACP transport
(stdio subprocess), this talks to an already-running QwenPaw server:

* send       -> ``POST /api/console/chat`` (SSE stream → ``SseParser``)
* interrupt  -> ``POST /api/console/chat/stop?chat_id=...``
* permission -> polled from ``GET /api/console/push-messages`` and resolved via
  ``POST /api/approval/{approve,deny}``

Auth: a configured token (``--token`` / ``PAW_TOKEN`` / ``QWENPAW_TOKEN``) is
sent as ``Authorization: Bearer <token>`` (needed when the server runs with
``QWENPAW_AUTH_ENABLED``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, AsyncIterator

from ..events import (
    Connected,
    PermissionOption,
    PermissionRequest,
    TransportError,
    TurnEnded,
    TuiEvent,
)
from ..sse import SseParser

logger = logging.getLogger(__name__)

_CLOSED = object()
_POLL_INTERVAL = 1.5


class HttpTransport:
    """Drive a remote ``qwenpaw app`` over HTTP + SSE."""

    def __init__(
        self,
        base_url: str,
        *,
        agent: str | None = None,
        user_id: str = "paw",
        token: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._agent = agent
        self._user = user_id
        self._token = (
            token
            or os.environ.get("PAW_TOKEN")
            or os.environ.get("QWENPAW_TOKEN")
        )
        self._session_id = f"paw:{uuid.uuid4().hex[:12]}"
        self._chat_id: str | None = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._client: Any = None
        self._turn_task: asyncio.Task[Any] | None = None
        self._poll_task: asyncio.Task[Any] | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._closed = False

    # -- helpers -------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._agent:
            h["X-Agent-Id"] = self._agent
        return h

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> Connected:
        import httpx

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=None, write=30.0, pool=None)
        )
        try:
            resp = await self._client.get(
                self._url("/api/version"), headers=self._headers()
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"cannot reach qwenpaw app at {self._base}: {exc}"
            ) from exc

        # Create a chat so we have a chat_id for the stop endpoint (optional).
        try:
            resp = await self._client.post(
                self._url("/api/chats"),
                headers=self._headers(),
                json={
                    "name": "paw",
                    "session_id": self._session_id,
                    "user_id": self._user,
                    "channel": "console",
                },
            )
            if resp.status_code < 300:
                self._chat_id = resp.json().get("id")
        except Exception as exc:  # noqa: BLE001
            logger.debug("chat create failed (non-fatal): %s", exc)

        return Connected(
            session_id=self._session_id, agent=self._agent, model=None
        )

    async def send(self, text: str) -> None:
        if self._client is None:
            raise RuntimeError("transport not started")
        if self._turn_task is not None and not self._turn_task.done():
            raise RuntimeError("a turn is already in progress")
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def _run_turn(self, text: str) -> None:
        parser = SseParser()
        body = {
            "input": [
                {
                    "role": "user",
                    "type": "message",
                    "content": [{"type": "text", "text": text}],
                }
            ],
            "session_id": self._session_id,
            "user_id": self._user,
            "channel": "console",
            "stream": True,
        }
        self._start_poll()
        ended = False
        try:
            async with self._client.stream(
                "POST",
                self._url("/api/console/chat"),
                headers=self._headers(),
                json=body,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ev in parser.feed(event):
                        await self._queue.put(ev)
                        if isinstance(ev, TurnEnded):
                            ended = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._queue.put(TransportError(str(exc)))
        finally:
            self._stop_poll()
            if not ended:
                await self._queue.put(TurnEnded(stop_reason="closed"))

    # -- approvals (polled) --------------------------------------------------
    def _start_poll(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_approvals())

    def _stop_poll(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

    async def _poll_approvals(self) -> None:
        while True:
            try:
                resp = await self._client.get(
                    self._url("/api/console/push-messages"),
                    params={"session_id": self._session_id},
                    headers=self._headers(),
                )
                if resp.status_code < 300:
                    data = resp.json()
                    for ap in data.get("pending_approvals", []) or []:
                        rid = ap.get("request_id")
                        if rid and rid not in self._pending:
                            self._pending[rid] = ap
                            await self._queue.put(
                                PermissionRequest(
                                    request_id=rid,
                                    title=ap.get("tool_name")
                                    or "tool approval",
                                    tool_kind=ap.get("severity"),
                                    options=[
                                        PermissionOption(
                                            "approve", "Approve", "allow_once"
                                        ),
                                        PermissionOption(
                                            "deny", "Deny", "reject_once"
                                        ),
                                    ],
                                )
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("approval poll error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)

    async def resolve_permission(
        self, request_id: str, option_id: str | None
    ) -> None:
        self._pending.pop(request_id, None)
        approve = option_id == "approve"
        endpoint = "approve" if approve else "deny"
        try:
            await self._client.post(
                self._url(f"/api/approval/{endpoint}"),
                headers=self._headers(),
                json={
                    "request_id": request_id,
                    "session_id": self._session_id,
                    "user_id": self._user,
                    "reason": None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("approval resolve failed: %s", exc)

    # -- control -------------------------------------------------------------
    async def interrupt(self) -> None:
        if self._client is None or not self._chat_id:
            return
        try:
            await self._client.post(
                self._url("/api/console/chat/stop"),
                params={"chat_id": self._chat_id},
                headers=self._headers(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("stop failed: %s", exc)

    async def set_model(self, model_id: str) -> None:
        # The server has no model-switch endpoint exposed here; the agent's
        # `/model` slash command (sent as a normal turn) handles this instead.
        return

    async def events(self) -> AsyncIterator[TuiEvent]:
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_poll()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
        await self._queue.put(_CLOSED)
