# -*- coding: utf-8 -*-
"""Live ACP integration against a local QwenPaw development checkout."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from paw.events import (
    Connected,
    PermissionRequest,
    TextDelta,
    ToolCall,
    TransportError,
    TurnEnded,
)
from paw.transport.acp import AcpTransport

DEV_QWENPAW = Path(
    os.environ.get(
        "QWENPAW_DEV_CHECKOUT",
        "/Users/erkang.zhu/code/ContinueLearningBench/submodules/QwenPaw",
    )
)


@dataclass(frozen=True)
class ProviderCase:
    provider_id: str
    env_keys: tuple[str, ...]
    model: str

    @property
    def model_spec(self) -> str:
        return f"{self.provider_id}:{self.model}"


PROVIDERS: tuple[ProviderCase, ...] = (
    ProviderCase(
        "dashscope",
        ("DASHSCOPE_API_KEY",),
        os.environ.get("PAW_E2E_DASHSCOPE_MODEL", "qwen3-max"),
    ),
    ProviderCase(
        "openai",
        ("OPENAI_API_KEY",),
        os.environ.get("PAW_E2E_OPENAI_MODEL", "gpt-5.2"),
    ),
    ProviderCase(
        "anthropic",
        ("ANTHROPIC_API_KEY",),
        os.environ.get("PAW_E2E_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    ),
    ProviderCase(
        "gemini",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        os.environ.get("PAW_E2E_GEMINI_MODEL", "gemini-2.5-flash"),
    ),
    ProviderCase(
        "deepseek",
        ("DEEPSEEK_API_KEY",),
        os.environ.get("PAW_E2E_DEEPSEEK_MODEL", "deepseek-chat"),
    ),
)
DASHSCOPE = PROVIDERS[0]


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to run the QwenPaw dev integration test")
    return uv


def _dev_command() -> list[str]:
    return [
        _uv(),
        "run",
        "--project",
        str(DEV_QWENPAW),
        "python",
        "-m",
        "qwenpaw",
        "acp",
    ]


def _run_dev_python(script: str, *, env: dict[str, str]) -> str:
    result = subprocess.run(
        [
            _uv(),
            "run",
            "--project",
            str(DEV_QWENPAW),
            "python",
            "-c",
            script,
        ],
        cwd=str(DEV_QWENPAW),
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            "QwenPaw dev environment is not runnable: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


async def _collect_turn(
    transport: AcpTransport,
    *,
    timeout: float = 180.0,
) -> list[object]:
    events: list[object] = []

    async def _run() -> None:
        async for event in transport.events():
            events.append(event)
            if isinstance(event, PermissionRequest):
                option_id = _allow_option_id(event)
                await transport.resolve_permission(event.request_id, option_id)
            if isinstance(event, TurnEnded):
                return

    await asyncio.wait_for(_run(), timeout=timeout)
    return events


def _allow_option_id(event: PermissionRequest) -> str | None:
    for option in event.options:
        haystack = f"{option.option_id} {option.name} {option.kind}".casefold()
        if "allow" in haystack:
            return option.option_id
    return event.options[0].option_id if event.options else None


def _turn_text(events: list[object]) -> str:
    errors = [event for event in events if isinstance(event, TransportError)]
    assert not errors, "; ".join(error.message for error in errors)
    return "".join(
        event.text for event in events if isinstance(event, TextDelta)
    )


def _provider_key(case: ProviderCase) -> str:
    for env_key in case.env_keys:
        value = os.environ.get(env_key, "").strip()
        if value:
            return value
    pytest.skip(
        f"{' or '.join(case.env_keys)} is required for {case.provider_id} E2E"
    )


@pytest.fixture()
def isolated_qwenpaw_env(tmp_path, monkeypatch) -> dict[str, str]:
    if not (DEV_QWENPAW / "src" / "qwenpaw").is_dir():
        pytest.skip(f"QwenPaw dev checkout not found: {DEV_QWENPAW}")

    (tmp_path / "qwenpaw-work").mkdir()
    (tmp_path / "qwenpaw-secret").mkdir()
    (tmp_path / "paw-state").mkdir()
    monkeypatch.setenv(
        "QWENPAW_WORKING_DIR",
        str(tmp_path / "qwenpaw-work"),
    )
    monkeypatch.setenv(
        "QWENPAW_SECRET_DIR",
        str(tmp_path / "qwenpaw-secret"),
    )
    monkeypatch.setenv("PAW_STATE_DIR", str(tmp_path / "paw-state"))

    return os.environ.copy()


def _assert_uses_dev_checkout(env: dict[str, str]) -> None:
    source_path = _run_dev_python(
        "import qwenpaw; print(qwenpaw.__file__)",
        env=env,
    )
    assert str(DEV_QWENPAW / "src" / "qwenpaw") in source_path


def _seed_provider(env: dict[str, str], case: ProviderCase) -> None:
    env = {**env, "PAW_E2E_PROVIDER_KEY": _provider_key(case)}
    _run_dev_python(
        f"""
