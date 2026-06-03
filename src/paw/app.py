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
    AssistantMessage,
    CommandMenu,
    CommandSuggester,
    ErrorMessage,
    PermissionModal,
    PromptInput,
    PushMessageBox,
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
    /* Tool calls + thinking are Collapsible widgets; flatten the default
       chrome so they sit quietly in the transcript. */
    .tool, .msg.thought {
        height: auto; background: transparent;
        border-top: none; padding: 0; margin: 0 0 1 0;
    }
    .tool {
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
        Binding("escape", "interrupt", "Interrupt", show=True),
        Binding("ctrl+t", "toggle_tools", "Hide/show tools", show=True),
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
        self._tools: dict[str, ToolPanel] = {}
        self._tools_hidden = False
        self._busy = False
        # Running token totals for the session (summed across LLM calls).
        self._tok_in = 0
        self._tok_out = 0
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
                "(/ commands · ⏎ send · esc interrupt · ⌃t tools · ⌃c quit)"
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
            await self._mount(
                ErrorMessage("Still working — press esc to interrupt.")
            )
            return
        await self._mount(UserMessage(text))
        # Reset per-turn lane state so a fresh assistant bubble is created.
        self._assistant = None
        self._thought = None
        self._busy = True
        self._status().set(state="thinking")
        try:
            await self._transport.send(text)
        except Exception as exc:  # noqa: BLE001
            self._busy = False
            self._status().set(state="ready")
            await self._mount(ErrorMessage(str(exc)))

    # -- actions -------------------------------------------------------------
    async def action_interrupt(self) -> None:
        if self._busy:
            await self._transport.interrupt()

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

    async def _dispatch(self, event) -> None:
        if isinstance(event, TextDelta):
            if self._assistant is None:
                self._assistant = AssistantMessage()
                await self._mount(self._assistant)
            await self._assistant.append(event.text)
            self._transcript().scroll_end(animate=False)

        elif isinstance(event, ThoughtDelta):
            if self._thought is None:
                self._thought = ThoughtMessage()
                await self._mount(self._thought)
            self._thought.append(event.text)

        elif isinstance(event, ToolCall):
            panel = self._tools.get(event.tool_call_id)
            if panel is None:
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

        elif isinstance(event, AvailableCommands):
            self._suggester.set_commands(event.commands)
            self._menu.set_commands(event.commands)

        elif isinstance(event, PushMessage):
            await self._mount(PushMessageBox(event.text))

        elif isinstance(event, Usage):
            self._status().set(used=event.used, size=event.size)

        elif isinstance(event, TokenUsage):
            self._tok_in += event.input_tokens
            self._tok_out += event.output_tokens
            self._status().set(tok_in=self._tok_in, tok_out=self._tok_out)

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
            self._assistant = None
            self._thought = None
            self._tools.clear()
            self._status().set(state="ready")

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
