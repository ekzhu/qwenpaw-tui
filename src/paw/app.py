# -*- coding: utf-8 -*-
"""The paw terminal chat application (Textual)."""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input

from .events import (
    Connected,
    PermissionRequest,
    PlanUpdate,
    PushMessage,
    TextDelta,
    ThoughtDelta,
    ToolCall,
    TransportError,
    TurnEnded,
    Usage,
)
from .transport.base import TuiTransport
from .widgets import (
    AssistantMessage,
    ErrorMessage,
    PermissionModal,
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
    .tool {
        height: auto; margin: 0 0 1 2; padding: 0 1;
        border-left: thick #3a3a4a;
    }
    #prompt { dock: bottom; border: round #3a3a4a; }
    #prompt:focus { border: round #b48cff; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt", show=True),
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
        self._busy = False

    # -- layout --------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield VerticalScroll(id="transcript")
        yield Input(
            placeholder=(
                "type a message  "
                "(/ commands · ⏎ send · esc interrupt · ⌃c quit)"
            ),
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
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
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
                panel = ToolPanel(event.tool_call_id, event.title, event.kind)
                self._tools[event.tool_call_id] = panel
                await self._mount(panel)
            panel.update_call(
                title=event.title,
                kind=event.kind,
                status=event.status,
                output=event.output,
            )

        elif isinstance(event, PushMessage):
            await self._mount(PushMessageBox(event.text))

        elif isinstance(event, Usage):
            self._status().set(used=event.used, size=event.size)

        elif isinstance(event, PlanUpdate):
            # Render the plan inline as a thought-style summary for now.
            lines = "\n".join(
                f"  {'✓' if e.status == 'completed' else '•'} {e.content}"
                for e in event.entries
            )
            if lines:
                box = ThoughtMessage()
                box.append("plan:\n" + lines)
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
