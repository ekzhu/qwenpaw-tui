# -*- coding: utf-8 -*-
"""Pilot-driven smoke tests for the Textual app with a fake transport."""

from __future__ import annotations

import asyncio

import pytest

from paw.app import PawApp
from paw.events import (
    AvailableCommands,
    Connected,
    PermissionOption,
    PermissionRequest,
    SlashCommand,
    TextDelta,
    ThoughtDelta,
    TokenUsage,
    ToolCall,
    TurnEnded,
)
from paw.widgets import (
    AssistantMessage,
    CommandMenu,
    PermissionModal,
    ThoughtMessage,
    ToolPanel,
    UserMessage,
)


class FakeTransport:
    """In-process transport that scripts a canned turn for the UI."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[str] = []
        self.interrupted = False
        self.resolved: list[tuple[str, str | None]] = []
        self.closed = False
        self._permission_mode = "none"

    async def start(self) -> Connected:
        return Connected(
            session_id="sess-abc", agent="default", model="qwen-max"
        )

    async def send(self, text: str) -> None:
        self.sent.append(text)
        if "permission" in text:
            await self._queue.put(
                PermissionRequest(
                    request_id="r1",
                    title="dangerous_tool",
                    options=[
                        PermissionOption("allow", "Allow", "allow_once"),
                        PermissionOption("deny", "Deny", "reject_once"),
                    ],
                )
            )
            return
        for chunk in ("Hello ", "there"):
            await self._queue.put(TextDelta(chunk))
        await self._queue.put(
            ToolCall(
                "t1",
                "read_file",
                kind="read",
                status="completed",
                output="data",
            )
        )
        await self._queue.put(TurnEnded(stop_reason="end_turn"))

    async def interrupt(self) -> None:
        self.interrupted = True
        await self._queue.put(TurnEnded(stop_reason="cancelled"))

    def events(self):
        async def _gen():
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item

        return _gen()

    async def resolve_permission(self, request_id, option_id):
        self.resolved.append((request_id, option_id))
        await self._queue.put(TextDelta(f"[{option_id}]"))
        await self._queue.put(TurnEnded())

    async def set_model(self, model_id):  # pragma: no cover
        pass

    async def close(self):
        self.closed = True
        await self._queue.put(None)


@pytest.mark.asyncio
async def test_basic_turn_renders():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        # status bar picked up the Connected event
        assert "qwen-max" in app.query_one("StatusBar").summary

        prompt = app.query_one("#prompt")
        prompt.value = "hi"
        await pilot.press("enter")
        # let the scripted events drain
        for _ in range(10):
            await pilot.pause()
            if not app._busy:
                break

        assert transport.sent == ["hi"]
        assert any(isinstance(w, UserMessage) for w in app.query(UserMessage))
        assistant = app.query(AssistantMessage).first()
        assert assistant.text == "Hello there"
        tools = list(app.query(ToolPanel))
        assert tools and tools[0]._status == "completed"


@pytest.mark.asyncio
async def test_running_tool_expanded_then_collapsed_when_done():
    """A tool stays open while running, then auto-collapses on completion."""
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()

        await app._dispatch(
            ToolCall(
                "t1",
                "execute_shell_command",
                kind="execute",
                status="in_progress",
                params="command: ls -la",
            )
        )
        await pilot.pause()
        panel = app.query(ToolPanel).first()
        assert panel.collapsed is False  # running → expanded

        await app._dispatch(
            ToolCall(
                "t1",
                "execute_shell_command",
                kind="execute",
                status="completed",
                output="total 0",
            )
        )
        await pilot.pause()
        assert panel.collapsed is True  # done → collapsed, re-openable


@pytest.mark.asyncio
async def test_tool_name_persists_after_completion_update():
    """The agent only sends the name on the start event; the completion
    update (title="") must not overwrite it back to a placeholder."""
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        # start: real name; completion: no title, just status + output
        await app._dispatch(
            ToolCall("t1", "execute_shell_command", status="in_progress")
        )
        await app._dispatch(
            ToolCall("t1", "", status="completed", output="done")
        )
        await pilot.pause()
        panel = app.query(ToolPanel).first()
        assert "execute_shell_command" in panel.title.plain


@pytest.mark.asyncio
async def test_finished_tool_header_is_informative():
    """A completed tool with a generic title still shows kind + params."""
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch(
            ToolCall(
                "t1",
                "tool",
                kind="execute",
                status="completed",
                params="command: ls -la /tmp\nshell: zsh",
                output="x",
            )
        )
        await pilot.pause()
        panel = app.query(ToolPanel).first()
        title = panel.title.plain
        assert "execute" in title  # falls back to kind, not bare "tool"
        assert "ls -la /tmp" in title  # primary param surfaced
        assert "completed" not in title  # redundant status word dropped


@pytest.mark.asyncio
async def test_toggle_hides_finished_tools_only():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch(
            ToolCall("done", "read_file", kind="read", status="completed")
        )
        await app._dispatch(
            ToolCall("live", "grep", kind="search", status="in_progress")
        )
        await pilot.pause()
        done = app._tools["done"]
        live = app._tools["live"]

        await pilot.press("ctrl+t")
        assert done.has_class("hidden")  # finished → hidden
        assert not live.has_class("hidden")  # running → still visible

        await pilot.press("ctrl+t")
        assert not done.has_class("hidden")  # toggled back for inspection


@pytest.mark.asyncio
async def test_thinking_collapsed_by_default():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch(ThoughtDelta("pondering the question"))
        await pilot.pause()
        thought = app.query(ThoughtMessage).first()
        assert thought.collapsed is True


@pytest.mark.asyncio
async def test_permission_modal_resolves():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        prompt.value = "do permission thing"
        await pilot.press("enter")

        # The modal should appear; pick the first (Allow) button.
        for _ in range(10):
            await pilot.pause()
            if isinstance(app.screen, PermissionModal):
                break
        assert isinstance(app.screen, PermissionModal)
        await pilot.press("enter")  # default-focused first button = Allow
        for _ in range(10):
            await pilot.pause()
            if transport.resolved:
                break

        assert transport.resolved == [("r1", "allow")]


@pytest.mark.asyncio
async def test_slash_command_suggestions():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch(
            AvailableCommands(
                commands=[
                    SlashCommand("model", "switch model"),
                    SlashCommand("agent", "change agent"),
                    SlashCommand("clear", "clear session"),
                ]
            )
        )
        menu = app.query_one(CommandMenu)
        prompt = app.query_one("#prompt")

        # Plain text → no dropdown.
        prompt.value = "hi"
        await pilot.pause()
        assert not menu.display

        # "/" opens the dropdown with every command.
        prompt.value = "/"
        await pilot.pause()
        assert menu.display
        assert menu.option_count == 3

        # Inline ghost completion offers the top match.
        assert await app._suggester.get_suggestion("/mod") == "/model"

        # Typing narrows the list.
        prompt.value = "/a"
        await pilot.pause()
        assert menu.display
        assert menu.selected == "agent"

        # Tab accepts the highlighted command (note trailing space) and the
        # menu steps aside instead of submitting.
        await pilot.press("tab")
        assert prompt.value == "/agent "
        assert not menu.display
        assert transport.sent == []


@pytest.mark.asyncio
async def test_text_after_tool_mounts_below_it():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        # text → tool → more text: the transcript should read top-down in
        # that order (the post-tool text must not fold into the first bubble).
        await app._dispatch(TextDelta("Let me check."))
        await app._dispatch(
            ToolCall("t1", "grep", kind="search", status="in_progress")
        )
        await app._dispatch(TextDelta("Found it."))
        await pilot.pause()

        transcript = app.query_one("#transcript")
        kinds = [
            type(w).__name__
            for w in transcript.children
            if isinstance(w, (AssistantMessage, ToolPanel))
        ]
        assert kinds == ["AssistantMessage", "ToolPanel", "AssistantMessage"]

        bubbles = list(app.query(AssistantMessage))
        assert bubbles[0].text == "Let me check."
        assert bubbles[1].text == "Found it."


@pytest.mark.asyncio
async def test_thinking_finalizes_to_thought_for():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch(ThoughtDelta("pondering..."))
        await pilot.pause()
        thought = app.query(ThoughtMessage).first()
        assert "thinking" in str(thought.title)
        assert not thought._finished

        # The visible answer beginning finalizes the thinking lane.
        await app._dispatch(TextDelta("Here is the answer."))
        await pilot.pause()
        assert thought._finished
        assert "thought for" in str(thought.title)


@pytest.mark.asyncio
async def test_live_token_estimate_then_exact():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one("StatusBar")

        # Stream 40 chars of assistant text → ~10 token estimate, marked "~".
        await app._dispatch(TextDelta("x" * 40))
        await pilot.pause()
        assert "↓~10" in bar.summary

        # Exact usage for the call replaces the estimate (no tilde).
        await app._dispatch(
            TokenUsage(input_tokens=1200, output_tokens=7, model="m")
        )
        await pilot.pause()
        assert "↓7" in bar.summary
        assert "↓~" not in bar.summary
        assert "↑1.2k" in bar.summary
        assert app._tok_out == 7 and app._stream_chars == 0


@pytest.mark.asyncio
async def test_interrupt_action():
    transport = FakeTransport()
    app = PawApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        prompt.value = (
            "permission please"  # leaves it busy awaiting permission
        )
        await pilot.press("enter")
        await pilot.pause()
        # dismiss modal if present so escape hits the app
        if isinstance(app.screen, PermissionModal):
            app.screen.dismiss(None)
            await pilot.pause()
        app._busy = True
        await app.action_interrupt()
        assert transport.interrupted
