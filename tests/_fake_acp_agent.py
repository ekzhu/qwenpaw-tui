# -*- coding: utf-8 -*-
"""A minimal ACP agent used to exercise the TUI's AcpTransport in tests.

Run as ``python _fake_acp_agent.py`` (stdio). It speaks just enough ACP to:
* answer ``initialize`` / ``new_session``
* stream a thought, two text deltas and a completed tool call on ``prompt``
* request permission when the prompt text contains ``need-permission``
* honour ``cancel``

This lets the transport be tested end-to-end without the heavy QwenPaw
backend (agentscope, etc.).
"""

from __future__ import annotations

import asyncio

from acp import (
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PROTOCOL_VERSION,
    PromptResponse,
    run_agent,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_agent_thought,
    update_tool_call,
)
from acp.schema import (
    AgentCapabilities,
    Implementation,
    PermissionOption,
    ToolCallUpdate,
)


class FakeAgent(Agent):
    def __init__(self) -> None:
        self._conn = None
        self._cancel: dict[str, asyncio.Event] = {}

    def on_connect(self, conn) -> None:  # noqa: ANN001
        self._conn = conn

    async def initialize(
        self,
        protocol_version,
        client_capabilities=None,
        client_info=None,
        **kw,
    ):  # noqa: ANN001
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(),
            agent_info=Implementation(name="fake-agent", version="0.0.1"),
        )

    async def new_session(
        self, cwd, additional_directories=None, mcp_servers=None, **kw
    ):  # noqa: ANN001
        return NewSessionResponse(session_id="sess-1")

    async def cancel(self, session_id, **kw):  # noqa: ANN001
        ev = self._cancel.get(session_id)
        if ev:
            ev.set()

    async def prompt(
        self, prompt, session_id, message_id=None, **kw
    ):  # noqa: ANN001
        text = ""
        for block in prompt:
            text += getattr(block, "text", "") or ""

        cancel = asyncio.Event()
        self._cancel[session_id] = cancel

        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_thought(text_block("thinking...")),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block("Hello ")),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block("world")),
        )

        if "need-permission" in text:
            outcome = await self._conn.request_permission(
                options=[
                    PermissionOption(
                        option_id="allow", name="Allow", kind="allow_once"
                    ),
                    PermissionOption(
                        option_id="deny", name="Deny", kind="reject_once"
                    ),
                ],
                session_id=session_id,
                tool_call=ToolCallUpdate(
                    tool_call_id="t1", title="dangerous_tool"
                ),
            )
            chosen = getattr(
                getattr(outcome, "outcome", None), "option_id", "cancelled"
            )
            await self._conn.session_update(
                session_id=session_id,
                update=update_agent_message(text_block(f" [perm:{chosen}]")),
            )

        # A tool call: start then complete.
        await self._conn.session_update(
            session_id=session_id,
            update=start_tool_call(
                "t2", "read_file", kind="read", status="in_progress"
            ),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=update_tool_call(
                "t2",
                status="completed",
                content=[tool_content(text_block("file contents"))],
            ),
        )

        if "loop" in text:
            # Stay busy so the test can exercise cancel().
            try:
                await asyncio.wait_for(cancel.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass

        self._cancel.pop(session_id, None)
        return PromptResponse(stop_reason="end_turn")

    async def close_session(self, session_id, **kw):  # noqa: ANN001
        return None


if __name__ == "__main__":
    asyncio.run(run_agent(FakeAgent()))
