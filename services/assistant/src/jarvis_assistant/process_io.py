from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from .cancellation import CancellationToken, OperationCancelled
from .logging_config import redact

_WINDOWS_RUNTIME_ENVIRONMENT = frozenset(
    name.casefold()
    for name in (
        "ALLUSERSPROFILE",
        "APPDATA",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "CommonProgramW6432",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "PSModulePath",
        "SystemDrive",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )
)
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_()]{0,127}$")
_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?:api_?key|authorization|credential|password|passwd|private_?key|secret|token|"
    r"access_?key|session_?key|\bkey\b)",
    re.IGNORECASE,
)


class ProcessTimeoutError(TimeoutError):
    pass


class ProcessOutputLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes


_P = ParamSpec("_P")
_R = TypeVar("_R")


async def run_blocking(
    function: Callable[_P, _R],
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _R:
    """Run local blocking work without detaching it when the async caller is cancelled."""
    future = asyncio.get_running_loop().run_in_executor(
        None,
        partial(function, *args, **kwargs),
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
        with suppress(Exception):
            future.result()
        raise


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Prevent helper processes from creating a visible console on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def is_sensitive_environment_name(name: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return bool(_SENSITIVE_ENVIRONMENT_NAME.search(normalized)) or normalized.casefold().endswith(
        "_key"
    )


def sanitized_child_environment(
    explicit: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the minimal Windows runtime environment plus operation-scoped values."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.casefold() in _WINDOWS_RUNTIME_ENVIRONMENT
        and not is_sensitive_environment_name(name)
    }
    for name, value in (explicit or {}).items():
        if not _ENVIRONMENT_NAME.fullmatch(name) or is_sensitive_environment_name(name):
            raise ValueError(f"unsafe child environment variable name: {name}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"invalid child environment value for {name}")
        for inherited_name in tuple(environment):
            if inherited_name.casefold() == name.casefold():
                environment.pop(inherited_name)
        environment[name] = value
    return environment


def decode_redacted_child_output(value: bytes) -> str:
    """Decode process output and redact both named and known in-process secrets."""
    text = value.decode("utf-8", errors="replace")
    sensitive_values = sorted(
        {
            environment_value
            for name, environment_value in os.environ.items()
            if is_sensitive_environment_name(name) and len(environment_value) >= 4
        },
        key=len,
        reverse=True,
    )
    for sensitive_value in sensitive_values:
        text = text.replace(sensitive_value, "[REDACTED]")
    return str(redact(text)).strip()


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    return next((value for key, value in environment.items() if key.casefold() == expected), None)


async def launch_associated_target(
    target: str,
    cancellation: CancellationToken,
    *,
    timeout_seconds: float = 10,
) -> None:
    """Open a file or URL through a sanitized Windows association broker."""
    if os.name != "nt":
        raise RuntimeError("Windows file associations are unavailable")
    environment = sanitized_child_environment()
    system_root = _environment_value(environment, "SystemRoot") or _environment_value(
        environment, "WINDIR"
    )
    executable = (
        Path(system_root) / "System32" / "rundll32.exe" if system_root else Path("rundll32.exe")
    )
    if not executable.is_file():
        located = shutil.which("rundll32.exe", path=_environment_value(environment, "PATH"))
        if not located:
            raise RuntimeError("the Windows association launcher is unavailable")
        executable = Path(located)
    cancellation.raise_if_cancelled()
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "url.dll,FileProtocolHandler",
        target,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        **hidden_subprocess_kwargs(),
    )
    output = await collect_process_output(
        process,
        cancellation,
        timeout_seconds=timeout_seconds,
        max_output_bytes=16_384,
    )
    if output.returncode != 0:
        detail = decode_redacted_child_output(output.stderr)
        raise RuntimeError(detail or f"association launcher exited with {output.returncode}")


async def collect_process_output(
    process: asyncio.subprocess.Process,
    cancellation: CancellationToken,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> BoundedProcessOutput:
    """Read both pipes incrementally and terminate the process on any hard bound."""
    if process.stdout is None or process.stderr is None:
        raise ValueError("process stdout and stderr must be pipes")
    limit_reached = asyncio.Event()

    async def read_bounded(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        buffer = bytearray()
        while True:
            chunk = await stream.read(8_192)
            if not chunk:
                return bytes(buffer), False
            remaining = max_output_bytes - len(buffer)
            if len(chunk) > remaining:
                if remaining > 0:
                    buffer.extend(chunk[:remaining])
                limit_reached.set()
                return bytes(buffer), True
            buffer.extend(chunk)

    stdout_task = asyncio.create_task(read_bounded(process.stdout))
    stderr_task = asyncio.create_task(read_bounded(process.stderr))
    process_task = asyncio.create_task(process.wait())
    cancellation_task = asyncio.create_task(cancellation.wait())
    limit_task = asyncio.create_task(limit_reached.wait())
    try:
        done, _pending = await asyncio.wait(
            {process_task, cancellation_task, limit_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            await terminate_process_tree(process)
            await _settle_readers(stdout_task, stderr_task)
            raise ProcessTimeoutError("process timed out")
        if cancellation_task in done:
            await terminate_process_tree(process)
            await _settle_readers(stdout_task, stderr_task)
            raise OperationCancelled("process cancelled")
        if limit_task in done:
            await terminate_process_tree(process)
            await _settle_readers(stdout_task, stderr_task)
            raise ProcessOutputLimitExceeded(
                f"process output exceeded {max_output_bytes} bytes per stream"
            )

        stdout, stdout_limited = await stdout_task
        stderr, stderr_limited = await stderr_task
        if stdout_limited or stderr_limited:
            await terminate_process_tree(process)
            raise ProcessOutputLimitExceeded(
                f"process output exceeded {max_output_bytes} bytes per stream"
            )
        return BoundedProcessOutput(
            returncode=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        for task in (process_task, cancellation_task, limit_task):
            task.cancel()
        for task in (process_task, cancellation_task, limit_task):
            with suppress(asyncio.CancelledError):
                await task
        if process.returncode is None:
            await terminate_process_tree(process)
        await _settle_readers(stdout_task, stderr_task)


async def _settle_readers(*tasks: asyncio.Task[tuple[bytes, bool]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
            await task


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        import psutil
    except ImportError:
        pass
    else:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                with suppress(psutil.Error):
                    child.kill()
        except psutil.Error:
            pass
    with suppress(ProcessLookupError):
        process.kill()
    with suppress(ProcessLookupError):
        await process.wait()
