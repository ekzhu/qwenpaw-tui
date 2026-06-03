# -*- coding: utf-8 -*-
"""Parse the ``qwenpaw app`` SSE chat stream into normalized ``TuiEvent``s.

The wire format (verified against a live server) is ``data: {json}\\n\\n`` with
these event shapes:

* ``{"object":"message","id":M,"type":"reasoning"|"message"|"plugin_call"|
  "plugin_call_output",...}`` — declares a message block; ``type`` tells us how
  to route the deltas that follow for that ``id``.
* ``{"object":"content","type":"text","delta":true,"msg_id":M,"text":...}`` —
  an *incremental* text chunk; routed to thinking/assistant by M's kind.
* ``{"object":"content","type":"data","data":{"call_id","name","arguments"|
  "output"}}`` — a tool call (no ``output``) or its result (has ``output``).
* ``{"object":"response","status":"completed","usage":{...}}`` — end of turn.

Stateful (it remembers ``msg_id`` -> kind and which tool calls it has seen), so
one parser is used per turn.
"""

from __future__ import annotations

import json
from typing import Any

from .events import (
    TextDelta,
    ThoughtDelta,
    ToolCall,
    TransportError,
    TuiEvent,
    TurnEnded,
    Usage,
)


def _stringify_output(output: Any) -> str:
    """Flatten a tool ``output`` (str | list of content blocks) to text."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "") or ""))
            else:
                parts.append(str(item))
        joined = "\n".join(p for p in parts if p)
        if joined:
            return joined
    try:
        return json.dumps(output, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(output)


class SseParser:
    """Turns one turn's SSE events into ``TuiEvent``s."""

    def __init__(self) -> None:
        self._kind_by_msg: dict[str, str] = {}  # msg_id -> "thought"|"text"
        self._tool_state: dict[str, str] = (
            {}
        )  # call_id -> "in_progress"|"done"

    def feed(self, event: dict[str, Any]) -> list[TuiEvent]:
        obj = event.get("object")

        if obj == "message":
            mid = event.get("id")
            typ = event.get("type")
            if mid:
                if typ == "reasoning":
                    self._kind_by_msg[mid] = "thought"
                elif typ == "message":
                    self._kind_by_msg[mid] = "text"
            return []

        if obj == "content":
            return self._content(event)

        if obj == "response":
            status = event.get("status")
            if status == "completed":
                out: list[TuiEvent] = []
                usage = event.get("usage") or {}
                total = usage.get("total_tokens")
                if total:
                    out.append(Usage(used=int(total), size=0))
                out.append(TurnEnded(stop_reason="completed"))
                return out
            if status == "failed" or event.get("error"):
                return [TransportError(str(event.get("error") or "failed"))]
            return []

        if event.get("error"):
            return [TransportError(str(event["error"]))]
        return []

    def _content(self, event: dict[str, Any]) -> list[TuiEvent]:
        typ = event.get("type")
        if typ == "text":
            if not event.get("delta"):
                return []  # skip the completed cumulative snapshot
            text = event.get("text") or ""
            if not text:
                return []
            kind = self._kind_by_msg.get(event.get("msg_id"), "text")
            return (
                [ThoughtDelta(text)]
                if kind == "thought"
                else [TextDelta(text)]
            )

        if typ == "data":
            data = event.get("data") or {}
            call_id = data.get("call_id")
            if not call_id:
                return []
            name = data.get("name") or "tool"
            output = data.get("output")
            if output not in (None, ""):
                self._tool_state[call_id] = "done"
                return [
                    ToolCall(
                        tool_call_id=call_id,
                        title=name,
                        status="completed",
                        output=_stringify_output(output),
                    )
                ]
            # A call in progress: emit once when first seen.
            if self._tool_state.get(call_id):
                return []
            self._tool_state[call_id] = "in_progress"
            return [
                ToolCall(
                    tool_call_id=call_id, title=name, status="in_progress"
                )
            ]

        return []
