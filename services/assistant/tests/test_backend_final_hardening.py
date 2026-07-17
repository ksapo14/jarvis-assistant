from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.config import Settings
from jarvis_assistant.models import (
    ConversationRole,
    EventType,
    LanguageModelRequest,
    LanguageModelResponse,
    ProviderStatus,
    SettingPatch,
    ToolCall,
    Transcript,
)
from jarvis_assistant.powershell import PowerShellRunner
from jarvis_assistant.providers.base import LanguageModelProvider, SpeechToTextProvider
from jarvis_assistant.runtime import AssistantRuntime
from jarvis_assistant.tools.desktop import OpenApplicationTool
from jarvis_assistant.tools.safe_paths import PathScope
from jarvis_assistant.tools.system import (
    ExecuteTrustedScriptTool,
    RunApprovedPowerShellOperationTool,
)


class _SegmentedSpeech(SpeechToTextProvider):
    async def transcribe(
        self, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> AsyncIterator[Transcript]:
        del audio
        cancellation.raise_if_cancelled()
        yield Transcript(text="turn on", is_final=True)
        yield Transcript(text="the lights", is_final=True, speech_final=True)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="segmented", available=True, detail="test")


class _ConciseModel(LanguageModelProvider):
    async def complete(
        self, request: LanguageModelRequest, cancellation: CancellationToken
    ) -> LanguageModelResponse:
        cancellation.raise_if_cancelled()
        assert request.messages[-1].role is ConversationRole.USER
        return LanguageModelResponse(text="Okay.", spoken_text="Okay.")

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="concise", available=True, detail="test")


async def test_voice_command_persists_actual_request_and_emits_cumulative_finals(
    settings: Settings,
) -> None:
    runtime = AssistantRuntime.create(settings)
    runtime.orchestrator.speech_to_text = _SegmentedSpeech()
    runtime.orchestrator.language_model = _ConciseModel()
    await runtime.start()
    try:
        async with runtime.event_bus.subscribe() as queue:
            await runtime.orchestrator.start_listening()
            task = runtime.orchestrator._active_task
            assert task is not None
            await asyncio.wait_for(task, timeout=3)
            events = []
            while not queue.empty():
                events.append(await queue.get())

        final_texts = [
            event.payload["text"] for event in events if event.type is EventType.FINAL_TRANSCRIPT
        ]
        assert final_texts == ["turn on", "turn on the lights"]
        history = await runtime.memory.history()
        assert history[0]["user_request"] == "turn on the lights"
        assert "[voice command]" not in str(history)
    finally:
        await runtime.shutdown()


def test_redirected_windows_known_folders_are_default_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_assistant import known_folders

    redirected = {
        folder_id: tmp_path / name for name, folder_id in known_folders._KNOWN_FOLDER_IDS.items()
    }
    monkeypatch.setattr(
        known_folders,
        "_known_folder_path",
        lambda folder_id: redirected[folder_id],
    )
    assert known_folders.default_user_file_roots(tmp_path / "home", windows=True) == tuple(
        path.resolve() for path in redirected.values()
    )


def test_known_folder_lookup_has_per_folder_portable_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_assistant import known_folders

    desktop_id = known_folders._KNOWN_FOLDER_IDS["Desktop"]

    def resolve(folder_id: str) -> Path:
        if folder_id == desktop_id:
            return tmp_path / "redirected-desktop"
        raise OSError("unavailable")

    monkeypatch.setattr(known_folders, "_known_folder_path", resolve)
    roots = known_folders.default_user_file_roots(tmp_path / "home", windows=True)
    assert roots == (
        (tmp_path / "redirected-desktop").resolve(),
        (tmp_path / "home" / "Documents").resolve(),
        (tmp_path / "home" / "Downloads").resolve(),
    )


def test_settings_uses_redirected_known_folders_when_no_roots_are_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_assistant import config

    redirected = tuple((tmp_path / name).resolve() for name in ("Desk", "Docs", "Downloads"))
    monkeypatch.setattr(config, "default_user_file_roots", lambda: redirected)
    settings = Settings(
        _env_file=None,
        ASSISTANT_SESSION_TOKEN="test-session-token-that-is-at-least-32-chars",
        ALLOWED_FILE_ROOTS_JSON="[]",
    )
    assert settings.allowed_file_roots == redirected


