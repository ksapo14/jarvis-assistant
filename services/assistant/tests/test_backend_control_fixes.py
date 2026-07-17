from __future__ import annotations

import asyncio
import subprocess
import threading
import types
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.config import Settings
from jarvis_assistant.models import (
    LanguageModelRequest,
    LanguageModelResponse,
    ProviderStatus,
    ToolCall,
)
from jarvis_assistant.process_io import hidden_subprocess_kwargs, run_blocking
from jarvis_assistant.providers.base import LanguageModelProvider
from jarvis_assistant.runtime import AssistantRuntime
from jarvis_assistant.tools import desktop
from jarvis_assistant.tools.desktop import (
    OpenApplicationTool,
    WindowActionArguments,
    _perform_window_action,
)


def test_windows_helpers_are_created_without_a_console() -> None:
    assert hidden_subprocess_kwargs()["creationflags"] & subprocess.CREATE_NO_WINDOW


async def test_cancelled_blocking_work_is_joined_before_the_task_settles() -> None:
    started = threading.Event()
    release = threading.Event()
    completed: list[bool] = []

    def blocking_operation() -> None:
        started.set()
        release.wait(timeout=2)
        completed.append(True)

    task = asyncio.create_task(run_blocking(blocking_operation))
    for _attempt in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed == [True]


def test_chrome_resolves_from_trusted_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "Local"
    chrome = local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"test")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr("jarvis_assistant.tools.desktop.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_read_app_paths_registry",
        staticmethod(lambda _name: str(chrome)),
    )

    assert Path(OpenApplicationTool._resolve("Google Chrome")) == chrome.resolve()


def test_chrome_app_path_rejects_untrusted_registry_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    untrusted = tmp_path / "Downloads" / "chrome.exe"
    untrusted.parent.mkdir()
    untrusted.write_bytes(b"test")
    monkeypatch.setattr("jarvis_assistant.tools.desktop.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_read_app_paths_registry",
        staticmethod(lambda _name: str(untrusted)),
    )

    with pytest.raises(Exception, match="outside approved installation folders"):
        OpenApplicationTool._resolve("chrome")


def test_window_geometry_arguments_are_bounded_and_action_specific() -> None:
    values = WindowActionArguments(
        title_contains="Chrome",
        action="move_resize",
        x=-100,
        y=40,
        width=1200,
        height=800,
    )
    assert values.width == 1200
    with pytest.raises(ValidationError, match="move actions require"):
        WindowActionArguments(title_contains="Chrome", action="move")
    with pytest.raises(ValidationError):
        WindowActionArguments(
            title_contains="Chrome",
            action="resize",
            width=50,
            height=800,
        )


