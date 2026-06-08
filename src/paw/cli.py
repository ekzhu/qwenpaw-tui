# -*- coding: utf-8 -*-
"""The ``paw`` command.

``paw``                       open an interactive chat with a QwenPaw agent
``paw --agent-cmd "..."``     drive an explicit ACP agent command

For non-interactive / one-shot use, use QwenPaw directly (``qwenpaw chat``).
Textual is imported lazily so ``paw --help`` stays snappy.
"""

from __future__ import annotations

import click

from .__version__ import __version__
from .resolve import AgentResolutionError, resolve_agent_command


def _build_transport(
    *,
    agent: str | None,
    agent_cmd: str | None,
):
    """Return ``(transport, description)`` for the requested target."""
    # An ACP/stdio agent: explicit --agent-cmd, or `qwenpaw acp` on PATH.
    try:
        resolved = resolve_agent_command(agent=agent, agent_cmd=agent_cmd)
    except AgentResolutionError as exc:
        raise click.ClickException(str(exc)) from exc

    from .transport.acp import AcpTransport

    return (
        AcpTransport(agent=agent, command=resolved.command),
        resolved.description,
    )


@click.command("paw", context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--agent", default=None, help="Agent ID to chat with.")
@click.option(
    "--agent-cmd",
    default=None,
    metavar="COMMAND",
    help="Explicit command that speaks ACP over stdio "
    "(e.g. 'qwenpaw acp'). Overrides discovery.",
)
@click.version_option(version=__version__, prog_name="paw")
def main(
    agent: str | None,
    agent_cmd: str | None,
) -> None:
    """paw — a terminal chat UI for QwenPaw."""
    transport, description = _build_transport(agent=agent, agent_cmd=agent_cmd)

    from .app import PawApp

    PawApp(transport, agent=agent or "default", target=description).run()


if __name__ == "__main__":
    main()
