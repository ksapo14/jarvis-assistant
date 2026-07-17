from __future__ import annotations

import asyncio
import json
import re
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .cancellation import CancellationToken
from .process_io import (
    ProcessOutputLimitExceeded,
    ProcessTimeoutError,
    collect_process_output,
    decode_redacted_child_output,
    sanitized_child_environment,
)


class PowerShellError(RuntimeError):
    pass


class PowerShellValidationError(PowerShellError):
    pass


class PowerShellOperationDenied(PowerShellError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDirectoryArguments(StrictModel):
    path: str = Field(min_length=1, max_length=1_024)
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("path contains a prohibited character")
        return value


class GetProcessesArguments(StrictModel):
    limit: int = Field(default=100, ge=1, le=500)


class GetSystemInformationArguments(StrictModel):
    pass


class OpenApplicationArguments(StrictModel):
    executable: str = Field(min_length=1, max_length=1_024)

    @field_validator("executable")
    @classmethod
    def safe_executable(cls, value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("executable contains a prohibited character")
        path = Path(value)
        if path.is_absolute():
            if path.suffix.casefold() != ".exe":
                raise ValueError("application path must refer to an .exe file")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]+\.exe", value):
            raise ValueError("application name is invalid")
        return value


class PowerAction(StrEnum):
    SHUTDOWN = "shutdown"
    RESTART = "restart"
    SLEEP = "sleep"
    SIGN_OUT = "sign_out"


class ShutdownArguments(StrictModel):
    action: PowerAction


class OperationSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    arguments_model: type[BaseModel]
    script: str
    environment_fields: dict[str, str]
    requires_confirmation: bool = False


class PowerShellResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


class PowerShellRunner:
    MAX_OUTPUT_BYTES: ClassVar[int] = 65_536
    _OPERATIONS: ClassVar[dict[str, OperationSpec]] = {
        "list_directory": OperationSpec(
            arguments_model=ListDirectoryArguments,
            script=(
                "$ErrorActionPreference='Stop'; $p=$env:JARVIS_PATH; $n=[int]$env:JARVIS_LIMIT; "
                "@(Get-ChildItem -LiteralPath $p | Select-Object -First $n Name,FullName,Length,"
                "LastWriteTime,PSIsContainer) | ConvertTo-Json -Compress -Depth 3"
            ),
            environment_fields={"path": "JARVIS_PATH", "limit": "JARVIS_LIMIT"},
        ),
        "get_processes": OperationSpec(
            arguments_model=GetProcessesArguments,
            script=(
                "$ErrorActionPreference='Stop'; $n=[int]$env:JARVIS_LIMIT; "
                "@(Get-Process | Sort-Object ProcessName | Select-Object -First $n "
                "Id,ProcessName,MainWindowTitle) | ConvertTo-Json -Compress -Depth 3"
            ),
            environment_fields={"limit": "JARVIS_LIMIT"},
        ),
        "get_system_information": OperationSpec(
            arguments_model=GetSystemInformationArguments,
            script=(
                "$ErrorActionPreference='Stop'; Get-CimInstance Win32_OperatingSystem | "
                "Select-Object Caption,Version,BuildNumber,OSArchitecture,LastBootUpTime | "
                "ConvertTo-Json -Compress"
            ),
            environment_fields={},
        ),
        "open_application": OperationSpec(
            arguments_model=OpenApplicationArguments,
            script=(
                "$ErrorActionPreference='Stop'; $p=Start-Process -FilePath $env:JARVIS_EXECUTABLE "
                "-PassThru; @{pid=$p.Id; executable=$env:JARVIS_EXECUTABLE} | ConvertTo-Json -Compress"
            ),
            environment_fields={"executable": "JARVIS_EXECUTABLE"},
        ),
        "shutdown_computer": OperationSpec(
            arguments_model=ShutdownArguments,
            script=(
                "$ErrorActionPreference='Stop'; switch($env:JARVIS_ACTION){"
                "'shutdown'{shutdown.exe /s /t 0};'restart'{shutdown.exe /r /t 0};"
                "'sleep'{rundll32.exe powrprof.dll,SetSuspendState 0,1,0};"
                "'sign_out'{shutdown.exe /l};default{throw 'Invalid action'}}"
            ),
            environment_fields={"action": "JARVIS_ACTION"},
            requires_confirmation=True,
        ),
    }

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("pwsh") or shutil.which("powershell")

    def validate(
        self, operation: str, arguments: dict[str, Any]
    ) -> tuple[OperationSpec, BaseModel]:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", operation):
            raise PowerShellValidationError("invalid operation name")
        spec = self._OPERATIONS.get(operation)
        if spec is None:
            raise PowerShellOperationDenied("PowerShell operation is not allowlisted")
        try:
            validated = spec.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            issues = [
                f"{'.'.join(str(part) for part in error.get('loc', ())) or 'arguments'}: "
                f"{error.get('type', 'invalid')}"
                for error in exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )[:5]
            ]
            raise PowerShellValidationError(
                f"invalid {operation} arguments: {'; '.join(issues)}"
            ) from exc
        return spec, validated

    async def run(
        self,
        operation: str,
        arguments: dict[str, Any],
        cancellation: CancellationToken,
        *,
        timeout_seconds: float = 15,
        confirmed: bool = False,
    ) -> PowerShellResult:
        spec, validated = self.validate(operation, arguments)
        if spec.requires_confirmation and not confirmed:
            raise PowerShellOperationDenied("operation requires explicit confirmation")
        if not self._executable:
            raise PowerShellError("PowerShell executable was not found")
        values = validated.model_dump(mode="json")
        operation_environment: dict[str, str] = {}
        for field, environment_name in spec.environment_fields.items():
            operation_environment[environment_name] = str(values[field])
        environment = sanitized_child_environment(operation_environment)
        cancellation.raise_if_cancelled()
        process = await asyncio.create_subprocess_exec(
            self._executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            spec.script,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            output = await collect_process_output(
                process,
                cancellation,
                timeout_seconds=timeout_seconds,
                max_output_bytes=self.MAX_OUTPUT_BYTES,
            )
        except ProcessTimeoutError as exc:
            raise TimeoutError(f"PowerShell operation {operation} timed out") from exc
        except ProcessOutputLimitExceeded as exc:
            raise PowerShellError(
                f"PowerShell operation {operation} exceeded the output limit"
            ) from exc
        return PowerShellResult(
            exit_code=output.returncode,
            stdout=decode_redacted_child_output(output.stdout),
            stderr=decode_redacted_child_output(output.stderr),
            truncated=False,
        )

    @staticmethod
    def parse_json_output(result: PowerShellResult) -> Any:
        if result.exit_code != 0:
            raise PowerShellError(result.stderr or f"PowerShell exited with {result.exit_code}")
        try:
            return json.loads(result.stdout) if result.stdout else None
        except json.JSONDecodeError as exc:
            raise PowerShellError("PowerShell returned malformed structured output") from exc
