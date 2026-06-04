# -*- coding: utf-8 -*-
"""Resolve how to launch the QwenPaw ACP agent paw should drive.

Resolution order (first match wins):

1. ``--agent-cmd "..."`` — an explicit command, used verbatim.
2. Bundled QwenPaw — if ``qwenpaw`` is importable in *this* interpreter
   (``pip install paw[bundled]``), run ``python -m qwenpaw acp``.
3. QwenPaw on PATH — ``qwenpaw acp``.

If none apply, a clear error tells the user how to fix it.
"""

from __future__ import annotations

import importlib.util
import shlex
import shutil
import sys
from dataclasses import dataclass


class AgentResolutionError(RuntimeError):
    """Raised when paw cannot determine how to start a QwenPaw agent."""


@dataclass(frozen=True)
class ResolvedAgent:
    command: list[str]
    description: str  # human-readable, for the status bar / errors


def _bundled_qwenpaw() -> bool:
    return importlib.util.find_spec("qwenpaw") is not None


def resolve_agent_command(
    *,
    agent: str | None = None,
    agent_cmd: str | None = None,
) -> ResolvedAgent:
    """Return the argv (and a label) to spawn the QwenPaw ACP agent."""
    suffix: list[str] = ["--agent", agent] if agent else []

    if agent_cmd:
        return ResolvedAgent(
            command=shlex.split(agent_cmd) + suffix,
            description=f"custom: {agent_cmd}",
        )

    if _bundled_qwenpaw():
        return ResolvedAgent(
            command=[sys.executable, "-m", "qwenpaw", "acp"] + suffix,
            description="bundled qwenpaw",
        )

    found = shutil.which("qwenpaw")
    if found:
        return ResolvedAgent(
            command=[found, "acp"] + suffix,
            description=f"qwenpaw on PATH ({found})",
        )

    raise AgentResolutionError(
        "QwenPaw not found. Either install it alongside paw "
        "(`pip install paw[bundled]` or `pip install qwenpaw`), "
        "or pass `--agent-cmd '<command>'`."
    )