async def test_preferred_application_alias_is_typed_persisted_and_stays_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program_files = tmp_path / "Program Files"
    application = program_files / "Editor" / "editor.exe"
    application.parent.mkdir(parents=True)
    application.write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("SystemRoot", raising=False)

    def make_settings() -> Settings:
        return Settings(
            _env_file=None,
            ASSISTANT_ENV="test",
            ASSISTANT_MOCK_MODE=True,
            ASSISTANT_DATA_DIR=tmp_path / "data",
            ASSISTANT_SESSION_TOKEN="test-session-token-that-is-at-least-32-chars",
            WAKE_WORD_ENABLED=False,
            ALLOWED_FILE_ROOTS_JSON=json.dumps([str(tmp_path)]),
        )

    first = AssistantRuntime.create(make_settings())
    await first.start()
    await first.orchestrator.update_settings(
        SettingPatch(preferred_applications={"Work Editor": str(application)})
    )
    await first.shutdown()

    second = AssistantRuntime.create(make_settings())
    await second.start()
    try:
        assert second.settings.preferred_applications == {"work editor": str(application)}
        snapshot = await second.orchestrator.settings_snapshot()
        assert snapshot["preferred_applications"] == {"work editor": str(application)}
        tool = second.registry.get("open_application")
        assert isinstance(tool, OpenApplicationTool)
        assert tool._resolve("WORK   EDITOR", second.settings.preferred_applications) == str(
            application.resolve()
        )
    finally:
        await second.shutdown()


@pytest.mark.parametrize(
    "value",
    [
        {"editor": "notepad.exe"},
        {"editor": "C:/Windows/notepad.exe --flag"},
        {"editor & shell": "C:/Windows/notepad.exe"},
    ],
)
def test_preferred_application_alias_rejects_names_args_and_nonabsolute_paths(
    value: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        SettingPatch(preferred_applications=value)


async def test_trusted_script_execution_is_bound_to_verified_bytes_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_assistant.tools import system

    trusted = tmp_path / "trusted"
    writable = tmp_path / "writable"
    trusted.mkdir()
    writable.mkdir()
    script = trusted / "approved.py"
    approved = b"print('approved')\n"
    script.write_bytes(approved)
    executed: dict[str, Any] = {}

    async def fake_run_process(
        command: list[str],
        cancellation: CancellationToken,
        *,
        cwd: Path | None = None,
        timeout_seconds: float,
    ) -> tuple[int, str, str, bool]:
        del cancellation, cwd, timeout_seconds
        execution_path = Path(command[1])
        executed["path"] = execution_path
        try:
            script.write_bytes(b"print('swapped')\n")
            executed["source_swap_blocked"] = False
        except OSError:
            executed["source_swap_blocked"] = True
        executed["bytes"] = await asyncio.to_thread(execution_path.read_bytes)
        return 0, "approved", "", False

    monkeypatch.setattr(system, "_run_process", fake_run_process)
    tool = ExecuteTrustedScriptTool(
        (trusted,),
        lambda: True,
        python_executable=Path(sys.executable),
        approved_scripts=(script,),
        writable_roots=(writable,),
    )
    values = tool.validate({"script_path": str(script), "arguments": []})
    _, bound = await tool.bind_confirmation(
        ToolCall(id="verified-copy", name=tool.name, arguments=values.model_dump(mode="json")),
        values,
        CancellationToken(),
    )
    await tool.execute(bound, CancellationToken())

    assert executed["bytes"] == approved
    execution_path = executed["path"]
    if sys.platform == "win32":
        assert executed["source_swap_blocked"] is True
        assert execution_path == script.resolve()
    else:
        assert execution_path != script.resolve()
        assert not execution_path.parent.exists()
    script.write_bytes(b"print('lock released')\n")


def test_powershell_tool_descriptor_only_advertises_implemented_model_operations() -> None:
    tool = RunApprovedPowerShellOperationTool(
        PowerShellRunner("powershell"), PathScope((Path.cwd(),))
    )
    advertised = set(tool.descriptor.argument_schema["properties"]["operation"]["enum"])
    assert advertised == {"list_directory", "get_processes", "get_system_information"}
    assert advertised <= set(PowerShellRunner._OPERATIONS)
    assert "set_volume" not in advertised
