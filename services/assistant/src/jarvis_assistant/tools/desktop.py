from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import re
import shutil
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, cast
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..cancellation import CancellationToken
from ..models import PermissionCategory, RiskLevel, ToolCall
from ..powershell import PowerShellRunner
from ..process_io import launch_associated_target, run_blocking
from .base import BaseTool, ToolExecutionError, ToolUnavailableError, ToolValidationError


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class ItemsResult(MessageResult):
    items: list[dict[str, Any]]


class CurrentDateTimeResult(MessageResult):
    local_iso: str
    timezone: str


class WindowInfoResult(MessageResult):
    title: str
    process_id: int
    process_name: str | None


class ClipboardResult(MessageResult):
    text: str


class ScreenshotResult(MessageResult):
    path: str


@dataclass(frozen=True, slots=True)
class WindowTarget:
    handle: int
    process_id: int
    title: str


class EmptyArguments(StrictArguments):
    pass


class GetCurrentDateTimeTool(BaseTool):
    name = "get_current_datetime"
    description = "Get the current local date, time, and timezone."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.LOW
    arguments_model = EmptyArguments
    result_model = CurrentDateTimeResult

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> CurrentDateTimeResult:
        del arguments
        cancellation.raise_if_cancelled()
        current = datetime.now().astimezone()
        timezone_name = current.tzname() or str(current.tzinfo)
        return CurrentDateTimeResult(
            message=f"It is {current.strftime('%A, %B %d at %I:%M %p')}.",
            local_iso=current.isoformat(),
            timezone=timezone_name,
        )


class OpenWebsiteArguments(StrictArguments):
    url: str = Field(min_length=1, max_length=2_048)

    @field_validator("url")
    @classmethod
    def safe_http_url(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("URL contains a prohibited control character")
        parsed = urlparse(value)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
        ):
            raise ValueError("only complete http:// or https:// URLs are allowed")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not allowed")
        return value


