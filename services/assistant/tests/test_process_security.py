from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.powershell import PowerShellRunner
from jarvis_assistant.process_io import (
    BoundedProcessOutput,
    decode_redacted_child_output,
    launch_associated_target,
    sanitized_child_environment,
)
from jarvis_assistant.providers.piper import PiperTextToSpeechProvider
from jarvis_assistant.tools.system import _run_process

SECRET_ENVIRONMENT = {
    "GEMINI_API_KEY": "gemini-test-secret-123456",
    "DEEPGRAM_API_KEY": "deepgram-test-secret-123456",
    "ASSISTANT_SESSION_TOKEN": "session-test-secret-123456",
}


def test_child_environment_is_minimal_and_strips_secret_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UNRELATED_PARENT_SETTING", "must-not-be-inherited")
    monkeypatch.setenv("TEMP", r"C:\Temp")

    environment = sanitized_child_environment({"JARVIS_PATH": r"C:\Allowed"})

    assert environment["TEMP"] == r"C:\Temp"
    assert environment["JARVIS_PATH"] == r"C:\Allowed"
    assert "UNRELATED_PARENT_SETTING" not in environment
    assert all(name not in environment for name in SECRET_ENVIRONMENT)
    with pytest.raises(ValueError, match="unsafe"):
        sanitized_child_environment({"OPERATION_API_KEY": "not-allowed"})


async def test_generic_child_cannot_read_parent_secrets_and_output_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TEMP", r"C:\RequiredTemp")
    command = [
        sys.executable,
        "-c",
        (
            "import json,os,sys; print(json.dumps({"
            "'gemini':os.getenv('GEMINI_API_KEY'),"
            "'deepgram':os.getenv('DEEPGRAM_API_KEY'),"
            "'session':os.getenv('ASSISTANT_SESSION_TOKEN'),"
            "'temp':os.getenv('TEMP'),"
            "'echo':sys.argv[1]}))"
        ),
        SECRET_ENVIRONMENT["GEMINI_API_KEY"],
    ]

    returncode, stdout, stderr, truncated = await _run_process(
        command, CancellationToken(), timeout_seconds=10
    )
    result = json.loads(stdout)

    assert returncode == 0
    assert stderr == ""
    assert truncated is False
    assert result == {
        "gemini": None,
        "deepgram": None,
        "session": None,
        "temp": r"C:\RequiredTemp",
        "echo": "[REDACTED]",
    }


async def test_powershell_receives_only_runtime_and_operation_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, Any] = {}

    async def fake_spawn(*args: object, **kwargs: object) -> object:
        del args
        captured.update(kwargs)
        return object()

    async def fake_collect(*args: object, **kwargs: object) -> BoundedProcessOutput:
        del args, kwargs
        return BoundedProcessOutput(returncode=0, stdout=b"[]", stderr=b"")

    monkeypatch.setattr("jarvis_assistant.powershell.asyncio.create_subprocess_exec", fake_spawn)
    monkeypatch.setattr("jarvis_assistant.powershell.collect_process_output", fake_collect)
    result = await PowerShellRunner("powershell.exe").run(
        "list_directory",
        {"path": r"C:\Allowed", "limit": 3},
        CancellationToken(),
    )

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["JARVIS_PATH"] == r"C:\Allowed"
    assert environment["JARVIS_LIMIT"] == "3"
    assert all(name not in environment for name in SECRET_ENVIRONMENT)
    assert result.stdout == "[]"


async def test_piper_spawn_uses_sanitized_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    executable = tmp_path / "piper.exe"
    model = tmp_path / "voice.onnx"
    executable.write_bytes(b"binary")
    model.write_bytes(b"model")
    captured: dict[str, Any] = {}

    class FakeStdin:
        def write(self, value: bytes) -> None:
            del value

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeProcess:
        stdin = FakeStdin()
        returncode = 0

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        del args
        captured.update(kwargs)
        return FakeProcess()

    async def fake_collect(*args: object, **kwargs: object) -> BoundedProcessOutput:
        del args, kwargs
        return BoundedProcessOutput(returncode=0, stdout=b"", stderr=b"")

    async def fake_play(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(
        "jarvis_assistant.providers.piper.asyncio.create_subprocess_exec", fake_spawn
    )
    monkeypatch.setattr("jarvis_assistant.providers.piper.collect_process_output", fake_collect)
    monkeypatch.setattr(PiperTextToSpeechProvider, "_scale_wav_volume", lambda *args: True)
    monkeypatch.setattr(PiperTextToSpeechProvider, "_play", fake_play)

    await PiperTextToSpeechProvider(executable, model).speak("hello", CancellationToken())

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert all(name not in environment for name in SECRET_ENVIRONMENT)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows association broker")
async def test_association_broker_uses_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, Any] = {}

    async def fake_spawn(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return object()

    async def fake_collect(*args: object, **kwargs: object) -> BoundedProcessOutput:
        del args, kwargs
        return BoundedProcessOutput(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("jarvis_assistant.process_io.asyncio.create_subprocess_exec", fake_spawn)
    monkeypatch.setattr("jarvis_assistant.process_io.collect_process_output", fake_collect)

    await launch_associated_target("https://example.test", CancellationToken())

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert all(name not in environment for name in SECRET_ENVIRONMENT)
    assert captured["args"][1:] == (
        "url.dll,FileProtocolHandler",
        "https://example.test",
    )


def test_child_output_redacts_named_secrets() -> None:
    output = decode_redacted_child_output(b"GEMINI_API_KEY=generated-secret PASSWORD: also-secret")
    assert "generated-secret" not in output
    assert "also-secret" not in output
    assert output.count("[REDACTED]") == 2
