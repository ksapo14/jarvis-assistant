from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import shutil
import sys
import types
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from jarvis_assistant.cancellation import CancellationToken, OperationCancelled
from jarvis_assistant.config import Settings
from jarvis_assistant.confirmations import ConfirmationManager
from jarvis_assistant.data_cleanup import clear_app_owned_screenshots
from jarvis_assistant.events import EventBus
from jarvis_assistant.logging_config import clear_rotating_logs, redact
from jarvis_assistant.memory import MemoryService
from jarvis_assistant.models import (
    AssistantState,
    ConfirmationDecision,
    ConversationMessage,
    ConversationRole,
    EventType,
    LanguageModelRequest,
    LanguageModelResponse,
    PermissionCategory,
    PermissionLevel,
    ProviderStatus,
    RiskLevel,
    SettingPatch,
    ToolCall,
    ToolDescriptor,
    ToolResult,
    Transcript,
)
from jarvis_assistant.parent_watchdog import ParentProcessWatchdog
from jarvis_assistant.permissions import PermissionManager
from jarvis_assistant.providers.base import (
    LanguageModelProvider,
    ProviderUnavailableError,
    SpeechToTextProvider,
)
from jarvis_assistant.providers.deepgram import DeepgramSpeechToTextProvider
from jarvis_assistant.providers.gemini import GeminiLanguageModelProvider
from jarvis_assistant.providers.openwakeword import OpenWakeWordProvider
from jarvis_assistant.providers.piper import PiperTextToSpeechProvider
from jarvis_assistant.runtime import AssistantRuntime
from jarvis_assistant.tools.base import (
    ToolExecutionError,
    ToolUnavailableError,
    ToolValidationError,
)
from jarvis_assistant.tools.desktop import (
    ClickControlArguments,
    OpenApplicationTool,
    TypeTextArguments,
    TypeTextTool,
    WindowTarget,
    _click_named_control,
    _configure_user32,
    _set_audio_volume,
    _set_text_via_uia_bound,
    _validate_type_text_target,
)
from jarvis_assistant.tools.files import DeletePathTool, MovePathTool, OpenPathTool
from jarvis_assistant.tools.safe_paths import PathScope
from jarvis_assistant.tools.system import ExecuteTrustedScriptTool, _run_process


def _descriptor(
    *,
    name: str = "example_tool",
    risk: RiskLevel = RiskLevel.MEDIUM,
    confirmation_required: bool = True,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="A test tool.",
        argument_schema={"type": "object", "properties": {}},
        result_schema={"type": "object"},
        permission_category=PermissionCategory.SYSTEM,
        risk_level=risk,
        confirmation_required=confirmation_required,
        timeout_seconds=5,
    )


def test_gemini_schema_dereferences_every_registered_tool(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    payload = GeminiLanguageModelProvider("secret")._build_payload(
        LanguageModelRequest(
            messages=[ConversationMessage(role=ConversationRole.USER, content="help")],
            tools=runtime.registry.descriptors(),
        )
    )
    declarations = payload["tools"][0]["functionDeclarations"]
    serialized = json.dumps(declarations)
    assert '"$ref"' not in serialized
    assert '"$defs"' not in serialized
    manage_window = next(item for item in declarations if item["name"] == "manage_window")
    assert manage_window["parameters"]["properties"]["action"]["enum"] == [
        "minimize",
        "maximize",
        "restore",
        "focus",
    ]


def test_gemini_builds_canonical_function_call_and_response_turns() -> None:
    call = ToolCall(id="call-1", name="get_current_datetime", arguments={})
    payload = GeminiLanguageModelProvider("secret")._build_payload(
        LanguageModelRequest(
            messages=[
                ConversationMessage(role=ConversationRole.USER, content="What time?"),
                ConversationMessage(
                    role=ConversationRole.ASSISTANT,
                    content="",
                    tool_calls=[call],
                ),
                ConversationMessage(
                    role=ConversationRole.TOOL,
                    name=call.name,
                    tool_call_id=call.id,
                    content='{"success":true,"summary":"done"}',
                ),
            ],
            tools=[_descriptor(name="get_current_datetime", risk=RiskLevel.LOW)],
        )
    )
    assert payload["contents"][1] == {
        "role": "model",
        "parts": [
            {
                "functionCall": {
                    "id": "call-1",
                    "name": "get_current_datetime",
                    "args": {},
                }
            }
        ],
    }
    function_response = payload["contents"][2]["parts"][0]["functionResponse"]
    assert function_response["id"] == "call-1"
    assert function_response["response"]["success"] is True


def test_gemini_preserves_provider_call_id_and_thought_signature() -> None:
    response = GeminiLanguageModelProvider._parse_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "thoughtSignature": "opaque-signature",
                                "functionCall": {
                                    "id": "provider-call-id",
                                    "name": "get_current_datetime",
                                    "args": {},
                                },
                            }
                        ]
                    }
                }
            ]
        },
        {"get_current_datetime"},
    )
    call = response.tool_calls[0]
    assert call.id == "provider-call-id"
    assert call.provider_metadata["thought_signature"] == "opaque-signature"
    payload = GeminiLanguageModelProvider("secret")._build_payload(
        LanguageModelRequest(
            messages=[
                ConversationMessage(role=ConversationRole.ASSISTANT, content="", tool_calls=[call]),
                ConversationMessage(
                    role=ConversationRole.TOOL,
                    content='{"success":true}',
                    name=call.name,
                    tool_call_id=call.id,
                ),
            ],
            tools=[_descriptor(name=call.name)],
        )
    )
    assert payload["contents"][0]["parts"][0]["thoughtSignature"] == "opaque-signature"
    assert payload["contents"][1]["parts"][0]["functionResponse"]["id"] == "provider-call-id"