def test_move_resize_uses_native_set_window_pos(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    class Function:
        argtypes: list[object] | None = None
        restype: object | None = None

        def __init__(self, result: int = 1) -> None:
            self.result = result

        def __call__(self, *args: object) -> int:
            calls.append(args)
            return self.result

    user32 = types.SimpleNamespace(
        GetForegroundWindow=Function(),
        GetWindowTextLengthW=Function(),
        GetWindowTextW=Function(),
        GetWindowThreadProcessId=Function(),
        IsWindowVisible=Function(),
        ShowWindow=Function(),
        SetForegroundWindow=Function(),
        BringWindowToTop=Function(),
        GetWindowRect=Function(),
        IsIconic=Function(result=0),
        IsZoomed=Function(result=1),
        SetWindowPos=Function(),
        PostMessageW=Function(),
        OpenClipboard=Function(),
        CloseClipboard=Function(),
        EmptyClipboard=Function(),
        GetClipboardData=Function(),
        SetClipboardData=Function(),
    )
    monkeypatch.setattr(desktop, "_find_window", lambda _title: (123, "Chrome"))
    monkeypatch.setattr(desktop.ctypes, "windll", types.SimpleNamespace(user32=user32))
    values = WindowActionArguments(
        title_contains="Chrome",
        action="move_resize",
        x=10,
        y=20,
        width=1000,
        height=700,
    )

    assert _perform_window_action(values) == "Chrome"
    assert calls[-1][0] == 123
    assert calls[-1][2:6] == (10, 20, 1000, 700)
    assert any(call[:2] == (123, 9) for call in calls)


async def test_sensitive_tools_require_explicit_request_intent(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        ordinary_names = {
            descriptor.name
            for descriptor in await runtime.orchestrator._enabled_descriptors("open chrome")
        }
        assert "system_power_action" not in ordinary_names
        assert "close_application" not in ordinary_names

        power_names = {
            descriptor.name
            for descriptor in await runtime.orchestrator._enabled_descriptors("restart my computer")
        }
        assert "system_power_action" in power_names

        result = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(
                id="bad-power",
                name="system_power_action",
                arguments={"action": "shutdown"},
            ),
            CancellationToken(),
            request_text="open chrome",
        )
        assert result.success is False
        assert result.error_code == "request_intent_mismatch"

        negated = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(
                id="negated-power",
                name="system_power_action",
                arguments={"action": "restart"},
            ),
            CancellationToken(),
            request_text="do not restart my computer",
        )
        assert negated.success is False
        assert negated.error_code == "request_intent_mismatch"

        wrong_close_target = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(
                id="wrong-close",
                name="close_application",
                arguments={"application_name": "notepad"},
            ),
            CancellationToken(),
            request_text="close chrome",
        )
        assert wrong_close_target.success is False
        assert wrong_close_target.error_code == "request_intent_mismatch"

        excluded_close_target = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(
                id="excluded-close",
                name="close_application",
                arguments={"application_name": "notepad"},
            ),
            CancellationToken(),
            request_text="close Chrome, not Notepad",
        )
        assert excluded_close_target.success is False
        assert excluded_close_target.error_code == "request_intent_mismatch"

        excluded_pid = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(
                id="excluded-pid",
                name="close_application",
                arguments={"process_id": 999},
            ),
            CancellationToken(),
            request_text="close everything except PID 999",
        )
        assert excluded_pid.success is False
        assert excluded_pid.error_code == "request_intent_mismatch"
    finally:
        await runtime.shutdown()


async def test_quiescing_blocks_new_commands_until_resumed(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        await runtime.orchestrator.quiesce()
        with pytest.raises(Exception, match="stopping"):
            await runtime.orchestrator.submit_text("open chrome")

        await runtime.orchestrator.resume_operations()
        await runtime.orchestrator.submit_text("what time is it")
        active = runtime.orchestrator._active_task
        assert active is not None
        await active
    finally:
        await runtime.shutdown()


async def test_invalid_tool_arguments_return_a_structured_failure(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        result = await runtime.orchestrator._execute_tool(
            uuid4(),
            ToolCall(id="invalid-window", name="manage_window", arguments={"action": "move"}),
            CancellationToken(),
            request_text="move chrome",
        )
        assert result.success is False
        assert result.error_code == "tool_validation_failed"
    finally:
        await runtime.shutdown()


class _IgnoringModel(LanguageModelProvider):
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
            tool_calls=[ToolCall(id="late", name="get_current_datetime", arguments={})]
        )

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="ignoring", available=True, detail="test")


async def test_repeated_cancel_is_prompt_and_late_tools_stay_blocked(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    model = _IgnoringModel()
    runtime.orchestrator.language_model = model
    await runtime.start()
    try:
        await runtime.orchestrator.submit_text("wait")
        await model.entered.wait()
        await asyncio.wait_for(runtime.orchestrator.cancel(), timeout=0.25)
        await asyncio.wait_for(runtime.orchestrator.cancel(), timeout=0.25)
        model.release.set()
        active = runtime.orchestrator._active_task
        assert active is not None
        await asyncio.wait_for(active, timeout=1)
        history = await runtime.memory.history()
        assert len(history) == 1
        assert history[0]["status"] == "cancelled"
        assert "tool_name" not in history[0]
    finally:
        model.release.set()
        await runtime.shutdown()
