from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .cancellation import CancellationToken
from .providers.base import ProviderUnavailableError


class AudioCapture:
    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        device: str | int | None = None,
        block_size: int = 1_280,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.block_size = block_size

    def configure(self, *, device: str | int | None) -> None:
        self.device = normalize_microphone_device(device)

    async def frames(self, cancellation: CancellationToken) -> AsyncIterator[bytes]:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise ProviderUnavailableError("sounddevice/PortAudio is unavailable") from exc
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | Exception] = asyncio.Queue(maxsize=64)

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            if status:
                loop.call_soon_threadsafe(self._queue_item, queue, RuntimeError(str(status)))
            loop.call_soon_threadsafe(self._queue_item, queue, bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=self.device,
                channels=1,
                dtype="int16",
                callback=callback,
            ):
                while not cancellation.cancelled:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.25)
                    except TimeoutError:
                        continue
                    if isinstance(item, Exception):
                        raise ProviderUnavailableError(
                            f"microphone capture failed: {item}"
                        ) from item
                    yield item
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError("microphone is missing, denied, or unavailable") from exc

    @staticmethod
    def _queue_item(queue: asyncio.Queue[bytes | Exception], item: bytes | Exception) -> None:
        if not queue.full():
            queue.put_nowait(item)

    @staticmethod
    async def list_devices() -> list[dict[str, Any]]:
        try:
            import sounddevice as sd
        except (ImportError, OSError):
            return []
        devices = await asyncio.to_thread(sd.query_devices)
        results: list[dict[str, Any]] = []
        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) > 0:
                results.append(
                    {
                        "id": str(index),
                        "name": str(device.get("name", f"Microphone {index}")),
                        "channels": int(device.get("max_input_channels", 0)),
                        "default_sample_rate": int(device.get("default_samplerate", 0)),
                    }
                )
        return results


def normalize_microphone_device(device: str | int | None) -> str | int | None:
    if device is None or isinstance(device, int):
        return device
    normalized = device.strip()
    if not normalized:
        return None
    if normalized.isdecimal():
        return int(normalized)
    return normalized
