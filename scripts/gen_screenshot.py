# -*- coding: utf-8 -*-
"""Regenerate the README screenshot (assets/screenshot.svg).

Drives the real Textual app with a staged in-process transport so the
transcript shows a representative turn (welcome logo + a tool call + a
themed answer), then exports the Textual screenshot SVG. Convert to PNG with
``rsvg-convert`` (see ``make`` step in the module docstring at the bottom).

Run:  python scripts/gen_screenshot.py [COLS] [ROWS]
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

# Use a throwaway state dir and the default orange "original" theme so the run
# is deterministic regardless of the developer's saved theme.
os.environ.setdefault("PAW_BACKGROUND_PROMPT", "original")
os.environ["PAW_STATE_DIR"] = tempfile.mkdtemp(prefix="paw-shot-")

from paw.app import PawApp  # noqa: E402
from paw.events import Connected, TextDelta, ToolCall, TurnEnded  # noqa: E402

OUT_SVG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets",
    "screenshot.svg",
)

USER_TEXT = "summarize today's unread newsletters"
ANSWER = (
    "Here's what I found across your **3 unread newsletters**:\n\n"
    "- **The Athletic** — Knicks go up 2-0 on the Suns; Game 3 is tonight "
    "at the Garden\n"
    "- **Nature Briefing** — new drug nearly doubles survival in advanced "
    "pancreatic cancer\n"
    "- **Hyperallergic** — \"Indian Theater\" opens at SITE Santa Fe, 100+ "
    "Native works\n\n"
    "Want the full story on any of these?"
)


class StagedTransport:
    """Returns a fixed Connected and otherwise stays silent; the script drives
    the turn by hand so the framing is fully reproducible."""

    async def start(self) -> Connected:
        return Connected(
            session_id="sess-abc",
            agent="default",
            model="qwen3.7-max",
            qwenpaw_version="1.1.10",
        )

    def events(self):
        async def _gen():
            return
            yield  # pragma: no cover - marks this as an async generator

        return _gen()

    async def send(self, text: str) -> None:  # driven manually instead
        pass

    async def interrupt(self) -> None:
        pass

    async def resolve_permission(self, request_id, option_id) -> None:
        pass

    async def close(self) -> None:
        pass


async def _main(cols: int, rows: int) -> None:
    app = PawApp(StagedTransport())
    async with app.run_test(size=(cols, rows)) as pilot:
        await pilot.pause()
        # Stage one representative turn.
        await app._submit(USER_TEXT)
        await app._dispatch(
            ToolCall(
                "t1",
                "read_inbox",
                kind="read",
                status="completed",
                params="folder: newsletters, unread: true",
                output="3 unread",
            )
        )
        for chunk in ANSWER.split(" "):
            await app._dispatch(TextDelta(chunk + " "))
        await app._dispatch(TurnEnded(stop_reason="end_turn"))
        await pilot.pause()
        await pilot.pause()
        svg = app.export_screenshot(title="QwenPaw-TUI")
    with open(OUT_SVG, "w", encoding="utf-8") as handle:
        handle.write(svg)
    print(f"wrote {OUT_SVG} ({cols}x{rows})")


if __name__ == "__main__":
    cols = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 28
    asyncio.run(_main(cols, rows))
