from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

logger = logging.getLogger(__name__)

ParentIdentityProbe = Callable[[int], float | None]


class ParentProbeUnavailable(RuntimeError):
    pass


class ParentProcessWatchdog:
    """Detect parent exit or PID reuse by comparing immutable process creation time."""

    def __init__(
        self,
        parent_pid: int,
        *,
        identity_probe: ParentIdentityProbe | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if parent_pid <= 0:
            raise ValueError("parent PID must be positive")
        self.parent_pid = parent_pid
        self._identity_probe = identity_probe or _process_creation_time
        self._poll_interval_seconds = poll_interval_seconds

    async def run(self, on_parent_lost: Callable[[], Awaitable[None]]) -> None:
        try:
            expected_identity = await asyncio.to_thread(self._identity_probe, self.parent_pid)
        except ParentProbeUnavailable:
            logger.warning("managed parent watchdog is unavailable")
            return
        if expected_identity is None:
            await on_parent_lost()
            return
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                current_identity = await asyncio.to_thread(self._identity_probe, self.parent_pid)
            except ParentProbeUnavailable:
                logger.warning("managed parent watchdog became unavailable")
                return
            if current_identity is None or current_identity != expected_identity:
                logger.info(
                    "managed parent process disappeared",
                    extra={"parent_pid": self.parent_pid},
                )
                await on_parent_lost()
                return


def _process_creation_time(process_id: int) -> float | None:
    try:
        import psutil
    except ImportError as exc:
        raise ParentProbeUnavailable("psutil is unavailable") from exc
    try:
        return float(psutil.Process(process_id).create_time())
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


async def cancel_watchdog(task: asyncio.Task[None] | None) -> None:
    if task is None or task is asyncio.current_task():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
