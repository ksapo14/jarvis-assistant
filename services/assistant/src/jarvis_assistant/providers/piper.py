from __future__ import annotations

import array
import asyncio
import contextlib
import os
import sys
import tempfile
import wave
from pathlib import Path

from ..cancellation import CancellationToken, OperationCancelled
from ..models import ProviderStatus
from ..process_io import (
    ProcessOutputLimitExceeded,
    ProcessTimeoutError,
    collect_process_output,
    decode_redacted_child_output,
    sanitized_child_environment,
    terminate_process_tree,
)
from .base import ProviderUnavailableError, TextToSpeechProvider


class PiperTextToSpeechProvider(TextToSpeechProvider):
    def __init__(
        self,
        executable_path: Path | None,
        model_path: Path | None,
        *,
        speech_rate: float = 1.0,
        volume: float = 1.0,
    ) -> None:
        self._executable_path = executable_path
        self._model_path = model_path
        self._speech_rate = speech_rate
        self._volume = volume
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._playback_stop = asyncio.Event()

    async def speak(self, text: str, cancellation: CancellationToken) -> None:
        if not text.strip():
            return
        self._validate_paths()
        async with self._lock:
            cancellation.raise_if_cancelled()
            self._playback_stop.clear()
            output_path: Path | None = None
            try:
                descriptor, filename = tempfile.mkstemp(prefix="jarvis-", suffix=".wav")
                os.close(descriptor)
                output_path = Path(filename)
                self._process = await asyncio.create_subprocess_exec(
                    str(self._executable_path),
                    "--model",
                    str(self._model_path),
                    "--output_file",
                    str(output_path),
                    "--length_scale",
                    str(1 / self._speech_rate),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=sanitized_child_environment(),
                )
                if self._process.stdin is None:
                    raise ProviderUnavailableError("Piper input pipe was unavailable")
                self._process.stdin.write(text.encode("utf-8"))
                await self._process.stdin.drain()
                self._process.stdin.close()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await self._process.stdin.wait_closed()
                try:
                    output = await collect_process_output(
                        self._process,
                        cancellation,
                        timeout_seconds=60,
                        max_output_bytes=65_536,
                    )
                except ProcessTimeoutError as exc:
                    raise ProviderUnavailableError("Piper timed out") from exc
                except ProcessOutputLimitExceeded as exc:
                    raise ProviderUnavailableError("Piper exceeded its output limit") from exc
                if output.returncode != 0:
                    detail = decode_redacted_child_output(output.stderr)[-500:]
                    raise ProviderUnavailableError(f"Piper failed: {detail}")
                cancellation.raise_if_cancelled()
                await asyncio.to_thread(self._scale_wav_volume, output_path, self._volume)
                cancellation.raise_if_cancelled()
                await self._play(output_path, cancellation)
            finally:
                self._process = None
                if output_path is not None:
                    with contextlib.suppress(OSError):
                        output_path.unlink()

    async def _play(self, path: Path, cancellation: CancellationToken) -> None:
        try:
            import winsound
        except ImportError as exc:
            raise ProviderUnavailableError("Windows audio playback is unavailable") from exc
        duration = self._duration(path)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        playback = asyncio.create_task(asyncio.sleep(duration + 0.1))
        cancelled = asyncio.create_task(cancellation.wait())
        stopped = asyncio.create_task(self._playback_stop.wait())
        done, pending = await asyncio.wait(
            {playback, cancelled, stopped}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if cancelled in done or stopped in done:
            winsound.PlaySound(None, winsound.SND_PURGE)
        if cancelled in done:
            raise OperationCancelled("speech cancelled")

    @staticmethod
    def _duration(path: Path) -> float:
        with wave.open(str(path), "rb") as wav_file:
            return wav_file.getnframes() / max(wav_file.getframerate(), 1)

    async def cancel(self) -> None:
        self._playback_stop.set()
        if self._process is not None and self._process.returncode is None:
            await terminate_process_tree(self._process)

    def update(
        self,
        *,
        speech_rate: float | None = None,
        volume: float | None = None,
        executable_path: Path | None = None,
        model_path: Path | None = None,
        update_executable_path: bool = False,
        update_model_path: bool = False,
    ) -> None:
        if speech_rate is not None:
            self._speech_rate = speech_rate
        if volume is not None:
            self._volume = volume
        if update_executable_path:
            self._executable_path = executable_path
        if update_model_path:
            self._model_path = model_path

    @staticmethod
    def _scale_wav_volume(path: Path, volume: float) -> bool:
        """Scale 16-bit PCM in place and safely leave unsupported WAVs unchanged."""
        if volume == 1.0:
            return True
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                return False
            parameters = source.getparams()
            frames = source.readframes(source.getnframes())
        samples = array.array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        for index, sample in enumerate(samples):
            samples[index] = max(-32_768, min(32_767, round(sample * volume)))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(path), "wb") as destination:
            destination.setparams(parameters)
            destination.writeframes(samples.tobytes())
        return True

    def _validate_paths(self) -> None:
        if self._executable_path is None or not self._executable_path.is_file():
            raise ProviderUnavailableError("Piper executable is missing")
        if self._model_path is None or not self._model_path.is_file():
            raise ProviderUnavailableError("Piper voice model is missing")

    async def status(self) -> ProviderStatus:
        if self._executable_path is None or not self._executable_path.is_file():
            return ProviderStatus(
                name="piper", available=False, detail="Piper executable is missing"
            )
        if self._model_path is None or not self._model_path.is_file():
            return ProviderStatus(name="piper", available=False, detail="Piper model is missing")
        return ProviderStatus(name="piper", available=True, detail=self._model_path.name)
