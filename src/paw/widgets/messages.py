# -*- coding: utf-8 -*-
"""Transcript message widgets (user / assistant / thinking / errors)."""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static

from ._anim import TICK, pulse, spinner


class UserMessage(Static):
    """A user turn, shown with a prompt glyph."""

    def __init__(self, text: str) -> None:
        body = Text()
        body.append("❯ ", style="bold #6db8ff")
        body.append(text)
        super().__init__(body, classes="msg user")


class AgentLabel(Static):
    """The ``qwenpaw`` lane label, shown once at the start of a turn.

    Kept separate from :class:`AssistantMessage` so a turn that interleaves
    thinking, tools and several answer chunks shows a single label above the
    whole group rather than one per bubble.
    """

    def __init__(self) -> None:
        super().__init__(
            Text("qwenpaw", style="bold #b48cff"), classes="agentlabel"
        )


class AssistantMessage(Widget):
    """Streaming assistant answer rendered as markdown.

    ``append()`` accumulates deltas and re-renders. The lane label is mounted
    separately (see :class:`AgentLabel`) so post-tool answer chunks flow
    under the same label instead of looking like new messages.
    """

    DEFAULT_CSS = """
    AssistantMessage { height: auto; }
    AssistantMessage > Markdown { height: auto; margin: 0; }
    """

    def __init__(self) -> None:
        super().__init__(classes="msg assistant")
        self._text = ""
        self._md = Markdown("")

    def compose(self) -> ComposeResult:
        yield self._md

    async def append(self, delta: str) -> None:
        self._text += delta
        await self._md.update(self._text)

    @property
    def text(self) -> str:
        return self._text


class ThoughtMessage(Collapsible):
    """Dimmed agent thinking lane, collapsed by default.

    The reasoning streams into the (hidden) body; the user can expand the
    header to read it. Reused for plan summaries via the ``title`` argument.

    When ``live=True`` the header animates (spinner + pulsing colour) and
    shows the elapsed time while the agent thinks; calling :meth:`done`
    freezes it to ``💭 thought for Ns``.
    """

    def __init__(
        self,
        title: str = "💭 thinking",
        collapsed: bool = True,
        *,
        live: bool = False,
    ) -> None:
        self._text = ""
        self._live = live
        self._start: float | None = None
        self._timer = None
        self._frame = 0
        self._finished = False
        self._body = Static(Text("", style="italic #8a8a8a"))
        super().__init__(
            self._body,
            title=title,
            collapsed=collapsed,
            classes="msg thought",
        )

    def on_mount(self) -> None:
        if self._live:
            self._start = time.monotonic()
            self._timer = self.set_interval(TICK, self._tick)
            self._tick()

    def _elapsed(self) -> int:
        if self._start is None:
            return 0
        return int(time.monotonic() - self._start)

    def _tick(self) -> None:
        if self._finished:
            return
        self._frame += 1
        head = Text()
        head.append("💭 ", style="")
        head.append(f"thinking {self._elapsed()}s ", style="italic #8a8a8a")
        head.append(spinner(self._frame), style=f"bold {pulse(self._frame)}")
        self.title = head

    def done(self) -> None:
        """Freeze the header to the final elapsed time."""
        if self._finished or not self._live:
            return
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
        self.title = f"💭 thought for {self._elapsed()}s"

    def append(self, delta: str) -> None:
        self._text += delta
        self._body.update(Text(self._text, style="italic #8a8a8a"))


class PushMessageBox(Static):
    """A server-initiated proactive message."""

    def __init__(self, text: str) -> None:
        body = Text()
        body.append("✦ ", style="bold #ffcf6d")
        body.append(text, style="#ffcf6d")
        super().__init__(body, classes="msg push")


class ErrorMessage(Static):
    """A transport/agent error."""

    def __init__(self, text: str) -> None:
        body = Text()
        body.append("⚠ ", style="bold #ff6d6d")
        body.append(text, style="#ff9d9d")
        super().__init__(body, classes="msg error")
