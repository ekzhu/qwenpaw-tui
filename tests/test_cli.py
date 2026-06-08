# -*- coding: utf-8 -*-
"""CLI smoke tests for the `paw` command."""

from __future__ import annotations

from click.testing import CliRunner

from paw.cli import main


def test_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "terminal chat UI for QwenPaw" in result.output
    assert "--agent-cmd" in result.output


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "paw" in result.output