async def test_gemini_http_request_cancels_promptly() -> None:
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiLanguageModelProvider("secret", client=client)
    token = CancellationToken()
    task = asyncio.create_task(
        provider.complete(
            LanguageModelRequest(
                messages=[ConversationMessage(role=ConversationRole.USER, content="hello")]
            ),
            token,
        )
    )
    await entered.wait()
    token.cancel()
    with pytest.raises(OperationCancelled):
        await asyncio.wait_for(task, timeout=0.5)
    await client.aclose()


async def test_openwakeword_uses_honest_stock_model_and_reloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[list[str]] = []

    class FakeModel:
        resets = 0

        def __init__(self, *, wakeword_models: list[str]) -> None:
            loaded.append(wakeword_models)

        def reset(self) -> None:
            type(self).resets += 1

    package = types.ModuleType("openwakeword")
    package.__path__ = []  # type: ignore[attr-defined]
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)
    provider = OpenWakeWordProvider(None, phrase="hey jarvis", sensitivity=0.5)
    await provider._get_model()
    assert loaded == [["hey_jarvis"]]
    await provider.configure(phrase="hey jarvis", sensitivity=0.7)
    await provider._get_model()
    assert loaded == [["hey_jarvis"], ["hey_jarvis"]]
    assert FakeModel.resets == 1
    await provider.configure(phrase="computer", sensitivity=0.7)
    with pytest.raises(ProviderUnavailableError, match="custom phrase"):
        await provider._get_model()


def test_piper_scales_16_bit_pcm_volume(tmp_path: Path) -> None:
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes((10_000).to_bytes(2, "little", signed=True))
    assert PiperTextToSpeechProvider._scale_wav_volume(path, 0.5)
    with wave.open(str(path), "rb") as source:
        sample = int.from_bytes(source.readframes(1), "little", signed=True)
    assert sample == 5_000


class _HangingWebSocket:
    def __aiter__(self) -> _HangingWebSocket:
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send(self, data: object) -> None:
        del data

    async def close(self, **kwargs: object) -> None:
        del kwargs


class _WebSocketContext:
    async def __aenter__(self) -> _HangingWebSocket:
        return _HangingWebSocket()

    async def __aexit__(self, *args: object) -> None:
        del args


async def test_deepgram_propagates_audio_sender_failure() -> None:
    async def broken_audio() -> AsyncIterator[bytes]:
        if False:
            yield b""
        raise ProviderUnavailableError("microphone permission denied")

    provider = DeepgramSpeechToTextProvider(
        "secret", connector=lambda *args, **kwargs: _WebSocketContext()
    )
    transcripts = provider.transcribe(broken_audio(), CancellationToken())
    with pytest.raises(ProviderUnavailableError, match="microphone permission"):
        await asyncio.wait_for(anext(transcripts), timeout=0.5)


async def test_system_process_output_is_bounded() -> None:
    command = [sys.executable, "-c", "import sys;sys.stdout.write('x'*70000)"]
    with pytest.raises(ToolExecutionError, match="output limit"):
        await _run_process(command, CancellationToken(), timeout_seconds=5)


def test_redaction_covers_assignments_and_query_parameters() -> None:
    source = (
        "DEEPGRAM_API_KEY=topsecret password: hunter2 "
        "https://example.test/?access_token=querysecret"
    )
    value = str(redact(source))
    assert "topsecret" not in value
    assert "hunter2" not in value
    assert "querysecret" not in value
    assert value.count("[REDACTED]") == 3


