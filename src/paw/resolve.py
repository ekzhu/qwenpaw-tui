# -*- coding: utf-8 -*-
"""Resolve how to launch the QwenPaw ACP agent paw should drive.

paw is only a UI for an *existing* QwenPaw installation, so resolution is
simple (first match wins):

1. ``--agent-cmd "..."`` — an explicit command, used verbatim.
2. QwenPaw on PATH — ``qwenpaw acp``.

If neither applies, a clear error tells the user to install QwenPaw first.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass


class AgentResolutionError(RuntimeError):
    """Raised when paw cannot determine how to start a QwenPaw agent."""


@dataclass(frozen=True)
class ResolvedAgent:
    command: list[str]
    description: str  # human-readable, for the status bar / errors


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

    found = shutil.which("qwenpaw")
    if found:
        return ResolvedAgent(
            command=[found, "acp"] + suffix,
            description=f"qwenpaw on PATH ({found})",
        )

    raise AgentResolutionError(
        "QwenPaw not found on PATH. Install QwenPaw first "
        "(see https://github.com/agentscope-ai/QwenPaw), then run paw — "
        "or pass `--agent-cmd '<command>'` to point at it explicitly."
    )
