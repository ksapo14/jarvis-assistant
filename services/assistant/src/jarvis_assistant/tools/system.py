from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..cancellation import CancellationToken
from ..models import PermissionCategory, RiskLevel, ToolCall
from ..powershell import PowerAction, PowerShellRunner
from ..process_io import (
    ProcessOutputLimitExceeded,
    ProcessTimeoutError,
    collect_process_output,
    decode_redacted_child_output,
    hidden_subprocess_kwargs,
    run_blocking,
    sanitized_child_environment,
)
from .base import BaseTool, ToolExecutionError, ToolUnavailableError, ToolValidationError
from .safe_paths import PathScope


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class StructuredResult(MessageResult):
    data: Any
    output_truncated: bool = False


class PowerShellOperationArguments(StrictArguments):
    operation: Literal["list_directory", "get_processes", "get_system_information"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class RunApprovedPowerShellOperationTool(BaseTool):
    name = "run_approved_powershell_operation"
    description = "Run one named, typed PowerShell operation from a fixed internal allowlist."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = PowerShellOperationArguments
    result_model = StructuredResult
    timeout_seconds = 30

    def __init__(self, powershell: PowerShellRunner, scope: PathScope) -> None:
        self._powershell = powershell
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(PowerShellOperationArguments, arguments)
        if values.operation != "list_directory":
            return call, values
        operation_arguments = dict(values.arguments)
        path = operation_arguments.get("path")
        if not isinstance(path, str):
            raise ToolValidationError("list_directory requires a path string")
        operation_arguments["path"] = str(self._scope.resolve(path, must_exist=True))
        bound = values.model_copy(update={"arguments": operation_arguments})
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> StructuredResult:
        values = cast(PowerShellOperationArguments, arguments)
        operation_arguments = dict(values.arguments)
        if values.operation == "list_directory":
            path = operation_arguments.get("path")
            if not isinstance(path, str):
                raise ToolValidationError("list_directory requires a path string")
            operation_arguments["path"] = str(self._scope.resolve(path, must_exist=True))
        self._powershell.validate(values.operation, operation_arguments)
        result = await self._powershell.run(
            values.operation, operation_arguments, cancellation, timeout_seconds=20
        )
        data = self._powershell.parse_json_output(result)
        return StructuredResult(
            message=f"Completed approved PowerShell operation {values.operation}.",
            data=data,
            output_truncated=result.truncated,
        )

    def preview(self, arguments: BaseModel) -> str:
        values = cast(PowerShellOperationArguments, arguments)
        return f"Run the approved PowerShell operation {values.operation} with {values.arguments}?"


class DevelopmentCommandArguments(StrictArguments):
    command_name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    working_directory: str | None = Field(default=None, max_length=1_024)


class LaunchDevelopmentCommandTool(BaseTool):
    name = "launch_development_command"
    description = "Launch a developer command selected by name from local configuration."
    permission_category = PermissionCategory.DEVELOPMENT
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = DevelopmentCommandArguments
    result_model = StructuredResult
    timeout_seconds = 120

    def __init__(
        self,
        commands: dict[str, list[str]],
        scope: PathScope,
        developer_mode_enabled: Callable[[], bool],
    ) -> None:
        self._commands = commands
        self._scope = scope
        self._developer_mode_enabled = developer_mode_enabled

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(DevelopmentCommandArguments, arguments)
        working_directory = (
            self._scope.resolve(values.working_directory, must_exist=True)
            if values.working_directory
            else self._scope.roots[0]
        )
        bound = values.model_copy(update={"working_directory": str(working_directory)})
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> StructuredResult:
        values = cast(DevelopmentCommandArguments, arguments)
        if not self._developer_mode_enabled():
            raise ToolValidationError("developer mode is disabled")
        command = self._commands.get(values.command_name)
        if not command:
            raise ToolValidationError("development command is not configured or enabled")
        working_directory = (
            self._scope.resolve(values.working_directory, must_exist=True)
            if values.working_directory
            else self._scope.roots[0]
        )
        if not working_directory.is_dir():
            raise ToolValidationError("working directory is not a directory")
        completed = await _run_process(
            command, cancellation, cwd=working_directory, timeout_seconds=110
        )
        if completed[0] != 0:
            raise ToolExecutionError(completed[2] or f"command exited with {completed[0]}")
        return StructuredResult(
            message=f"Development command {values.command_name} completed.",
            data={"exit_code": completed[0], "stdout": completed[1], "stderr": completed[2]},
            output_truncated=completed[3],
        )

    def preview(self, arguments: BaseModel) -> str:
        values = cast(DevelopmentCommandArguments, arguments)
        command = self._commands.get(values.command_name)
        return (
            f"Run configured development command {values.command_name}: "
            f"{command or '[not configured]'} in {values.working_directory or self._scope.roots[0]}?"
        )


class TrustedScriptArguments(StrictArguments):
    script_path: str = Field(min_length=1, max_length=1_024)
    arguments: list[str] = Field(default_factory=list, max_length=32)
    target_script_sha256: str | None = Field(default=None, json_schema_extra={"internal": True})

    @field_validator("arguments")
    @classmethod
    def safe_argument_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1_024 or any(char in value for char in "\x00\r\n") for value in values):
            raise ValueError("script argument is too long or contains a prohibited character")
        return values