class OpenWebsiteTool(BaseTool):
    name = "open_website"
    description = "Open an HTTP or HTTPS website in the default browser."
    permission_category = PermissionCategory.APPLICATIONS
    risk_level = RiskLevel.LOW
    arguments_model = OpenWebsiteArguments
    result_model = MessageResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        url = cast(OpenWebsiteArguments, arguments).url
        cancellation.raise_if_cancelled()
        try:
            await launch_associated_target(url, cancellation)
        except (RuntimeError, TimeoutError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return MessageResult(message=f"Opened {url}.")

    def preview(self, arguments: BaseModel) -> str:
        return f"Open {cast(OpenWebsiteArguments, arguments).url} in the default browser?"


class OpenApplicationArguments(StrictArguments):
    application: str = Field(min_length=1, max_length=1_024)
    resolved_executable: str | None = Field(default=None, json_schema_extra={"internal": True})

    @field_validator("application")
    @classmethod
    def no_command_syntax(cls, value: str) -> str:
        if any(character in value for character in "\x00\r\n"):
            raise ValueError("application contains a prohibited character")
        return value


class OpenApplicationTool(BaseTool):
    name = "open_application"
    description = "Open a known installed Windows application without command-line arguments."
    permission_category = PermissionCategory.APPLICATIONS
    risk_level = RiskLevel.LOW
    arguments_model = OpenApplicationArguments
    result_model = MessageResult

    _ALIASES: ClassVar[dict[str, str]] = {
        "calculator": "calc.exe",
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "notepad": "notepad.exe",
        "paint": "mspaint.exe",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "terminal": "wt.exe",
    }

    def __init__(
        self,
        powershell: PowerShellRunner,
        preferred_applications: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._powershell = powershell
        self._preferred_applications = preferred_applications or (lambda: {})

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(OpenApplicationArguments, arguments)
        bound = values.model_copy(
            update={
                "resolved_executable": self._resolve(
                    values.application, self._preferred_applications()
                )
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        requested = cast(OpenApplicationArguments, arguments).application
        values = cast(OpenApplicationArguments, arguments)
        executable = values.resolved_executable or self._resolve(
            requested, self._preferred_applications()
        )
        if values.resolved_executable is not None:
            executable = self._validated_trusted_executable(executable)
        result = await self._powershell.run(
            "open_application", {"executable": executable}, cancellation, timeout_seconds=10
        )
        if result.exit_code != 0:
            raise ToolExecutionError(result.stderr or "application could not be opened")
        return MessageResult(message=f"Opened {requested}.")

    @classmethod
    def _resolve(
        cls,
        requested: str,
        preferred_applications: dict[str, str] | None = None,
    ) -> str:
        normalized = " ".join(requested.strip().split()).casefold()
        alias = (preferred_applications or {}).get(normalized) or cls._ALIASES.get(normalized)
        candidate = alias or requested
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.exe", candidate):
            resolved = shutil.which(candidate) or cls._resolve_windows_app_path(candidate)
            if resolved is not None:
                path = Path(resolved).resolve(strict=False)
                cls._require_trusted_install_path(path)
                return str(path)
            raise ToolValidationError(f"application was not found: {requested}")
        path = Path(os.path.expandvars(candidate)).expanduser().resolve(strict=False)
        if not path.is_file() or path.suffix.casefold() != ".exe":
            raise ToolValidationError("application must be a known name or an existing .exe")
        cls._require_trusted_install_path(path)
        return str(path)

    @staticmethod
    def _require_trusted_install_path(path: Path) -> None:
        roots = [
            Path(value).resolve(strict=False)
            for name in ("ProgramFiles", "ProgramFiles(x86)", "SystemRoot")
            if (value := os.getenv(name))
        ]
        if local_app_data := os.getenv("LOCALAPPDATA"):
            local_root = Path(local_app_data).resolve(strict=False)
            roots.extend(
                [
                    local_root / "Programs",
                    local_root / "Google" / "Chrome" / "Application",
                ]
            )
        if not any(_is_relative_to(path, root) for root in roots):
            raise ToolValidationError("application path is outside approved installation folders")

    @classmethod
    def _resolve_windows_app_path(cls, executable_name: str) -> str | None:
        if sys.platform != "win32":
            return None
        registry_candidate = cls._read_app_paths_registry(executable_name)
        if registry_candidate is not None:
            path = Path(registry_candidate).resolve(strict=False)
            if path.is_file() and path.suffix.casefold() == ".exe":
                cls._require_trusted_install_path(path)
                return str(path)
        if executable_name.casefold() != "chrome.exe":
            return None
        candidates: list[Path] = []
        for environment_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if value := os.getenv(environment_name):
                candidates.append(Path(value) / "Google" / "Chrome" / "Application" / "chrome.exe")
        for candidate in candidates:
            if candidate.is_file():
                resolved = candidate.resolve(strict=True)
                cls._require_trusted_install_path(resolved)
                return str(resolved)
        return None

    @staticmethod
    def _read_app_paths_registry(executable_name: str) -> str | None:
        try:
            import winreg
        except ImportError:
            return None
        subkey = r"Software\Microsoft\Windows\CurrentVersion\App Paths" f"\\{executable_name}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in (
                winreg.KEY_READ,
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
            ):
                try:
                    with winreg.OpenKey(hive, subkey, 0, access) as key:
                        value, _kind = winreg.QueryValueEx(key, None)
                except OSError:
                    continue
                if isinstance(value, str) and value.strip():
                    return os.path.expandvars(value.strip().strip('"'))
        return None

    @classmethod
    def _validated_trusted_executable(cls, executable: str) -> str:
        path = Path(executable)
        if not path.is_file():
            raise ToolValidationError(
                "the confirmed application executable changed or no longer exists"
            )
        resolved = path.resolve(strict=True)
        cls._require_trusted_install_path(resolved)
        return str(resolved)

    def preview(self, arguments: BaseModel) -> str:
        values = cast(OpenApplicationArguments, arguments)
        target = values.resolved_executable or values.application
        return f"Open the exact application executable {target}?"


class GetActiveWindowTool(BaseTool):
    name = "get_active_window"
    description = "Get the title and process identity of the currently active window."
    permission_category = PermissionCategory.WINDOWS
    risk_level = RiskLevel.LOW
    arguments_model = EmptyArguments
    result_model = WindowInfoResult

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> WindowInfoResult:
        del arguments
        cancellation.raise_if_cancelled()
        return await run_blocking(_active_window_info)


class ListApplicationsArguments(StrictArguments):
    max_results: int = Field(default=100, ge=1, le=300)
    windows_only: bool = True


class ListRunningApplicationsTool(BaseTool):
    name = "list_running_applications"
    description = "List running application names and process IDs without command lines."
    permission_category = PermissionCategory.APPLICATIONS
    risk_level = RiskLevel.LOW
    arguments_model = ListApplicationsArguments
    result_model = ItemsResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> ItemsResult:
        values = cast(ListApplicationsArguments, arguments)
        cancellation.raise_if_cancelled()
        try:
            import psutil
        except ImportError as exc:
            raise ToolUnavailableError("psutil is required to list applications") from exc
        items: list[dict[str, Any]] = []
        for process in psutil.process_iter(["pid", "name"]):
            cancellation.raise_if_cancelled()
            try:
                item: dict[str, Any] = {
                    "pid": int(process.info["pid"]),
                    "name": str(process.info["name"] or "Unknown"),
                }
                if values.windows_only:
                    titles = _window_titles_for_pid(item["pid"])
                    if not titles:
                        continue
                    item["window_titles"] = titles
                items.append(item)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            if len(items) >= values.max_results:
                break
        return ItemsResult(message=f"Found {len(items)} running applications.", items=items)


class SetVolumeArguments(StrictArguments):
    level: int = Field(ge=0, le=100)


class SetVolumeTool(BaseTool):
    name = "set_system_volume"
    description = "Set the Windows master output volume from 0 to 100 percent."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.LOW
    arguments_model = SetVolumeArguments
    result_model = MessageResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        level = cast(SetVolumeArguments, arguments).level
        cancellation.raise_if_cancelled()
        actual = await run_blocking(_set_audio_volume, level)
        if actual == level:
            return MessageResult(message=f"Windows reports master volume at {actual} percent.")
        return MessageResult(
            message=(
                f"Requested {level} percent; Windows reports master volume at {actual} percent."
            )
        )


class SetMuteArguments(StrictArguments):
    muted: bool


class SetMuteTool(BaseTool):
    name = "set_audio_muted"
    description = "Mute or unmute the Windows master output device."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.LOW
    arguments_model = SetMuteArguments
    result_model = MessageResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        muted = cast(SetMuteArguments, arguments).muted
        cancellation.raise_if_cancelled()
        await run_blocking(_set_audio_muted, muted)
        return MessageResult(message="Muted audio." if muted else "Unmuted audio.")


class ReadClipboardTool(BaseTool):
    name = "read_clipboard"
    description = "Read plain text from the Windows clipboard after permission is granted."
    permission_category = PermissionCategory.CLIPBOARD
    risk_level = RiskLevel.LOW
    confirmation_required = True
    arguments_model = EmptyArguments
    result_model = ClipboardResult

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> ClipboardResult:
        del arguments
        cancellation.raise_if_cancelled()
        text = await run_blocking(_read_clipboard_text)
        return ClipboardResult(message="Read clipboard text.", text=text[:100_000])

    def preview(self, arguments: BaseModel) -> str:
        del arguments
        return "Read the current clipboard text? It may contain private information."


class SetClipboardArguments(StrictArguments):
    text: str = Field(max_length=100_000)


class SetClipboardTool(BaseTool):
    name = "set_clipboard"
    description = "Replace the Windows clipboard with plain text."
    permission_category = PermissionCategory.CLIPBOARD
    risk_level = RiskLevel.LOW
    arguments_model = SetClipboardArguments
    result_model = MessageResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        text = cast(SetClipboardArguments, arguments).text
        cancellation.raise_if_cancelled()
        await run_blocking(_set_clipboard_text, text)
        return MessageResult(message=f"Placed {len(text)} characters on the clipboard.")

    def preview(self, arguments: BaseModel) -> str:
        text = cast(SetClipboardArguments, arguments).text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return (
            f"Replace the clipboard with exactly {len(text)} UTF-8 characters (SHA-256 {digest})?"
        )


class TakeScreenshotTool(BaseTool):
    name = "take_screenshot"
    description = "Capture all screens and save the image in the assistant data directory."
    permission_category = PermissionCategory.SCREEN_CAPTURE
    risk_level = RiskLevel.LOW
    confirmation_required = True
    arguments_model = EmptyArguments
    result_model = ScreenshotResult
    timeout_seconds = 15
    enforce_timeout = False

    def __init__(self, output_directory: Path) -> None:
        self._output_directory = output_directory

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> ScreenshotResult:
        del arguments
        cancellation.raise_if_cancelled()
        self._output_directory.mkdir(parents=True, exist_ok=True)
        path = self._output_directory / f"screenshot-{uuid4()}.png"
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise ToolUnavailableError("Pillow is required for screenshots") from exc
        completed = False
        try:
            image = await run_blocking(ImageGrab.grab, all_screens=True)
            cancellation.raise_if_cancelled()
            await run_blocking(image.save, path, "PNG")
            cancellation.raise_if_cancelled()
            completed = True
            return ScreenshotResult(message=f"Saved screenshot to {path}.", path=str(path))
        finally:
            if not completed:
                with contextlib.suppress(OSError):
                    path.unlink()

    def preview(self, arguments: BaseModel) -> str:
        del arguments
        return "Capture an image of everything currently visible on all screens?"


class WindowAction(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    FOCUS = "focus"
    MOVE = "move"
    RESIZE = "resize"
    MOVE_RESIZE = "move_resize"


class WindowActionArguments(StrictArguments):
    title_contains: str = Field(min_length=1, max_length=300)
    action: WindowAction
    x: int | None = Field(default=None, ge=-32_768, le=32_767)
    y: int | None = Field(default=None, ge=-32_768, le=32_767)
    width: int | None = Field(default=None, ge=100, le=16_384)
    height: int | None = Field(default=None, ge=100, le=16_384)

    @model_validator(mode="after")
    def required_geometry(self) -> WindowActionArguments:
        needs_position = self.action in {WindowAction.MOVE, WindowAction.MOVE_RESIZE}
        needs_size = self.action in {WindowAction.RESIZE, WindowAction.MOVE_RESIZE}
        if needs_position and (self.x is None or self.y is None):
            raise ValueError("move actions require both x and y")
        if needs_size and (self.width is None or self.height is None):
            raise ValueError("resize actions require both width and height")
        return self


class ManageWindowTool(BaseTool):
    name = "manage_window"
    description = (
        "Minimize, maximize, restore, focus, move, or resize the topmost matching visible "
        "window by title."
    )
    permission_category = PermissionCategory.WINDOWS
    risk_level = RiskLevel.LOW
    arguments_model = WindowActionArguments
    result_model = MessageResult

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        values = cast(WindowActionArguments, arguments)
        cancellation.raise_if_cancelled()
        title = await run_blocking(_perform_window_action, values)
        return MessageResult(message=f"Applied {values.action.value} to {title}.")


class TypeTextArguments(StrictArguments):
    text: str = Field(min_length=1, max_length=2_000)
    target_window_handle: int | None = Field(default=None, json_schema_extra={"internal": True})
    target_process_id: int | None = Field(default=None, json_schema_extra={"internal": True})
    target_window_title: str | None = Field(default=None, json_schema_extra={"internal": True})

    @field_validator("text")
    @classmethod
    def plain_text_only(cls, value: str) -> str:
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError(
                "control characters such as Enter, Tab, or Escape are not allowed; "
                "use a purpose-built confirmed tool for committing an action"
            )
        return value


class TypeTextTool(BaseTool):
    name = "type_text"
    description = (
        "Append plain Unicode text to the focused editable control using target-addressed "
        "Windows UI Automation."
    )
    permission_category = PermissionCategory.INPUT
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = TypeTextArguments
    result_model = MessageResult

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        target = await run_blocking(_get_active_window_target)
        await run_blocking(_validate_type_text_target, target.process_id)
        values = cast(TypeTextArguments, arguments).model_copy(
            update={
                "target_window_handle": target.handle,
                "target_process_id": target.process_id,
                "target_window_title": target.title,
            }
        )
        return call.model_copy(update={"arguments": values.model_dump(mode="json")}), values

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        values = cast(TypeTextArguments, arguments)
        text = values.text
        cancellation.raise_if_cancelled()
        target = _bound_target(values)
        sent = await run_blocking(_set_text_via_uia_bound, text, target)
        if sent != len(text.encode("utf-16-le")) // 2:
            raise ToolExecutionError("Windows accepted only part of the requested keyboard input")
        return MessageResult(message=f"Typed {len(text)} characters into the active application.")

    def preview(self, arguments: BaseModel) -> str:
        values = cast(TypeTextArguments, arguments)
        text = values.text
        target = _bound_target(values)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return (
            f"Type the exact text shown in the action details into {target.title!r} "
            f"(PID {target.process_id}, HWND {target.handle}; UTF-8 SHA-256 {digest})?"
        )


class ClickControlArguments(StrictArguments):
    control_name: str = Field(min_length=1, max_length=300)
    control_type: Literal["button", "menu_item", "tab_item", "list_item", "any"] = "any"
    timeout_seconds: float = Field(default=3, ge=0.5, le=10)
    target_window_handle: int | None = Field(default=None, json_schema_extra={"internal": True})
    target_process_id: int | None = Field(default=None, json_schema_extra={"internal": True})
    target_window_title: str | None = Field(default=None, json_schema_extra={"internal": True})

    @model_validator(mode="after")
    def restrict_generic_invocation_to_navigation(self) -> ClickControlArguments:
        if self.control_type in {"tab_item", "list_item"}:
            return self
        separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", self.control_name)
        normalized = " ".join(re.findall(r"[a-z0-9]+", separated.casefold()))
        safe_non_committing_controls = {
            "back",
            "cancel",
            "collapse",
            "expand",
            "help",
            "maximize",
            "minimize",
            "mute",
            "options",
            "pause",
            "play",
            "previous",
            "restore",
            "settings",
            "stop",
            "unmute",
        }
        if normalized not in safe_non_committing_controls:
            raise ValueError(
                "generic UI Automation is limited to tab/list selection and an allowlist of "
                "non-committing navigation controls; create a purpose-built high-risk tool "
                "for forms, messages, credentials, purchases, or destructive actions"
            )
        return self


class ClickNamedControlTool(BaseTool):
    name = "click_named_control"
    description = (
        "Use Windows UI Automation to select a named tab/list item or invoke an allowlisted "
        "non-committing navigation control in the active window."
    )
    permission_category = PermissionCategory.INPUT
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = ClickControlArguments
    result_model = MessageResult
    timeout_seconds = 15

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        target = await run_blocking(_get_active_window_target)
        values = cast(ClickControlArguments, arguments).model_copy(
            update={
                "target_window_handle": target.handle,
                "target_process_id": target.process_id,
                "target_window_title": target.title,
            }
        )
        return call.model_copy(update={"arguments": values.model_dump(mode="json")}), values

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        values = cast(ClickControlArguments, arguments)
        cancellation.raise_if_cancelled()
        await run_blocking(_click_named_control, values, _bound_target(values))
        return MessageResult(message=f"Invoked the {values.control_name} control.")

    def preview(self, arguments: BaseModel) -> str:
        values = cast(ClickControlArguments, arguments)
        target = _bound_target(values)
        return (
            f"Invoke the {values.control_name!r} control in {target.title!r} "
            f"(PID {target.process_id}, HWND {target.handle})?"
        )


class CloseApplicationArguments(StrictArguments):
    process_id: int | None = Field(default=None, ge=1)
    application_name: str | None = Field(default=None, min_length=1, max_length=260)
    target_process_ids: list[int] = Field(
        default_factory=list, json_schema_extra={"internal": True}
    )
    target_process_create_times: dict[str, float] = Field(
        default_factory=dict, json_schema_extra={"internal": True}
    )

    @model_validator(mode="after")
    def exactly_one_target(self) -> CloseApplicationArguments:
        if (self.process_id is None) == (self.application_name is None):
            raise ValueError("provide exactly one of process_id or application_name")
        if self.application_name and not re.fullmatch(
            r"[A-Za-z0-9 ._()-]+(?:\.exe)?", self.application_name
        ):
            raise ValueError("application_name contains prohibited characters")
        return self


class CloseApplicationTool(BaseTool):
    name = "close_application"
    description = "Ask a visible application to close normally; it does not force-terminate it."
    permission_category = PermissionCategory.APPLICATIONS
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = CloseApplicationArguments
    result_model = MessageResult

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(CloseApplicationArguments, arguments)
        identities = await run_blocking(_resolve_close_process_identities, values)
        if not identities:
            raise ToolValidationError("no running application matches the requested target")
        bound = values.model_copy(
            update={
                "target_process_ids": sorted(identities),
                "target_process_create_times": {
                    str(process_id): create_time for process_id, create_time in identities.items()
                },
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        values = cast(CloseApplicationArguments, arguments)
        cancellation.raise_if_cancelled()
        count = await run_blocking(_close_application_windows, values)
        if count == 0:
            raise ToolExecutionError("no visible windows matched the requested application")
        return MessageResult(message=f"Sent a normal close request to {count} window(s).")

    def preview(self, arguments: BaseModel) -> str:
        values = cast(CloseApplicationArguments, arguments)
        target = f"PID {values.process_id}" if values.process_id else values.application_name
        return (
            f"Ask {target} to close the exact confirmed process IDs "
            f"and start times {values.target_process_create_times}? "
            "Unsaved work may prompt for attention."
        )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_windows() -> None:
    if sys.platform != "win32":
        raise ToolUnavailableError("this desktop tool is available only on Windows")


def _configure_user32(user32: Any) -> None:
    """Declare pointer-width-safe signatures for every shared User32 call."""
    from ctypes import wintypes

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    if hasattr(user32, "BringWindowToTop"):
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
    if hasattr(user32, "GetWindowRect"):
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
    if hasattr(user32, "IsIconic"):
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
    if hasattr(user32, "IsZoomed"):
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.IsZoomed.restype = wintypes.BOOL
    if hasattr(user32, "SetWindowPos"):
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE


def _configure_kernel32(kernel32: Any) -> None:
    from ctypes import wintypes

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE


def _active_window_info() -> WindowInfoResult:
    _require_windows()
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    _configure_user32(user32)
    handle = user32.GetForegroundWindow()
    if not handle:
        raise ToolExecutionError("Windows did not report an active window")
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, len(buffer))
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    process_name: str | None = None
    try:
        import psutil

        process_name = psutil.Process(process_id.value).name()
    except Exception:
        process_name = None
    return WindowInfoResult(
        message=f"The active window is {buffer.value or 'untitled'}.",
        title=buffer.value,
        process_id=process_id.value,
        process_name=process_name,
    )


def _window_titles_for_pid(process_id: int) -> list[str]:
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    _configure_user32(user32)
    titles: list[str] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(handle: int, parameter: int) -> bool:
        del parameter
        current_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(current_pid))
        if current_pid.value == process_id and user32.IsWindowVisible(handle):
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            if buffer.value:
                titles.append(buffer.value)
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return titles


def _audio_endpoint() -> Any:
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except ImportError as exc:
        raise ToolUnavailableError("pycaw is required to control audio mute") from exc
    speakers = AudioUtilities.GetSpeakers()
    interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))


def _set_audio_volume(level: int) -> int:
    endpoint = _audio_endpoint()
    endpoint.SetMasterVolumeLevelScalar(level / 100, None)
    actual = float(endpoint.GetMasterVolumeLevelScalar())
    if not 0 <= actual <= 1:
        raise ToolExecutionError("Windows returned an invalid master-volume readback")
    return round(actual * 100)


def _set_audio_muted(muted: bool) -> None:
    endpoint = _audio_endpoint()
    endpoint.SetMute(int(muted), None)


def _read_clipboard_text() -> str:
    _require_windows()

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_user32(user32)
    _configure_kernel32(kernel32)
    if not user32.OpenClipboard(None):
        raise ToolExecutionError("clipboard is busy")
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            raise ToolExecutionError("clipboard does not contain plain text")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ToolExecutionError("clipboard data could not be locked")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    _require_windows()

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_user32(user32)
    _configure_kernel32(kernel32)
    encoded = (text + "\x00").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(0x0002, len(encoded))  # GMEM_MOVEABLE
    if not handle:
        raise ToolExecutionError("clipboard memory allocation failed")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise ToolExecutionError("clipboard memory could not be locked")
    ctypes.memmove(pointer, encoded, len(encoded))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise ToolExecutionError("clipboard is busy")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            raise ToolExecutionError("clipboard update failed")
        handle = None
    finally:
        user32.CloseClipboard()


def _find_window(title_contains: str) -> tuple[int, str]:
    _require_windows()
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    _configure_user32(user32)
    needle = title_contains.casefold()
    matches: list[tuple[int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(handle: int, parameter: int) -> bool:
        del parameter
        if user32.IsWindowVisible(handle):
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            if needle in buffer.value.casefold():
                matches.append((handle, buffer.value))
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    if not matches:
        raise ToolExecutionError(f"no visible window title contains {title_contains!r}")
    exact_matches = [match for match in matches if match[1].strip().casefold() == needle.strip()]
    candidates = exact_matches or matches
    foreground = int(user32.GetForegroundWindow() or 0)
    return next((match for match in candidates if int(match[0]) == foreground), candidates[0])


def _perform_window_action(values: WindowActionArguments) -> str:
    handle, title = _find_window(values.title_contains)
    user32 = ctypes.windll.user32
    _configure_user32(user32)
    commands = {WindowAction.MINIMIZE: 6, WindowAction.MAXIMIZE: 3, WindowAction.RESTORE: 9}
    if values.action is WindowAction.FOCUS:
        user32.ShowWindow(handle, 9)
        user32.BringWindowToTop(handle)
        focused = bool(user32.SetForegroundWindow(handle))
        if not focused and int(user32.GetForegroundWindow() or 0) != int(handle):
            raise ToolExecutionError(
                "Windows refused focus; elevated windows cannot be controlled by a normal process"
            )
    elif values.action in commands:
        user32.ShowWindow(handle, commands[values.action])
    else:
        from ctypes import wintypes

        if user32.IsIconic(handle) or user32.IsZoomed(handle):
            user32.ShowWindow(handle, 9)
        rectangle = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            raise ToolExecutionError("Windows could not read the current window position")
        x = values.x if values.x is not None else int(rectangle.left)
        y = values.y if values.y is not None else int(rectangle.top)
        width = values.width if values.width is not None else int(rectangle.right - rectangle.left)
        height = (
            values.height if values.height is not None else int(rectangle.bottom - rectangle.top)
        )
        if not user32.SetWindowPos(handle, None, x, y, width, height, 0x0004 | 0x0010):
            raise ToolExecutionError(
                "Windows refused to move or resize the window; elevated windows may be protected"
            )
    return title


def _set_text_via_uia_bound(text: str, expected: WindowTarget) -> int:
    actual = _get_active_window_target()
    if actual != expected:
        raise ToolValidationError(
            "the active window changed after confirmation; request a fresh confirmation"
        )
    _validate_type_text_target(actual.process_id)
    try:
        import uiautomation as automation
    except ImportError as exc:
        raise ToolUnavailableError("uiautomation is required for safe text input") from exc
    control = automation.GetFocusedControl()
    if control is None or int(control.ProcessId) != expected.process_id:
        raise ToolValidationError(
            "the focused text control changed after confirmation; request a fresh confirmation"
        )
    if not _uia_control_belongs_to_window(control, expected.handle):
        raise ToolValidationError(
            "the focused text control belongs to a different window; request a fresh confirmation"
        )
    if bool(control.IsPassword):
        raise ToolValidationError("generic text input is disabled for password controls")
    pattern = control.GetPattern(automation.PatternId.ValuePattern)
    if pattern is None:
        raise ToolUnavailableError(
            "the focused control does not support safe target-addressed text input"
        )
    if bool(pattern.IsReadOnly):
        raise ToolValidationError("the focused text control is read-only")
    current = str(pattern.Value)
    if len(current) > 100_000:
        raise ToolValidationError("the focused text value is too large for safe append input")
    updated = current + text
    if not pattern.SetValue(updated):
        raise ToolExecutionError("Windows UI Automation rejected the text update")
    if str(pattern.Value) != updated:
        raise ToolExecutionError("the focused control did not retain the complete text update")
    return len(text.encode("utf-16-le")) // 2


def _uia_control_belongs_to_window(control: Any, expected_handle: int) -> bool:
    current = control
    try:
        for _ in range(64):
            if int(current.NativeWindowHandle or 0) == expected_handle:
                return True
            current = current.GetParentControl()
            if current is None:
                return False
    except Exception:
        return False
    return False


_TERMINAL_PROCESS_NAMES = frozenset(
    {
        "alacritty",
        "bash",
        "cmd",
        "conhost",
        "git-bash",
        "hyper",
        "mintty",
        "powershell",
        "pwsh",
        "wezterm",
        "windows terminal",
        "windowsterminal",
        "wsl",
        "wt",
    }
)


def _validate_type_text_target(process_id: int) -> None:
    try:
        import psutil
    except ImportError as exc:
        raise ToolUnavailableError("psutil is required to validate the typing target") from exc
    try:
        process_name = Path(psutil.Process(process_id).name()).stem.casefold()
    except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
        raise ToolValidationError("the typing target process could not be validated") from exc
    if process_name in _TERMINAL_PROCESS_NAMES:
        raise ToolValidationError(
            "generic text input is disabled for terminals and command shells; "
            "use an approved developer operation instead"
        )


def _click_named_control(values: ClickControlArguments, expected: WindowTarget) -> None:
    _require_windows()
    try:
        import uiautomation as automation
    except ImportError as exc:
        raise ToolUnavailableError("uiautomation is required for named control actions") from exc
    actual = _get_active_window_target()
    if actual != expected:
        raise ToolValidationError(
            "the active window changed after confirmation; request a fresh confirmation"
        )
    root = automation.ControlFromHandle(expected.handle)
    search_depth = 20
    constructors = {
        "button": root.ButtonControl,
        "menu_item": root.MenuItemControl,
        "tab_item": root.TabItemControl,
        "list_item": root.ListItemControl,
        "any": root.Control,
    }
    control = constructors[values.control_type](Name=values.control_name, searchDepth=search_depth)
    if not control.Exists(maxSearchSeconds=values.timeout_seconds):
        raise ToolExecutionError("named control was not found in the active window")
    if values.control_type in {"tab_item", "list_item"}:
        selection = control.GetPattern(automation.PatternId.SelectionItemPattern)
        if selection is None or not selection.Select():
            raise ToolExecutionError("the named tab/list item cannot be safely selected")
        return
    invocation = control.GetPattern(automation.PatternId.InvokePattern)
    if invocation is None or not invocation.Invoke():
        raise ToolExecutionError("the allowlisted control cannot be safely invoked")


def _get_active_window_target() -> WindowTarget:
    _require_windows()
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    _configure_user32(user32)
    handle = user32.GetForegroundWindow()
    if not handle:
        raise ToolExecutionError("Windows did not report an active window")
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, len(buffer))
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    return WindowTarget(handle=int(handle), process_id=process_id.value, title=buffer.value)


def _bound_target(values: TypeTextArguments | ClickControlArguments) -> WindowTarget:
    if (
        values.target_window_handle is None
        or values.target_process_id is None
        or values.target_window_title is None
    ):
        raise ToolValidationError(
            "input action is not bound to a confirmed window target; request confirmation again"
        )
    return WindowTarget(
        handle=values.target_window_handle,
        process_id=values.target_process_id,
        title=values.target_window_title,
    )


def _close_application_windows(values: CloseApplicationArguments) -> int:
    _require_windows()
    from ctypes import wintypes

    process_ids = set(values.target_process_ids)
    if process_ids:
        _validate_close_process_identities(values)
    else:
        process_ids = set(_resolve_close_process_identities(values))
    user32 = ctypes.windll.user32
    _configure_user32(user32)
    count = 0
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(handle: int, parameter: int) -> bool:
        nonlocal count
        del parameter
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value in process_ids and user32.IsWindowVisible(handle):
            user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
            count += 1
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return count


def _resolve_close_process_identities(
    values: CloseApplicationArguments,
) -> dict[int, float]:
    if values.process_id is not None:
        if values.process_id == os.getpid():
            raise ToolValidationError("the assistant cannot close itself")
        try:
            import psutil

            process = psutil.Process(values.process_id)
            create_time = float(process.create_time())
        except ImportError as exc:
            raise ToolUnavailableError("psutil is required to validate a process") from exc
        except psutil.NoSuchProcess as exc:
            raise ToolValidationError("the requested process no longer exists") from exc
        except psutil.AccessDenied as exc:
            raise ToolExecutionError("Windows denied access to the process") from exc
        return {values.process_id: create_time}
    try:
        import psutil
    except ImportError as exc:
        raise ToolUnavailableError("psutil is required to target an application by name") from exc
    identities: dict[int, float] = {}
    if values.application_name:
        requested = values.application_name.removesuffix(".exe").casefold()
        for process in psutil.process_iter(["pid", "name"]):
            try:
                actual = str(process.info["name"] or "").removesuffix(".exe").casefold()
                if actual == requested:
                    identities[int(process.info["pid"])] = float(process.create_time())
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    return identities


def _validate_close_process_identities(values: CloseApplicationArguments) -> None:
    try:
        import psutil
    except ImportError as exc:
        raise ToolUnavailableError("psutil is required to validate processes") from exc
    requested_name = (
        values.application_name.removesuffix(".exe").casefold() if values.application_name else None
    )
    for process_id in values.target_process_ids:
        expected_create_time = values.target_process_create_times.get(str(process_id))
        try:
            process = psutil.Process(process_id)
            actual_create_time = float(process.create_time())
            actual_name = process.name().removesuffix(".exe").casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
            raise ToolValidationError(
                "a confirmed process changed or exited; confirm again"
            ) from exc
        if expected_create_time is None or actual_create_time != expected_create_time:
            raise ToolValidationError("a confirmed process identity changed; confirm again")
        if requested_name is not None and actual_name != requested_name:
            raise ToolValidationError("a confirmed application changed; confirm again")
