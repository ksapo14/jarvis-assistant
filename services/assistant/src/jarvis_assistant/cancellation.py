from __future__ import annotations

import asyncio


class OperationCancelled(Exception):
    """Raised when the active assistant operation is cancelled."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled("operation cancelled")

    async def sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=seconds)
        except TimeoutError:
            return
        raise OperationCancelled("operation cancelled")
