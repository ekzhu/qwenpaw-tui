# -*- coding: utf-8 -*-
"""Unit tests for the qwenpaw-app SSE parser.

Event shapes below are taken verbatim from a live `qwenpaw app` stream.
"""

from __future__ import annotations

from paw import events as E
from paw.sse import SseParser


def test_reasoning_then_message_routing():
    p = SseParser()
    # reasoning block opens
    assert p.feed({"object": "message", "id": "m1", "type": "reasoning"}) == []
    # its deltas are thinking
    assert p.feed(
        {
            "object": "content",
            "type": "text",
            "delta": True,
            "msg_id": "m1",
            "text": "thinking",
        }
    ) == [E.ThoughtDelta("thinking")]
    # assistant message block opens
    assert p.feed({"object": "message", "id": "m2", "type": "message"}) == []
    # its deltas are visible text
    assert p.feed(
        {
            "object": "content",
            "type": "text",
            "delta": True,
            "msg_id": "m2",
            "text": "Hello",
        }
    ) == [E.TextDelta("Hello")]


def test_incremental_text_is_not_deduped():
    p = SseParser()
    p.feed({"object": "message", "id": "m", "type": "message"})
    out = []
    for chunk in ("Hello", " there", " friend"):
        out += p.feed(
            {
                "object": "content",
                "type": "text",
                "delta": True,
                "msg_id": "m",
                "text": chunk,
            }
        )
    assert "".join(e.text for e in out) == "Hello there friend"


def test_completed_text_snapshot_is_skipped():
    p = SseParser()
    p.feed({"object": "message", "id": "m", "type": "message"})
    # delta is null/absent on the completed snapshot -> ignored
    assert (
        p.feed(
            {
                "object": "content",
                "type": "text",
                "delta": None,
                "msg_id": "m",
                "text": "Hello there friend",
            }
        )
        == []
    )


def test_unknown_msg_id_defaults_to_text():
    p = SseParser()
    assert p.feed(
        {
            "object": "content",
            "type": "text",
            "delta": True,
            "msg_id": "ghost",
            "text": "hi",
        }
    ) == [E.TextDelta("hi")]


def test_tool_call_then_output():
    p = SseParser()
    # call in progress (no output) -> emitted once
    first = p.feed(
        {
            "object": "content",
            "type": "data",
            "data": {
                "call_id": "c1",
                "name": "browser_use",
                "arguments": "",
            },
        }
    )
    assert first == [
        E.ToolCall(
            tool_call_id="c1", title="browser_use", status="in_progress"
        )
    ]
    # subsequent in-progress args updates are deduped
    assert (
        p.feed(
            {
                "object": "content",
                "type": "data",
                "data": {
                    "call_id": "c1",
                    "name": "browser_use",
                    "arguments": '{"action":"open"}',
                },
            }
        )
        == []
    )
    # output arrives (list of content blocks) -> completed with text
    done = p.feed(
        {
            "object": "content",
            "type": "data",
            "data": {
                "call_id": "c1",
                "name": "browser_use",
                "output": [{"type": "text", "text": "17.2k stars"}],
            },
        }
    )
    assert done == [
        E.ToolCall(
            tool_call_id="c1",
            title="browser_use",
            status="completed",
            output="17.2k stars",
        )
    ]


def test_response_completed_emits_usage_then_turnended():
    p = SseParser()
    out = p.feed(
        {
            "object": "response",
            "status": "completed",
            "usage": {
                "prompt_tokens": 6336,
                "completion_tokens": 46,
                "total_tokens": 6382,
            },
        }
    )
    assert out == [E.Usage(used=6382, size=0), E.TurnEnded("completed")]


def test_response_created_and_in_progress_ignored():
    p = SseParser()
    assert p.feed({"object": "response", "status": "created"}) == []
    assert p.feed({"object": "response", "status": "in_progress"}) == []


def test_error_event():
    p = SseParser()
    assert p.feed({"error": "boom"}) == [E.TransportError("boom")]