class ExecuteTrustedScriptTool(BaseTool):
    name = "execute_trusted_script"
    description = "Execute a .ps1 or .py file from a configured trusted script directory."
    permission_category = PermissionCategory.DEVELOPMENT
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = TrustedScriptArguments
    result_model = StructuredResult
    timeout_seconds = 120

    def __init__(
        self,
        trusted_roots: tuple[Path, ...],
        developer_mode_enabled: Callable[[], bool],
        *,
        python_executable: Path | None = None,
        approved_scripts: tuple[Path, ...] = (),
        writable_roots: tuple[Path, ...] = (),
    ) -> None:
        self._scope = PathScope(trusted_roots) if trusted_roots else None
        self._developer_mode_enabled = developer_mode_enabled
        self._python_executable = python_executable
        self._approved_scripts = frozenset(
            path.expanduser().resolve(strict=False) for path in approved_scripts
        )
        trusted = tuple(path.expanduser().resolve(strict=False) for path in trusted_roots)
        writable = tuple(path.expanduser().resolve(strict=False) for path in writable_roots)
        self._roots_overlap = any(
            _paths_overlap(trusted_root, writable_root)
            for trusted_root in trusted
            for writable_root in writable
        )

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        if self._scope is None:
            raise ToolValidationError("no trusted script directories are configured")
        self._validate_trust_boundary()
        values = cast(TrustedScriptArguments, arguments)
        script = self._scope.resolve(values.script_path, must_exist=True, allow_root=False)
        self._require_approved_script(script)
        digest = await run_blocking(_script_sha256, script)
        bound = values.model_copy(
            update={
                "script_path": str(script),
                "target_script_sha256": digest,
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> StructuredResult:
        values = cast(TrustedScriptArguments, arguments)
        if not self._developer_mode_enabled():
            raise ToolValidationError("developer mode is disabled")
        if self._scope is None:
            raise ToolValidationError("no trusted script directories are configured")
        self._validate_trust_boundary()
        script = self._scope.resolve(values.script_path, must_exist=True, allow_root=False)
        self._require_approved_script(script)
        if values.target_script_sha256 is None:
            raise ToolValidationError("script is not bound to a confirmed content hash")
        with _verified_execution_script(script, values.target_script_sha256) as execution_script:
            if script.suffix.casefold() == ".py":
                configured_python = self._python_executable
                if getattr(sys, "frozen", False) and configured_python is None:
                    raise ToolUnavailableError(
                        "Python scripts are disabled in the packaged backend; configure "
                        "TRUSTED_PYTHON_EXECUTABLE_PATH or use a trusted .ps1 script"
                    )
                python = configured_python or Path(sys.executable)
                if not python.is_file():
                    raise ToolUnavailableError(
                        "the configured trusted Python executable was not found"
                    )
                command = [str(python), str(execution_script), *values.arguments]
            elif script.suffix.casefold() == ".ps1":
                powershell = shutil.which("pwsh") or shutil.which("powershell")
                if not powershell:
                    raise ToolUnavailableError("PowerShell is not installed")
                command = [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(execution_script),
                    *values.arguments,
                ]
            else:
                raise ToolValidationError("only .py and .ps1 scripts are approved")
            completed = await _run_process(
                command, cancellation, cwd=script.parent, timeout_seconds=110
            )
        if completed[0] != 0:
            raise ToolExecutionError(completed[2] or f"script exited with {completed[0]}")
        return StructuredResult(
            message=f"Trusted script {script.name} completed.",
            data={"exit_code": completed[0], "stdout": completed[1], "stderr": completed[2]},
            output_truncated=completed[3],
        )

    def preview(self, arguments: BaseModel) -> str:
        values = cast(TrustedScriptArguments, arguments)
        return (
            f"Run approved script {values.script_path} with SHA-256 "
            f"{values.target_script_sha256} and exact arguments {values.arguments}?"
        )

    def _validate_trust_boundary(self) -> None:
        if self._roots_overlap:
            raise ToolValidationError(
                "trusted script roots may not overlap model-writable file roots"
            )
        if not self._approved_scripts:
            raise ToolValidationError(
                "no exact trusted scripts are configured in TRUSTED_SCRIPT_ALLOWLIST_JSON"
            )

    def _require_approved_script(self, script: Path) -> None:
        if script not in self._approved_scripts:
            raise ToolValidationError("script path is not in the exact trusted-script allowlist")


class TerminateProcessArguments(StrictArguments):
    process_id: int = Field(ge=5)
    target_process_name: str | None = Field(default=None, json_schema_extra={"internal": True})
    target_process_create_time: float | None = Field(
        default=None, json_schema_extra={"internal": True}
    )


class ForceTerminateProcessTool(BaseTool):
    name = "force_terminate_process"
    description = "Forcefully terminate one non-critical process by its exact process ID."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.HIGH
    confirmation_required = True
    arguments_model = TerminateProcessArguments
    result_model = MessageResult

    _CRITICAL_NAMES = frozenset(
        {
            "csrss.exe",
            "dwm.exe",
            "lsass.exe",
            "services.exe",
            "smss.exe",
            "system",
            "wininit.exe",
            "winlogon.exe",
        }
    )

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(TerminateProcessArguments, arguments)
        if values.process_id == os.getpid():
            raise ToolValidationError("the assistant cannot terminate itself")
        name, create_time = await run_blocking(_process_identity, values.process_id)
        if name.casefold() in self._CRITICAL_NAMES:
            raise ToolValidationError(f"refusing to terminate critical process {name}")
        bound = values.model_copy(
            update={
                "target_process_name": name,
                "target_process_create_time": create_time,
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        values = cast(TerminateProcessArguments, arguments)
        process_id = values.process_id
        if process_id == os.getpid():
            raise ToolValidationError("the assistant cannot terminate itself")
        cancellation.raise_if_cancelled()
        try:
            import psutil
        except ImportError as exc:
            raise ToolUnavailableError("psutil is required to terminate a process") from exc
        try:
            process = psutil.Process(process_id)
            name = process.name()
            create_time = process.create_time()
            if (
                values.target_process_name is None
                or values.target_process_create_time is None
                or name != values.target_process_name
                or create_time != values.target_process_create_time
            ):
                raise ToolValidationError(
                    "the process identity changed after confirmation; confirm again"
                )
            if name.casefold() in self._CRITICAL_NAMES:
                raise ToolValidationError(f"refusing to terminate critical process {name}")
            process.kill()
            await run_blocking(process.wait, 5)
        except psutil.NoSuchProcess as exc:
            raise ToolValidationError("process no longer exists") from exc
        except psutil.AccessDenied as exc:
            raise ToolExecutionError(
                "Windows denied termination; elevated processes cannot be controlled normally"
            ) from exc
        return MessageResult(message=f"Forcefully terminated {name} (PID {process_id}).")

    def preview(self, arguments: BaseModel) -> str:
        values = cast(TerminateProcessArguments, arguments)
        return (
            f"Forcefully terminate {values.target_process_name!r} at PID {values.process_id}? "
            f"Confirmed process start time: {values.target_process_create_time}. "
            "Unsaved data may be lost."
        )


class SystemPowerArguments(StrictArguments):
    action: PowerAction


class SystemPowerActionTool(BaseTool):
    name = "system_power_action"
    description = "Shut down, restart, sleep, or sign out of Windows."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.HIGH
    confirmation_required = True
    arguments_model = SystemPowerArguments
    result_model = MessageResult
    timeout_seconds = 20

    def __init__(self, powershell: PowerShellRunner) -> None:
        self._powershell = powershell

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> MessageResult:
        action = cast(SystemPowerArguments, arguments).action
        result = await self._powershell.run(
            "shutdown_computer",
            {"action": action.value},
            cancellation,
            timeout_seconds=10,
            confirmed=True,
        )
        if result.exit_code != 0:
            raise ToolExecutionError(result.stderr or f"Windows refused to {action.value}")
        return MessageResult(message=f"Requested Windows to {action.value.replace('_', ' ')}.")

    def preview(self, arguments: BaseModel) -> str:
        action = cast(SystemPowerArguments, arguments).action.value.replace("_", " ")
        return f"{action.capitalize()} this computer now? Unsaved work may be lost."


class PackageActionArguments(StrictArguments):
    action: Literal["install", "uninstall"]
    package_id: str = Field(min_length=1, max_length=200)

    @field_validator("package_id")
    @classmethod
    def package_identifier_only(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}", value):
            raise ValueError("package_id is not a valid exact winget package identifier")
        return value


class ManageSoftwarePackageTool(BaseTool):
    name = "manage_software_package"
    description = "Install or uninstall one exact winget package ID without arbitrary arguments."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.HIGH
    confirmation_required = True
    arguments_model = PackageActionArguments
    result_model = StructuredResult
    timeout_seconds = 300

    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> StructuredResult:
        values = cast(PackageActionArguments, arguments)
        winget = shutil.which("winget")
        if not winget:
            raise ToolUnavailableError("winget is not installed")
        command = [
            winget,
            values.action,
            "--id",
            values.package_id,
            "--exact",
            "--silent",
            "--disable-interactivity",
            "--accept-source-agreements",
        ]
        if values.action == "install":
            command.append("--accept-package-agreements")
        completed = await _run_process(command, cancellation, timeout_seconds=290)
        if completed[0] != 0:
            raise ToolExecutionError(completed[2] or f"winget exited with {completed[0]}")
        return StructuredResult(
            message=f"Winget {values.action} completed for {values.package_id}.",
            data={"exit_code": completed[0], "stdout": completed[1], "stderr": completed[2]},
            output_truncated=completed[3],
        )

    def preview(self, arguments: BaseModel) -> str:
        values = cast(PackageActionArguments, arguments)
        return f"Use winget to {values.action} the exact package {values.package_id}?"


async def _run_process(
    command: list[str],
    cancellation: CancellationToken,
    *,
    cwd: Path | None = None,
    timeout_seconds: float,
) -> tuple[int, str, str, bool]:
    if not command or any(not isinstance(part, str) or "\x00" in part for part in command):
        raise ToolValidationError("configured process command is invalid")
    cancellation.raise_if_cancelled()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sanitized_child_environment(),
        **hidden_subprocess_kwargs(),
    )
    try:
        output = await collect_process_output(
            process,
            cancellation,
            timeout_seconds=timeout_seconds,
            max_output_bytes=65_536,
        )
    except ProcessTimeoutError as exc:
        raise ToolExecutionError("process timed out") from exc
    except ProcessOutputLimitExceeded as exc:
        raise ToolExecutionError("process exceeded the 65536-byte output limit") from exc
    return (
        output.returncode,
        decode_redacted_child_output(output.stdout),
        decode_redacted_child_output(output.stderr),
        False,
    )


def _process_identity(process_id: int) -> tuple[str, float]:
    try:
        import psutil
    except ImportError as exc:
        raise ToolUnavailableError("psutil is required to inspect a process") from exc
    try:
        process = psutil.Process(process_id)
        return str(process.name()), float(process.create_time())
    except psutil.NoSuchProcess as exc:
        raise ToolValidationError("process no longer exists") from exc
    except psutil.AccessDenied as exc:
        raise ToolExecutionError("Windows denied access to the process") from exc


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        try:
            second.relative_to(first)
            return True
        except ValueError:
            return False


def _script_sha256(path: Path) -> str:
    with _locked_script_snapshot(path) as snapshot:
        return hashlib.sha256(snapshot).hexdigest()


@contextmanager
def _verified_execution_script(path: Path, expected_sha256: str) -> Iterator[Path]:
    """Keep verified bytes immutable until the child interpreter exits."""
    with _locked_script_snapshot(path) as snapshot:
        if hashlib.sha256(snapshot).hexdigest() != expected_sha256:
            raise ToolValidationError(
                "trusted script content changed after confirmation; confirm again"
            )
        if sys.platform == "win32":
            # _locked_script_snapshot retains a CreateFile handle that denies write/delete
            # sharing for the entire yield, so the interpreter opens these exact bytes.
            yield path
            return

        temporary_directory = Path(tempfile.mkdtemp(prefix="jarvis-verified-script-"))
        execution_script = temporary_directory / path.name
        descriptor: int | None = None
        try:
            descriptor = os.open(
                execution_script,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            view = memoryview(snapshot)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("could not write verified script snapshot")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            execution_script.chmod(stat.S_IRUSR)
            yield execution_script
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if execution_script.exists():
                execution_script.chmod(stat.S_IRUSR | stat.S_IWUSR)
            shutil.rmtree(temporary_directory, ignore_errors=True)


@contextmanager
def _locked_script_snapshot(path: Path) -> Iterator[bytes]:
    descriptor: int | None = None
    try:
        descriptor = _open_script_read_locked(path)
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_size > 5_000_000:
            raise ToolValidationError("approved script must be a regular file no larger than 5 MB")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > 5_000_000:
                raise ToolValidationError(
                    "approved script must be a regular file no larger than 5 MB"
                )
            chunks.append(chunk)
        yield b"".join(chunks)
    except ToolValidationError:
        raise
    except OSError as exc:
        raise ToolValidationError("approved script is unavailable or currently writable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_script_read_locked(path: Path) -> int:
    if sys.platform != "win32":
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    handle = create_file(
        str(path),
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW could not lock the approved script")
    try:
        binary = getattr(os, "O_BINARY", 0)
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | binary)
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise
