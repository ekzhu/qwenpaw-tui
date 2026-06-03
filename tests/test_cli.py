# -*- coding: utf-8 -*-
"""CLI smoke tests for the `paw` command."""

from __future__ import annotations

import os
import sys

from click.testing import CliRunner

from paw.cli import main

FAKE = os.path.join(os.path.dirname(__file__), "_fake_acp_agent.py")


def test_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "terminal chat UI for QwenPaw" in result.output
    assert "--remote" in result.output
    assert "--agent-cmd" in result.output


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "paw" in result.output


def test_http_unreachable_errors_cleanly():
    # One-shot against an unreachable server should fail fast, not hang.
    result = CliRunner().invoke(
        main, ["--remote", "http://127.0.0.1:1", "-p", "hi"]
    )
    assert result.exit_code != 0


def test_oneshot_against_fake_agent():
    # Drive the real transport against the fake ACP agent via --agent-cmd.
    cmd = f"{sys.executable} {FAKE}"
    result = CliRunner().invoke(main, ["--agent-cmd", cmd, "-p", "hello"])
    assert result.exit_code == 0, result.output
    assert "Hello world" in result.output
