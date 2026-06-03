# -*- coding: utf-8 -*-
"""Top status bar: agent, model, session, token usage, busy state."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

# Public field name -> internal attribute. Internal names are namespaced with
# ``_sb_`` so they never clash with Textual ``Widget`` internals (``_size``,
# ``size``, ``region`` ...).
_FIELDS = (
    "agent",
    "model",
    "session",
    "used",
    "size",
    "tok_in",
    "tok_out",
    "state",
)


class StatusBar(Static):
    """A one-line header rendered from a few fields via :meth:`set`."""

    def __init__(self) -> None:
        self._sb_agent = "default"
        self._sb_model = "—"
        self._sb_session = "—"
        self._sb_used = 0
        self._sb_size = 0
        self._sb_tok_in = 0
        self._sb_tok_out = 0
        self._sb_state = "connecting"
        # Pass an initial renderable so the first arrange has a valid visual.
        super().__init__(self._compose_line(), classes="statusbar")

    def set(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            if key in _FIELDS and value is not None:
                setattr(self, f"_sb_{key}", value)
        self.update(self._compose_line())

    @property
    def summary(self) -> str:
        """Plain-text view of the bar (handy for tests)."""
        return self._compose_line().plain

    def _compose_line(self) -> Text:
        state_color = {
            "connecting": "#ffcf6d",
            "ready": "#6dff9d",
            "thinking": "#6db8ff",
            "error": "#ff6d6d",
        }.get(self._sb_state, "#8a8a8a")

        line = Text()
        line.append(" paw ", style="bold on #2a2a3a")
        line.append("  agent:", style="#8a8a8a")
        line.append(f"{self._sb_agent}", style="bold")
        line.append("  ", style="")
        line.append(f"{self._sb_model}", style="#b48cff")
        line.append("  session:", style="#8a8a8a")
        line.append(f"{str(self._sb_session)[:8]}", style="")
        if self._sb_used:
            tokens = f"{self._sb_used:,}"
            if self._sb_size:
                tokens += f"/{self._sb_size:,}"
            line.append(f"  tokens:{tokens}", style="#8a8a8a")
        if self._sb_tok_in or self._sb_tok_out:
            line.append("  tok ", style="#8a8a8a")
            line.append(f"↑{self._sb_tok_in:,}", style="#7fb7d9")
            line.append(f" ↓{self._sb_tok_out:,}", style="#6dff9d")
        line.append("   ", style="")
        line.append(f"⏺ {self._sb_state}", style=f"bold {state_color}")
        return line