async def test_medium_without_required_confirmation_can_be_explicitly_auto_allowed(
    memory: MemoryService,
) -> None:
    manager = PermissionManager(memory)
    descriptor = _descriptor(confirmation_required=False)
    await manager.update_policy(descriptor, enabled=True, permission=PermissionLevel.ALWAYS_ALLOW)
    decision = await manager.authorize(descriptor)
    assert decision.allowed
    assert not decision.requires_confirmation
    high = _descriptor(name="dangerous_tool", risk=RiskLevel.HIGH)
    assert (await manager.authorize(high)).requires_confirmation


async def test_permission_is_rechecked_after_confirmation(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        tool = runtime.registry.get("get_current_datetime")
        await runtime.permissions.update_policy(
            tool.descriptor,
            enabled=True,
            permission=PermissionLevel.ASK_EVERY_TIME,
        )
        await runtime.state.transition(AssistantState.THINKING)
        execution = asyncio.create_task(
            runtime.orchestrator._execute_tool(
                uuid4(),
                ToolCall(id="time-1", name="get_current_datetime", arguments={}),
                CancellationToken(),
            )
        )
        for _ in range(100):
            pending = await runtime.confirmations.pending()
            if pending:
                break
            await asyncio.sleep(0.001)
        assert pending
        await runtime.permissions.update_policy(
            tool.descriptor,
            enabled=False,
            permission=PermissionLevel.DISABLED,
        )
        item = pending[0]
        await runtime.confirmations.decide(
            UUID(str(item["id"])),
            ConfirmationDecision(
                decision="yes",
                confirmation_token=str(item["confirmation_token"]),
                action_fingerprint=str(item["action_fingerprint"]),
            ),
        )
        result = await execution
        assert not result.success
        assert result.error_code == "permission_changed"
    finally:
        await runtime.shutdown()


async def test_history_uses_wire_contract_and_includes_command_only_turn(
    memory: MemoryService,
) -> None:
    await memory.start_command("command-1", "Do a thing")
    result = ToolResult(
        tool_call_id="call-1",
        tool_name="example_tool",
        success=True,
        summary="Completed.",
    )
    await memory.add_tool_history(
        command_id="command-1",
        tool_name="example_tool",
        arguments={"value": 1},
        result=result,
        risk_level=RiskLevel.MEDIUM,
        confirmation_result="yes",
    )
    await memory.finish_command("command-1", "Done", "completed")
    await memory.start_command("command-2", "Just answer")
    await memory.finish_command("command-2", "Here is the answer", "completed")
    history = await memory.history()
    tool_row = next(item for item in history if item.get("tool_name"))
    command_row = next(item for item in history if item["command_id"] == "command-2")
    assert tool_row["tool_arguments"] == {"value": 1}
    assert tool_row["tool_result"]["success"] is True
    assert tool_row["confirmation_result"] == "approved"
    assert tool_row["status"] == "success"
    assert command_row["user_request"] == "Just answer"
    assert command_row["assistant_response"] == "Here is the answer"


def _make_persistent_settings(data_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        ASSISTANT_ENV="test",
        ASSISTANT_MOCK_MODE=True,
        ASSISTANT_DATA_DIR=data_dir,
        ASSISTANT_SESSION_TOKEN="test-session-token-that-is-at-least-32-chars",
        WAKE_WORD_ENABLED=False,
        ALLOWED_FILE_ROOTS_JSON=f'["{data_dir.as_posix()}"]',
    )


async def test_settings_hydrate_and_normalize_microphone_after_restart(
    tmp_path: Path,
) -> None:
    first = AssistantRuntime.create(_make_persistent_settings(tmp_path))
    await first.start()
    await first.orchestrator.update_settings(
        SettingPatch(
            microphone_device="4",
            wake_phrase="computer",
            wake_sensitivity=0.73,
            developer_mode=True,
        )
    )
    await first.shutdown()

    second = AssistantRuntime.create(_make_persistent_settings(tmp_path))
    await second.start()
    try:
        assert second.settings.microphone_device == "4"
        assert second.orchestrator.audio_capture.device == 4
        assert second.settings.wake_word_phrase == "computer"
        assert second.settings.wake_word_sensitivity == 0.73
        assert second.settings.developer_mode is True
        snapshot = await second.orchestrator.settings_snapshot()
        assert "launch_development_command" in snapshot["tool_permissions"]
    finally:
        await second.shutdown()


async def test_live_piper_settings_support_explicit_path_clearing(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    provider = PiperTextToSpeechProvider(Path("piper.exe"), Path("voice.onnx"))
    runtime.orchestrator.text_to_speech = provider
    await runtime.start()
    try:
        patch = SettingPatch(
            piper_executable_path=None,
            piper_model_path=None,
            speech_rate=1.25,
            speech_volume=0.4,
        )
        assert patch.model_dump(exclude_unset=True)["piper_model_path"] is None
        await runtime.orchestrator.update_settings(patch)
        assert provider._executable_path is None
        assert provider._model_path is None
        assert provider._speech_rate == 1.25
        assert provider._volume == 0.4
        with pytest.raises(ValidationError, match="speech_rate cannot be null"):
            SettingPatch(speech_rate=None)
    finally:
        await runtime.shutdown()


async def test_save_history_false_keeps_tool_audit_without_conversation(
    tmp_path: Path,
) -> None:
    settings = _make_persistent_settings(tmp_path)
    settings.save_conversation_history = False
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        await runtime.orchestrator.submit_text("What time is it?")
        assert runtime.orchestrator._active_task is not None
        await runtime.orchestrator._active_task
        assert await runtime.memory.recent_conversation() == []
        assert await runtime.memory.latest_summary() is None
        history = await runtime.memory.history()
        assert history[0]["tool_name"] == "get_current_datetime"
        assert history[0]["user_request"] == ""
    finally:
        await runtime.shutdown()


async def test_developer_tools_are_hidden_and_fail_closed(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        descriptors = await runtime.orchestrator._enabled_descriptors()
        assert all(
            item.permission_category is not PermissionCategory.DEVELOPMENT for item in descriptors
        )
        result = await runtime.registry.execute(
            ToolCall(
                id="dev-1",
                name="launch_development_command",
                arguments={"command_name": "missing"},
            ),
            CancellationToken(),
            confirmed=True,
        )
        assert not result.success
        assert result.error_code == "tool_validation_failed"
        assert "developer mode is disabled" in result.summary
    finally:
        await runtime.shutdown()


def _make_symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")


async def test_file_tools_reject_final_component_links_without_touching_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "valuable"
    target.mkdir()
    valuable_file = target / "keep.txt"
    valuable_file.write_text("keep", encoding="utf-8")
    link = root / "alias"
    _make_symlink(link, target, directory=True)
    scope = PathScope((root,))
    with pytest.raises(ToolValidationError, match="symbolic links"):
        scope.resolve(str(link), must_exist=True)
    delete = DeletePathTool(scope)
    with pytest.raises(ToolValidationError):
        await delete.execute(
            delete.validate({"path": str(link), "recursive": True}), CancellationToken()
        )
    move = MovePathTool(scope)
    with pytest.raises(ToolValidationError):
        await move.execute(
            move.validate({"source": str(link), "destination": str(root / "moved")}),
            CancellationToken(),
        )
    assert valuable_file.read_text(encoding="utf-8") == "keep"


async def test_delete_and_move_reject_replaced_source_after_confirmation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    scope = PathScope((root,))

    delete_target = root / "delete.txt"
    delete_target.write_text("original", encoding="utf-8")
    delete = DeletePathTool(scope)
    delete_call = ToolCall(
        id="delete-identity",
        name=delete.name,
        arguments={"path": str(delete_target), "recursive": False},
    )
    _, delete_bound = await delete.bind_confirmation(
        delete_call, delete.validate(delete_call.arguments), CancellationToken()
    )
    delete_target.rename(root / "original-delete.txt")
    delete_target.write_text("replacement", encoding="utf-8")
    with pytest.raises(ToolValidationError, match="changed after confirmation"):
        await delete.execute(delete_bound, CancellationToken())
    assert delete_target.read_text(encoding="utf-8") == "replacement"

    move_source = root / "move.txt"
    move_source.write_text("original", encoding="utf-8")
    move = MovePathTool(scope)
    move_call = ToolCall(
        id="move-identity",
        name=move.name,
        arguments={
            "source": str(move_source),
            "destination": str(root / "destination.txt"),
        },
    )
    _, move_bound = await move.bind_confirmation(
        move_call, move.validate(move_call.arguments), CancellationToken()
    )
    move_source.rename(root / "original-move.txt")
    move_source.write_text("replacement", encoding="utf-8")
    with pytest.raises(ToolValidationError, match="changed after confirmation"):
        await move.execute(move_bound, CancellationToken())
    assert move_source.read_text(encoding="utf-8") == "replacement"


async def test_open_path_rejects_active_content_before_os_launch(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    active = root / "payload.html"
    active.write_text("<script>alert(1)</script>", encoding="utf-8")
    tool = OpenPathTool(PathScope((root,)))
    with pytest.raises(ToolValidationError, match="allowlist"):
        await tool.execute(tool.validate({"path": str(active)}), CancellationToken())


def test_open_application_rejects_untrusted_path_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    untrusted = tmp_path / "calc.exe"
    untrusted.write_bytes(b"MZ")
    monkeypatch.setattr(shutil, "which", lambda name: str(untrusted))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    with pytest.raises(ToolValidationError, match="approved installation"):
        OpenApplicationTool._resolve("calculator")


@pytest.mark.parametrize("text", ["hello\nworld", "run\r", "next\tfield", "escape\x1b"])
def test_type_text_rejects_action_producing_control_characters(text: str) -> None:
    with pytest.raises(ValidationError, match="control characters"):
        TypeTextArguments(text=text)


def test_type_text_rejects_terminal_and_shell_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psutil

    process = types.SimpleNamespace(name=lambda: "powershell.exe")
    monkeypatch.setattr(psutil, "Process", lambda process_id: process)
    with pytest.raises(ToolValidationError, match="terminals and command shells"):
        _validate_type_text_target(42)


def test_type_text_uses_target_addressed_value_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uiautomation as automation

    from jarvis_assistant.tools import desktop

    target = WindowTarget(handle=100, process_id=42, title="Editor")

    class ValuePattern:
        IsReadOnly = False
        Value = "before "

        def SetValue(self, value: str) -> bool:
            self.Value = value
            return True

    pattern = ValuePattern()
    control = types.SimpleNamespace(
        ProcessId=42,
        IsPassword=False,
        NativeWindowHandle=100,
        GetParentControl=lambda: None,
        GetPattern=lambda pattern_id: pattern,
    )
    monkeypatch.setattr(desktop, "_get_active_window_target", lambda: target)
    monkeypatch.setattr(desktop, "_validate_type_text_target", lambda process_id: None)
    monkeypatch.setattr(automation, "GetFocusedControl", lambda: control)

    assert _set_text_via_uia_bound("after", target) == 5
    assert pattern.Value == "before after"


def test_type_text_rejects_same_process_control_from_another_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uiautomation as automation

    from jarvis_assistant.tools import desktop

    target = WindowTarget(handle=100, process_id=42, title="Confirmed")
    other_window = types.SimpleNamespace(
        ProcessId=42,
        NativeWindowHandle=200,
        GetParentControl=lambda: None,
    )
    monkeypatch.setattr(desktop, "_get_active_window_target", lambda: target)
    monkeypatch.setattr(desktop, "_validate_type_text_target", lambda process_id: None)
    monkeypatch.setattr(automation, "GetFocusedControl", lambda: other_window)
    with pytest.raises(ToolValidationError, match="different window"):
        _set_text_via_uia_bound("safe text", target)


def test_tab_and_list_controls_use_selection_not_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uiautomation as automation

    from jarvis_assistant.tools import desktop

    target = WindowTarget(handle=100, process_id=42, title="Settings")
    selected: list[bool] = []

    class SelectionPattern:
        def Select(self) -> bool:
            selected.append(True)
            return True

    class Control:
        def Exists(self, *, maxSearchSeconds: float) -> bool:
            return maxSearchSeconds > 0

        def GetPattern(self, pattern_id: int) -> SelectionPattern | None:
            if pattern_id == automation.PatternId.SelectionItemPattern:
                return SelectionPattern()
            raise AssertionError("list selection must never use InvokePattern")

    def constructor(**kwargs: object) -> Control:
        del kwargs
        return Control()

    root = types.SimpleNamespace(
        ButtonControl=constructor,
        MenuItemControl=constructor,
        TabItemControl=constructor,
        ListItemControl=constructor,
        Control=constructor,
    )
    monkeypatch.setattr(desktop, "_get_active_window_target", lambda: target)
    monkeypatch.setattr(automation, "ControlFromHandle", lambda handle: root)
    values = ClickControlArguments(
        control_name="General",
        control_type="list_item",
        target_window_handle=100,
        target_process_id=42,
        target_window_title="Settings",
    )

    _click_named_control(values, target)
    assert selected == [True]


def test_system_volume_uses_native_scalar_and_reports_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis_assistant.tools import desktop

    requested: list[float] = []
    endpoint = types.SimpleNamespace(
        SetMasterVolumeLevelScalar=lambda value, context: requested.append(value),
        GetMasterVolumeLevelScalar=lambda: 0.36,
    )
    monkeypatch.setattr(desktop, "_audio_endpoint", lambda: endpoint)

    assert _set_audio_volume(37) == 36
    assert requested == [0.37]


async def test_type_text_confirmation_is_bound_to_unchanged_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis_assistant.tools import desktop

    first = WindowTarget(handle=2**40, process_id=10, title="Document")
    second = WindowTarget(handle=2**40 + 1, process_id=11, title="Other")
    monkeypatch.setattr(desktop, "_get_active_window_target", lambda: first)
    monkeypatch.setattr(desktop, "_validate_type_text_target", lambda process_id: None)
    tool = TypeTextTool()
    call = ToolCall(id="type-1", name=tool.name, arguments={"text": "hello"})
    bound_call, bound_values = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    assert "target_window_handle" not in tool.descriptor.argument_schema["properties"]
    assert bound_call.arguments["target_window_handle"] == 2**40
    monkeypatch.setattr(desktop, "_get_active_window_target", lambda: second)
    with pytest.raises(ToolValidationError, match="changed after confirmation"):
        await tool.execute(bound_values, CancellationToken())


async def test_wake_capture_closes_before_command_capture_starts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class ExclusiveCapture:
        active = False

        async def frames(self, cancellation: CancellationToken) -> AsyncIterator[bytes]:
            if self.active:
                raise RuntimeError("microphone stream overlap")
            self.active = True
            events.append("wake-open")
            try:
                yield b"wake"
            finally:
                self.active = False
                events.append("wake-close")

    class WakeWord:
        async def detect(self, frame: bytes) -> bool:
            return frame == b"wake"

    capture = ExclusiveCapture()
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    runtime.orchestrator.audio_capture = capture  # type: ignore[assignment]
    runtime.orchestrator.wake_word = WakeWord()  # type: ignore[assignment]
    runtime.orchestrator.settings.play_activation_sound = False

    async def start_listening(*, wake_activated: bool = False) -> None:
        assert wake_activated
        assert not capture.active
        events.append("command-start")
        runtime.orchestrator._shutdown.set()

    monkeypatch.setattr(runtime.orchestrator, "start_listening", start_listening)
    try:
        await asyncio.wait_for(runtime.orchestrator._wake_loop(), timeout=1)
    finally:
        await runtime.shutdown()
    assert events == ["wake-open", "wake-close", "command-start"]


async def test_push_to_talk_stops_wake_capture_before_command_stream(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    wake_open = asyncio.Event()

    class ExclusiveCapture:
        active = False
        invocation = 0

        async def frames(self, cancellation: CancellationToken) -> AsyncIterator[bytes]:
            if self.active:
                raise RuntimeError("microphone stream overlap")
            self.active = True
            self.invocation += 1
            label = "wake" if self.invocation == 1 else "command"
            events.append(f"{label}-open")
            if label == "wake":
                wake_open.set()
            try:
                while not cancellation.cancelled:
                    await asyncio.sleep(0.01)
                    yield label.encode()
            finally:
                self.active = False
                events.append(f"{label}-close")

    class NeverWake:
        async def detect(self, frame: bytes) -> bool:
            del frame
            return False

    capture = ExclusiveCapture()
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    runtime.orchestrator.audio_capture = capture  # type: ignore[assignment]
    runtime.orchestrator.wake_word = NeverWake()  # type: ignore[assignment]

    async def capture_command(command_id: UUID, cancellation: CancellationToken) -> str:
        del command_id
        async for _frame in capture.frames(cancellation):
            break
        runtime.orchestrator._shutdown.set()
        return "heard"

    monkeypatch.setattr(runtime.orchestrator, "_capture_and_process", capture_command)
    wake_task = asyncio.create_task(runtime.orchestrator._wake_loop())
    runtime.orchestrator._wake_task = wake_task
    try:
        await asyncio.wait_for(wake_open.wait(), timeout=1)
        await runtime.orchestrator.start_listening()
        active = runtime.orchestrator._active_task
        assert active is not None
        await asyncio.wait_for(active, timeout=1)
    finally:
        await runtime.shutdown()
    assert events.index("wake-close") < events.index("command-open")


async def test_manual_listen_ignores_wake_result_that_finishes_after_cancellation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    detection_started = asyncio.Event()
    release_detection = asyncio.Event()

    class ExclusiveCapture:
        active = False
        invocation = 0

        async def frames(self, cancellation: CancellationToken) -> AsyncIterator[bytes]:
            if self.active:
                raise RuntimeError("microphone stream overlap")
            self.active = True
            self.invocation += 1
            label = "wake" if self.invocation == 1 else "command"
            events.append(f"{label}-open")
            try:
                yield label.encode()
                await cancellation.wait()
            finally:
                self.active = False
                events.append(f"{label}-close")

    class DelayedWake:
        async def detect(self, frame: bytes) -> bool:
            assert frame == b"wake"
            detection_started.set()
            await release_detection.wait()
            return True

    capture = ExclusiveCapture()
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    runtime.orchestrator.audio_capture = capture  # type: ignore[assignment]
    runtime.orchestrator.wake_word = DelayedWake()  # type: ignore[assignment]

    async def capture_command(command_id: UUID, cancellation: CancellationToken) -> str:
        del command_id
        frames = capture.frames(cancellation)
        try:
            await anext(frames)
        finally:
            await frames.aclose()
        runtime.orchestrator._shutdown.set()
        return "heard"

    async def announce_wake() -> None:
        events.append("wake-announced")

    monkeypatch.setattr(runtime.orchestrator, "_capture_and_process", capture_command)
    monkeypatch.setattr(runtime.orchestrator, "_announce_wake_activation", announce_wake)
    wake_task = asyncio.create_task(runtime.orchestrator._wake_loop())
    runtime.orchestrator._wake_task = wake_task
    try:
        await asyncio.wait_for(detection_started.wait(), timeout=1)
        wake_token = runtime.orchestrator._wake_cancellation
        assert wake_token is not None
        manual_start = asyncio.create_task(runtime.orchestrator.start_listening())
        await asyncio.wait_for(wake_token.wait(), timeout=1)
        assert runtime.orchestrator._wake_paused
        release_detection.set()
        await asyncio.wait_for(manual_start, timeout=1)
        active = runtime.orchestrator._active_task
        assert active is not None
        await asyncio.wait_for(active, timeout=1)
        await asyncio.wait_for(wake_task, timeout=1)
    finally:
        await runtime.shutdown()
    assert "wake-announced" not in events
    assert events.index("wake-close") < events.index("command-open")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ctypes ABI test")
def test_windows_ctypes_prototypes_preserve_pointer_width() -> None:
    class Function:
        argtypes: list[object] | None = None
        restype: object | None = None

        def __call__(self, *args: object) -> int:
            del args
            return 0

    names = (
        "GetForegroundWindow",
        "GetWindowTextLengthW",
        "GetWindowTextW",
        "GetWindowThreadProcessId",
        "IsWindowVisible",
        "ShowWindow",
        "SetForegroundWindow",
        "PostMessageW",
        "OpenClipboard",
        "CloseClipboard",
        "EmptyClipboard",
        "GetClipboardData",
        "SetClipboardData",
    )
    library = types.SimpleNamespace(**{name: Function() for name in names})
    _configure_user32(library)
    assert ctypes.sizeof(library.GetForegroundWindow.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(library.GetClipboardData.restype) == ctypes.sizeof(ctypes.c_void_p)


async def test_frozen_backend_rejects_python_script_without_trusted_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "trusted.py"
    script.write_text("print('ok')", encoding="utf-8")
    tool = ExecuteTrustedScriptTool((tmp_path,), lambda: True, approved_scripts=(script,))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    values = tool.validate({"script_path": str(script), "arguments": []})
    _, values = await tool.bind_confirmation(
        ToolCall(id="script-1", name=tool.name, arguments=values.model_dump(mode="json")),
        values,
        CancellationToken(),
    )
    with pytest.raises(ToolUnavailableError, match="packaged backend"):
        await tool.execute(values, CancellationToken())


async def test_trusted_script_rejects_writable_overlap_and_content_change(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    writable = tmp_path / "writable"
    trusted.mkdir()
    writable.mkdir()
    script = trusted / "approved.py"
    script.write_text("print('approved')", encoding="utf-8")
    overlapping = ExecuteTrustedScriptTool(
        (trusted,),
        lambda: True,
        approved_scripts=(script,),
        writable_roots=(tmp_path,),
    )
    values = overlapping.validate({"script_path": str(script), "arguments": []})
    with pytest.raises(ToolValidationError, match="overlap"):
        await overlapping.bind_confirmation(
            ToolCall(id="overlap", name=overlapping.name, arguments={"script_path": str(script)}),
            values,
            CancellationToken(),
        )

    tool = ExecuteTrustedScriptTool(
        (trusted,),
        lambda: True,
        approved_scripts=(script,),
        writable_roots=(writable,),
    )
    values = tool.validate({"script_path": str(script), "arguments": []})
    _, bound = await tool.bind_confirmation(
        ToolCall(id="script-change", name=tool.name, arguments={"script_path": str(script)}),
        values,
        CancellationToken(),
    )
    script.write_text("print('changed')", encoding="utf-8")
    with pytest.raises(ToolValidationError, match="content changed"):
        await tool.execute(bound, CancellationToken())


def test_clear_screenshots_stays_inside_owned_directory(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    (screenshots / "capture.png").write_bytes(b"private")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    clear_app_owned_screenshots(tmp_path)
    assert not screenshots.exists()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_clear_screenshots_unlinks_reparse_target_without_deleting_it(tmp_path: Path) -> None:
    target = tmp_path / "external-captures"
    target.mkdir()
    private = target / "keep.png"
    private.write_bytes(b"keep")
    link = tmp_path / "screenshots"
    _make_symlink(link, target, directory=True)
    clear_app_owned_screenshots(tmp_path)
    assert not link.exists()
    assert private.read_bytes() == b"keep"


def test_clear_logs_rejects_path_outside_data_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside"):
        clear_rotating_logs(
            tmp_path.parent / "outside-logs",
            data_dir=tmp_path,
            level="INFO",
            max_bytes=100_000,
            backup_count=1,
        )


async def test_parent_watchdog_detects_exit_and_pid_reuse() -> None:
    identities = iter([123.0, 123.0, 999.0])
    lost = asyncio.Event()
    watchdog = ParentProcessWatchdog(
        42,
        identity_probe=lambda process_id: next(identities),
        poll_interval_seconds=0.001,
    )

    async def on_lost() -> None:
        lost.set()

    await asyncio.wait_for(watchdog.run(on_lost), timeout=0.5)
    assert lost.is_set()


async def test_confirmation_expiry_is_published_and_pending_is_queryable(
    memory: MemoryService,
) -> None:
    event_bus = EventBus()
    manager = ConfirmationManager(memory, event_bus, timeout_seconds=0.01)
    async with event_bus.subscribe() as queue:
        request = await manager.create(
            ToolCall(id="confirm-1", name="example_tool", arguments={"target": "x"}),
            RiskLevel.MEDIUM,
            "Do the exact action?",
        )
        pending = await manager.pending()
        assert pending[0]["confirmation_token"] == request.confirmation_token
        assert (await queue.get()).type is EventType.CONFIRMATION_REQUEST
        assert await manager.wait(request) == "no"
        resolved = await asyncio.wait_for(queue.get(), timeout=0.2)
        assert resolved.type is EventType.CONFIRMATION_DECISION
        assert resolved.payload["decision"] == "expired"
        assert await manager.pending() == []


def test_host_contract_rejects_non_ipv4_loopback(tmp_path: Path) -> None:
    common: dict[str, Any] = {
        "_env_file": None,
        "ASSISTANT_DATA_DIR": tmp_path,
        "ASSISTANT_SESSION_TOKEN": "test-session-token-that-is-at-least-32-chars",
    }
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        Settings(**common, ASSISTANT_HOST="localhost")
    assert Settings(**common, ASSISTANT_HOST="127.0.0.1").host == "127.0.0.1"


class _SegmentedSpeechProvider(SpeechToTextProvider):
    async def transcribe(
        self, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> AsyncIterator[Transcript]:
        del audio
        cancellation.raise_if_cancelled()
        yield Transcript(text="turn on", is_final=True)
        yield Transcript(text="the lights", is_final=True, speech_final=True)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="segmented", available=True, detail="test")


class _CapturingLanguageModel(LanguageModelProvider):
    def __init__(self) -> None:
        self.user_text = ""

    async def complete(
        self, request: LanguageModelRequest, cancellation: CancellationToken
    ) -> LanguageModelResponse:
        cancellation.raise_if_cancelled()
        self.user_text = next(
            message.content
            for message in reversed(request.messages)
            if message.role is ConversationRole.USER
        )
        return LanguageModelResponse(text="Okay.", spoken_text="Okay.")

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="capture", available=True, detail="test")


class _CancellationIgnoringLanguageModel(LanguageModelProvider):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self, request: LanguageModelRequest, cancellation: CancellationToken
    ) -> LanguageModelResponse:
        del request, cancellation
        self.entered.set()
        await self.release.wait()
        return LanguageModelResponse(
            tool_calls=[ToolCall(id="late-tool", name="get_current_datetime", arguments={})]
        )

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="ignore-cancel", available=True, detail="test")


async def test_voice_capture_accumulates_final_deepgram_segments(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    model = _CapturingLanguageModel()
    runtime.orchestrator.speech_to_text = _SegmentedSpeechProvider()
    runtime.orchestrator.language_model = model
    await runtime.start()
    try:
        await runtime.orchestrator.start_listening()
        assert runtime.orchestrator._active_task is not None
        await runtime.orchestrator._active_task
        assert model.user_text == "turn on the lights"
    finally:
        await runtime.shutdown()


async def test_cancelled_model_response_cannot_propose_or_execute_tool(
    settings: Settings,
) -> None:
    runtime = AssistantRuntime.create(settings)
    model = _CancellationIgnoringLanguageModel()
    runtime.orchestrator.language_model = model
    await runtime.start()
    try:
        async with runtime.event_bus.subscribe() as queue:
            await runtime.orchestrator.submit_text("wait")
            await model.entered.wait()
            cancellation = asyncio.create_task(runtime.orchestrator.cancel())
            model.release.set()
            await cancellation
            event_types: list[EventType] = []
            while not queue.empty():
                event_types.append((await queue.get()).type)
        assert EventType.TOOL_PROPOSAL not in event_types
        assert all(not item.get("tool_name") for item in await runtime.memory.history())
    finally:
        await runtime.shutdown()


def test_logging_boundary_check_does_not_replace_existing_handlers(tmp_path: Path) -> None:
    root = logging.getLogger()
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        with pytest.raises(ValueError):
            clear_rotating_logs(
                tmp_path.parent / "not-owned",
                data_dir=tmp_path,
                level="INFO",
                max_bytes=100_000,
                backup_count=1,
            )
        assert handler in root.handlers
    finally:
        root.removeHandler(handler)
