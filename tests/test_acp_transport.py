# -*- coding: utf-8 -*-
"""End-to-end test of AcpTransport against a fake ACP agent subprocess."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from paw.events import (
    Connected,
    PermissionRequest,
    TextDelta,
    ThoughtDelta,
    ToolCall,
    TransportError,
    TurnEnded,
)
from paw.transport.acp import AcpTransport

FAKE = os.path.join(os.path.dirname(__file__), "_fake_acp_agent.py")


def _transport() -> AcpTransport:
    return AcpTransport(command=[sys.executable, FAKE])


async def _collect_turn(transport: AcpTransport, *, timeout: float = 10.0):
    """Drain events until TurnEnded; return the list."""
    events = []

    async def _run():
        async for ev in transport.events():
            events.append(ev)
            if isinstance(ev, TurnEnded):
                return

    await asyncio.wait_for(_run(), timeout=timeout)
    return events


@pytest.mark.asyncio
async def test_start_and_basic_turn():
    transport = _transport()
    try:
        connected = await asyncio.wait_for(transport.start(), timeout=10.0)
        assert isinstance(connected, Connected)
        assert connected.session_id == "sess-1"

        await transport.send("hi there")
        events = await _collect_turn(transport)
    finally:
        await transport.close()

    assert any(isinstance(e, ThoughtDelta) for e in events)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Hello world"

    # ACP sends a start (carries the title) then an update (carries status/
    # output) sharing one tool_call_id; the UI merges them by id.
    tools = [e for e in events if isinstance(e, ToolCall)]
    assert any(t.title == "read_file" and t.kind == "read" for t in tools)
    assert any(
        t.tool_call_id == "t2"
        and t.status == "completed"
        and t.output == "file contents"
        for t in tools
    )
    assert not any(isinstance(e, TransportError) for e in events)
    assert isinstance(events[-1], TurnEnded)


@pytest.mark.asyncio
async def test_permission_allow():
    transport = _transport()
    try:
        await asyncio.wait_for(transport.start(), timeout=10.0)
        await transport.send("please need-permission now")

        # Drive the turn, answering the permission prompt with "allow".
        events = []

        async def _run():
            async for ev in transport.events():
                events.append(ev)
                if isinstance(ev, PermissionRequest):
                    assert {o.option_id for o in ev.options} == {
                        "allow",
                        "deny",
                    }
                    await transport.resolve_permission(ev.request_id, "allow")
                if isinstance(ev, TurnEnded):
                    return

        await asyncio.wait_for(_run(), timeout=10.0)
    finally:
        await transport.close()

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "[perm:allow]" in text


@pytest.mark.asyncio
async def test_interrupt_cancels_turn():
    transport = _transport()
    try:
        await asyncio.wait_for(transport.start(), timeout=10.0)
        await transport.send("please loop forever")

        events = []
        seen_tool = asyncio.Event()

        async def _run():
            async for ev in transport.events():
                events.append(ev)
                if isinstance(ev, ToolCall) and ev.status == "completed":
                    seen_tool.set()
                if isinstance(ev, TurnEnded):
                    return

        runner = asyncio.create_task(_run())
        await asyncio.wait_for(seen_tool.wait(), timeout=10.0)
        await transport.interrupt()
        await asyncio.wait_for(runner, timeout=10.0)
    finally:
        await transport.close()

    assert isinstance(events[-1], TurnEnded)


def test_session_agent_reads_meta():
    from types import SimpleNamespace

    from paw.transport.acp import _session_agent

    # Agent id reported via the session response _meta.
    sess = SimpleNamespace(field_meta={"qwenpaw.agent": "writer"})
    assert _session_agent(sess) == "writer"

    # Missing / unrelated meta → None (caller falls back to the requested id).
    assert _session_agent(SimpleNamespace(field_meta={"other": 1})) is None
    assert _session_agent(SimpleNamespace(field_meta=None)) is None