import asyncio
import os
from qwenpaw.constant import SECRET_DIR, WORKING_DIR
from qwenpaw.providers.provider import ModelInfo
from qwenpaw.providers.provider_manager import ProviderManager

assert str(WORKING_DIR) == os.environ["QWENPAW_WORKING_DIR"]
assert str(SECRET_DIR) == os.environ["QWENPAW_SECRET_DIR"]

manager = ProviderManager.get_instance()
provider = manager.get_provider("{case.provider_id}")
assert provider is not None, "provider not found"

config = {{"api_key": os.environ["PAW_E2E_PROVIDER_KEY"]}}
if not provider.has_model("{case.model}"):
    extra = list(provider.extra_models)
    extra.append(ModelInfo(id="{case.model}", name="{case.model}"))
    config["extra_models"] = [model.model_dump() for model in extra]

assert manager.update_provider("{case.provider_id}", config)
asyncio.run(manager.activate_model("{case.provider_id}", "{case.model}"))
print(str(WORKING_DIR) + "|" + str(SECRET_DIR))
        """,
        env=env,
    )


async def _started_transport(
    env: dict[str, str], case: ProviderCase
) -> AcpTransport:
    transport = AcpTransport(
        command=_dev_command(),
        cwd=env["QWENPAW_WORKING_DIR"],
    )
    connected = await asyncio.wait_for(transport.start(), timeout=120)
    assert isinstance(connected, Connected)
    assert connected.session_id
    # The model is already activated in QwenPaw's config by _seed_provider;
    # paw no longer switches models over ACP.
    return transport


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qwenpaw_dev_acp_dashscope_two_turns_with_tool_usage(
    isolated_qwenpaw_env,
):
    env = isolated_qwenpaw_env
    _assert_uses_dev_checkout(env)
    _seed_provider(env, DASHSCOPE)

    sentinel = "PAW_TOOL_SENTINEL_8472"
    fixture_path = Path(env["QWENPAW_WORKING_DIR"]) / "tool-fixture.txt"
    fixture_path.write_text(
        f"QwenPaw live tool fixture: {sentinel}\n", encoding="utf-8"
    )

    transport = await _started_transport(env, DASHSCOPE)
    try:
        await transport.send(
            "You must call the read_file tool to read this exact file path: "
            f"{fixture_path}. Do not guess from the prompt. After reading, "
            f"reply exactly TOOL_OK if the file contains {sentinel}."
        )
        first_events = await _collect_turn(transport)
        first_text = _turn_text(first_events)
        assert "tool_ok" in first_text.casefold()
        assert any(isinstance(event, ToolCall) for event in first_events)

        second_events = await _collect_turn_after_send(
            transport,
            "Using the file you read in the previous turn, reply exactly "
            "MEMORY_OK if it contained the sentinel.",
        )
        second_text = _turn_text(second_events)
        assert "memory_ok" in second_text.casefold()
    finally:
        await transport.close()


async def _collect_turn_after_send(
    transport: AcpTransport, prompt: str
) -> list[object]:
    await transport.send(prompt)
    return await _collect_turn(transport)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qwenpaw_dev_acp_dashscope_tool_call_event(
    isolated_qwenpaw_env,
):
    env = isolated_qwenpaw_env
    _assert_uses_dev_checkout(env)
    _seed_provider(env, DASHSCOPE)

    sentinel = "PAW_READ_FILE_EVENT_3921"
    fixture_path = Path(env["QWENPAW_WORKING_DIR"]) / "tool-event.txt"
    fixture_path.write_text(f"{sentinel}\n", encoding="utf-8")

    transport = await _started_transport(env, DASHSCOPE)
    try:
        events = await _collect_turn_after_send(
            transport,
            "Call read_file on this exact file path, then reply exactly "
            f"READ_EVENT_OK if the content includes {sentinel}: "
            f"{fixture_path}",
        )
        text = _turn_text(events)
        assert "read_event_ok" in text.casefold()
        tools = [event for event in events if isinstance(event, ToolCall)]
        assert tools, "expected at least one tool call event"
        assert any(
            "read" in (tool.title or "").casefold()
            or "read" in (tool.kind or "").casefold()
            for tool in tools
        )
    finally:
        await transport.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_case",
    PROVIDERS,
    ids=[case.provider_id for case in PROVIDERS],
)
async def test_qwenpaw_dev_acp_provider_two_turn_smoke(
    isolated_qwenpaw_env,
    provider_case: ProviderCase,
):
    env = isolated_qwenpaw_env
    _assert_uses_dev_checkout(env)
    _seed_provider(env, provider_case)

    transport = await _started_transport(env, provider_case)
    try:
        first_events = await _collect_turn_after_send(
            transport,
            "Reply exactly PROVIDER_TURN_ONE_OK.",
        )
        assert "provider_turn_one_ok" in _turn_text(first_events).casefold()

        second_events = await _collect_turn_after_send(
            transport,
            "This is the second turn. Reply exactly PROVIDER_TURN_TWO_OK.",
        )
        assert "provider_turn_two_ok" in _turn_text(second_events).casefold()
    finally:
        await transport.close()
