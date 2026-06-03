# -*- coding: utf-8 -*-
"""The paw terminal chat application (Textual)."""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input

from .events import (
    AvailableCommands,
    Connected,
    PermissionRequest,
    PlanUpdate,
    PushMessage,
    SessionTitle,
    TextDelta,
    ThoughtDelta,
    TokenUsage,
    ToolCall,
    TransportError,
    TurnEnded,
    Usage,
)
from .transport.base import TuiTransport
from .widgets import (
    AgentLabel,
    AssistantMessage,
    CommandMenu,
    CommandSuggester,
    ErrorMessage,
    FileLinkBox,
    PermissionModal,
    PromptInput,
    PushMessageBox,
    QueuedMessage,
    StatusBar,
    ThoughtMessage,
    ToolPanel,
    UserMessage,
)


class PawApp(App):
    """Streaming chat over a :class:`TuiTransport` (ACP by default)."""

    CSS = """
    Screen { background: $background; }
    .statusbar { dock: top; height: 1; background: #1c1c28; color: $text; }
    #transcript { padding: 1 2; }
    .msg { height: auto; margin-bottom: 1; }
    .msg.user { color: $text; }
    /* One agent label per turn, sitting tight above the activity below it. */
    .agentlabel { height: 1; margin: 0 0 0 0; }
    /* Tool calls + thinking are Collapsible widgets; flatten the default
       chrome so they sit quietly in the transcript. */
    .tool, .msg.thought {
        height: auto; background: transparent;
        border-top: none; padding: 0; margin: 0 0 1 0;
    }
    /* Indent thinking + tools the same, with a shared left rule, so the
       agent's "activity" lanes line up consistently under the answer. */
    .tool, .msg.thought {
        margin-left: 2; padding-left: 1;
        border-left: thick #3a3a4a;
    }
    .tool > CollapsibleTitle, .msg.thought > CollapsibleTitle { padding: 0 1; }
    .tool Contents, .msg.thought Contents { padding: 0 1 0 2; }
    .tool.hidden { display: none; }
    #prompt { dock: bottom; border: round #3a3a4a; }
    #prompt:focus { border: round #b48cff; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "Cancel/interrupt", show=True),
        Binding("up", "recall_queued", "Edit queued", show=False),
        # Kept functional for power users but no longer advertised in the
        # input bar — the glyph was unfamiliar to most users.
        Binding("ctrl+t", "toggle_tools", "Hide/show tools", show=False),
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        transport: TuiTransport,
        *,
        agent: str = "default",
        target: str | None = None,
    ) -> None:
        super().__init__()
        self._transport = transport
        self._agent = agent
        self._target = target
        self._assistant: AssistantMessage | None = None
        self._thought: ThoughtMessage | None = None
        # Whether the "qwenpaw" label has been shown for the current turn.
        self._labeled = False
        self._tools: dict[str, ToolPanel] = {}
        self._tools_hidden = False
        self._busy = False
        # Messages typed while the agent is busy wait here (FIFO) and are sent
        # automatically as each turn ends. Each entry pairs the text with its
        # dimmed transcript widget so it can be removed when sent or recalled.
        self._queued: list[tuple[str, QueuedMessage]] = []
        # (tool_call_id, uri) pairs already surfaced as a FileLinkBox, so a
        # repeated tool update doesn't mount the same link twice.
        self._file_links_seen: set[tuple[str, str]] = set()
        # Running token totals for the session (summed across LLM calls).
        # ``_tok_out`` is the confirmed output total; ``_stream_chars`` counts
        # characters streamed since the last confirmed usage, for a live
        # (approximate) output-token estimate while a call is in flight.
        self._tok_in = 0
        self._tok_out = 0
        self._stream_chars = 0
        self._suggester = CommandSuggester()
        self._menu = CommandMenu()

    # -- layout --------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield VerticalScroll(id="transcript")
        yield self._menu
        yield PromptInput(
            self._menu,
            placeholder=(
                "type a message  "
                "(/ commands · ⏎ send/queue · ↑ edit queued · "
                "esc cancel · ⌃c quit)"
            ),
            suggester=self._suggester,
            id="prompt",
        )

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._status().set(agent=self._agent)
        self._consume()

    # -- helpers -------------------------------------------------------------
    def _status(self) -> StatusBar:
        return self.query_one(StatusBar)

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    async def _mount(self, widget) -> None:
        await self._transcript().mount(widget)
        self._transcript().scroll_end(animate=False)

    async def _ensure_turn_label(self) -> None:
        """Mount the ``qwenpaw`` label once per turn, above the first piece
        of assistant activity (thinking, a tool, or the answer)."""
        if not self._labeled:
            self._labeled = True
            await self._mount(AgentLabel())

    def _set_terminal_title(self, title: str) -> None:
        """Set the terminal tab/window title via an OSC escape sequence."""
        driver = getattr(self, "_driver", None)
        if driver is not None:
            try:
                driver.write(f"\x1b]2;{title}\x07")
            except Exception:  # noqa: BLE001 - cosmetic, never fatal
                pass

    # -- input ---------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        self._menu.update_for(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self._menu.display = False
        if self._busy:
            # Queue it; it's delivered automatically when the turn ends. The
            # user can recall it with ↑ to edit before it's picked up.
            widget = QueuedMessage(text)
            await self._mount(widget)
            self._queued.append((text, widget))
            return
        await self._submit(text)

    async def _submit(self, text: str) -> None:
        """Deliver one user turn now."""
        await self._mount(UserMessage(text))
        # Reset per-turn lane state so a fresh assistant bubble (and a single
        # new "qwenpaw" label) is created for the reply.
        self._assistant = None
        self._thought = None
        self._labeled = False
        self._busy = True
        self._status().set(state="thinking")
        try:
            await self._transport.send(text)
        except Exception as exc:  # noqa: BLE001
            self._busy = False
            self._status().set(state="ready")
            await self._mount(ErrorMessage(str(exc)))
            await self._drain_queue()

    async def _drain_queue(self) -> None:
        """Send the next queued message, if the agent is free to take it."""
        if self._busy or not self._queued:
            return
        text, widget = self._queued.pop(0)
        widget.remove()
        await self._submit(text)

    # -- actions -------------------------------------------------------------
    async def action_interrupt(self) -> None:
        if self._busy:
            # Reflect the request immediately; the agent may take a moment to
            # observe the cancel and end the turn (then state → ready).
            self._status().set(state="interrupting")
            await self._transport.interrupt()
            return
        # Idle: esc cancels the current input draft (and any open menu).
        prompt = self.query_one("#prompt", Input)
        if prompt.value:
            prompt.value = ""
            self._menu.display = False

    async def action_recall_queued(self) -> None:
        """Pull the most recently queued message back into the input to edit.

        Only when the menu is closed and the input is empty, so it never
        clobbers text the user is mid-way through typing.
        """
        if self._menu.display or not self._queued:
            return
        prompt = self.query_one("#prompt", Input)
        if prompt.value:
            return
        text, widget = self._queued.pop()
        widget.remove()
        prompt.value = text
        prompt.cursor_position = len(text)

    def action_toggle_tools(self) -> None:
        """Hide (or reveal) every finished tool panel for a clean transcript.

        Running tools stay visible; completed/failed ones are display:none'd
        but remain mounted so toggling back restores them for inspection.
        """
        self._tools_hidden = not self._tools_hidden
        hidden = 0
        for panel in self.query(ToolPanel):
            if panel.is_done:
                panel.set_class(self._tools_hidden, "hidden")
                hidden += 1
        verb = "Hid" if self._tools_hidden else "Showing"
        self.notify(f"{verb} {hidden} finished tool(s)", timeout=2)

    async def action_quit(self) -> None:
        try:
            await self._transport.close()
        finally:
            self.exit()

    # -- event pump ----------------------------------------------------------
    @work(exclusive=True)
    async def _consume(self) -> None:
        try:
            connected = await self._transport.start()
            self._on_connected(connected)
            async for event in self._transport.events():
                await self._dispatch(event)
        except Exception as exc:  # noqa: BLE001
            self._status().set(state="error")
            await self._mount(ErrorMessage(f"transport: {exc}"))

    def _on_connected(self, ev: Connected) -> None:
        self._status().set(
            session=ev.session_id,
            agent=ev.agent or self._agent,
            model=ev.model or "—",
            state="ready",
        )
        # Start with the session id; replaced by the real title once the
        # agent reports one (see SessionTitle).
        self._set_terminal_title(f"QwenPaw {str(ev.session_id)[:8]}")

    # Rough bytes-per-token for the live output estimate (~4 chars/token for
    # typical text; intentionally crude — it's marked approximate and is
    # replaced by the exact count when the call's usage arrives).
    _CHARS_PER_TOKEN = 4

    def _refresh_tokens(self) -> None:
        """Push token totals to the status bar, including the in-flight
        estimate for output tokens still streaming."""
        est = self._stream_chars // self._CHARS_PER_TOKEN
        self._status().set(
            tok_in=self._tok_in,
            tok_out=self._tok_out + est,
            tok_out_approx=est > 0,
        )

    async def _dispatch(self, event) -> None:
        if isinstance(event, TextDelta):
            await self._ensure_turn_label()
            # The visible answer beginning means thinking is done; drop the
            # lane so reasoning that resumes later starts a fresh block.
            if self._thought is not None:
                self._thought.done()
                self._thought = None
            if self._assistant is None:
                self._assistant = AssistantMessage()
                await self._mount(self._assistant)
            await self._assistant.append(event.text)
            self._transcript().scroll_end(animate=False)
            self._stream_chars += len(event.text)
            self._refresh_tokens()

        elif isinstance(event, ThoughtDelta):
            await self._ensure_turn_label()
            # A new thinking block: any answer text after it should mount
            # below, so close the current assistant bubble.
            self._assistant = None
            if self._thought is None:
                self._thought = ThoughtMessage(live=True)
                await self._mount(self._thought)
            self._thought.append(event.text)
            # Reasoning counts toward output tokens too.
            self._stream_chars += len(event.text)
            self._refresh_tokens()

        elif isinstance(event, ToolCall):
            panel = self._tools.get(event.tool_call_id)
            if panel is None:
                await self._ensure_turn_label()
                # A new tool ends the current thinking block and closes the
                # assistant bubble, so transcript widgets stay in the order
                # content was produced (text → tool → text reads top-down).
                if self._thought is not None:
                    self._thought.done()
                    self._thought = None
                self._assistant = None
                panel = ToolPanel(
                    event.tool_call_id,
                    event.title,
                    event.kind,
                    params=event.params,
                )
                self._tools[event.tool_call_id] = panel
                await self._mount(panel)
            panel.update_call(
                title=event.title,
                kind=event.kind,
                status=event.status,
                output=event.output,
                params=event.params,
            )
            # Respect an active "hide finished tools" toggle for tools that
            # complete after it was switched on.
            if self._tools_hidden and panel.is_done:
                panel.add_class("hidden")
            # Surface any files the tool returned (e.g. send_file_to_user) as
            # their own clickable transcript line, since the panel collapses.
            for link in event.links:
                key = (event.tool_call_id, link.uri)
                if key in self._file_links_seen:
                    continue
                self._file_links_seen.add(key)
                await self._mount(FileLinkBox(link.name, link.uri))

        elif isinstance(event, SessionTitle):
            self._set_terminal_title(f"QwenPaw {event.title}")

        elif isinstance(event, AvailableCommands):
            self._suggester.set_commands(event.commands)
            self._menu.set_commands(event.commands)

        elif isinstance(event, PushMessage):
            await self._mount(PushMessageBox(event.text))

        elif isinstance(event, Usage):
            self._status().set(used=event.used, size=event.size)

        elif isinstance(event, TokenUsage):
            # Exact usage for the just-finished call replaces our estimate.
            self._tok_in += event.input_tokens
            self._tok_out += event.output_tokens
            self._stream_chars = 0
            if event.model:
                self._status().set(model=event.model)
            self._refresh_tokens()

        elif isinstance(event, PlanUpdate):
            # Render the plan inline as a thought-style summary for now.
            lines = "\n".join(
                f"  {'✓' if e.status == 'completed' else '•'} {e.content}"
                for e in event.entries
            )
            if lines:
                box = ThoughtMessage(title="📋 plan", collapsed=False)
                box.append(lines)
                await self._mount(box)

        elif isinstance(event, PermissionRequest):
            self._on_permission(event)

        elif isinstance(event, TransportError):
            await self._mount(ErrorMessage(event.message))

        elif isinstance(event, TurnEnded):
            self._busy = False
            if self._thought is not None:
                self._thought.done()
            self._assistant = None
            self._thought = None
            self._labeled = False
            self._tools.clear()
            # Drop any leftover estimate (e.g. a turn with no usage report).
            self._stream_chars = 0
            self._refresh_tokens()
            self._status().set(state="ready")
            # Hand off to the next message the user queued while we worked.
            await self._drain_queue()

    def _on_permission(self, event: PermissionRequest) -> None:
        def _resolve(option_id: str | None) -> None:
            self.run_worker(
                self._transport.resolve_permission(
                    event.request_id, option_id
                ),
                exclusive=False,
            )

        self.push_screen(PermissionModal(event), _resolve)

    async def on_unmount(self) -> None:
        await self._transport.close()
