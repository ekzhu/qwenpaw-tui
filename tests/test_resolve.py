# -*- coding: utf-8 -*-
"""Unit tests for agent-command resolution."""

from __future__ import annotations

import pytest

from paw import resolve
from paw.resolve import AgentResolutionError, resolve_agent_command


def test_explicit_agent_cmd_is_split():
    r = resolve_agent_command(agent_cmd="qwenpaw acp")
    assert r.command == ["qwenpaw", "acp"]


def test_agent_id_is_appended():
    r = resolve_agent_command(agent_cmd="qwenpaw acp", agent="writer")
    assert r.command == ["qwenpaw", "acp", "--agent", "writer"]


def test_path_resolution(monkeypatch):
    monkeypatch.setattr(resolve.shutil, "which", lambda _: "/usr/bin/qwenpaw")
    r = resolve_agent_command()
    assert r.command == ["/usr/bin/qwenpaw", "acp"]


def test_not_found_raises(monkeypatch):
    monkeypatch.setattr(resolve.shutil, "which", lambda _: None)
    with pytest.raises(AgentResolutionError):
        resolve_agent_command()
