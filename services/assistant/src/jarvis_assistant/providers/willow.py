from __future__ import annotations

import array
import asyncio
import io
import math
import wave
from collections.abc import AsyncIterator
from contextlib import suppress

import httpx

from ..cancellation import CancellationToken
from ..models import ProviderStatus, Transcript
from .base import ProviderResponseError, ProviderUnavailableError, SpeechToTextProvider


class WillowSpeechToTextProvider(SpeechToTextProvider):
    """Optional local Willow Inference Server adapter using its transcription HTTP endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        sample_rate: int = 16_000,
        client: httpx.AsyncClient | None = None,
        max_audio_bytes: int | None = None,
        max_duration_seconds: float = 20,
        trailing_silence_seconds: float = 0.8,
        silence_rms_threshold: int = 350,
    ) -> None:
        self._endpoint = endpoint
        self._sample_rate = sample_rate
        self._client = client
        self._max_audio_bytes = max_audio_bytes or int(sample_rate * 2 * max_duration_seconds)
        self._trailing_silence_bytes = int(sample_rate * 2 * trailing_silence_seconds)
        self._silence_rms_threshold = silence_rms_threshold
        self._connection_verified = False

    async def transcribe(
        self, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> AsyncIterator[Transcript]:
        chunks: list[bytes] = []
        size = 0
        speech_started = False
        trailing_silence = 0
        async for chunk in audio:
            cancellation.raise_if_cancelled()
            remaining = self._max_audio_bytes - size
            if remaining <= 0:
                break
            bounded_chunk = chunk[:remaining]
            size += len(bounded_chunk)
            chunks.append(bounded_chunk)
            if _pcm16_rms(bounded_chunk) >= self._silence_rms_threshold:
                speech_started = True
                trailing_silence = 0
            elif speech_started:
                trailing_silence += len(bounded_chunk)
                if trailing_silence >= self._trailing_silence_bytes:
                    break
            if size >= self._max_audio_bytes:
                break
        cancellation.raise_if_cancelled()
        if not chunks:
            raise ProviderUnavailableError("Willow capture ended without audio")
        wav_bytes = self._to_wav(b"".join(chunks))
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))
        try:
            request_task = asyncio.create_task(
                client.post(
                    self._endpoint,
                    files={"file": ("command.wav", wav_bytes, "audio/wav")},
                    data={"response_format": "json"},
                )
            )
            cancel_task = asyncio.create_task(cancellation.wait())
            done, _ = await asyncio.wait(
                {request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done:
                request_task.cancel()
                cancellation.raise_if_cancelled()
            cancel_task.cancel()
            response = await request_task
            response.raise_for_status()
            self._connection_verified = True
            payload = response.json()
            text = payload.get("text") or payload.get("transcript")
            if not isinstance(text, str):
                raise ProviderResponseError("Willow response did not contain a transcript")
            yield Transcript(text=text, is_final=True, confidence=None, speech_final=True)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Willow inference server request failed") from exc
        finally:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            if request_task.cancelled():
                with suppress(asyncio.CancelledError):
                    await request_task
            if owns_client:
                await client.aclose()

    def _to_wav(self, pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm)
        return output.getvalue()

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            name="willow",
            available=self._connection_verified,
            detail=(
                f"Connected to local endpoint: {self._endpoint}"
                if self._connection_verified
                else f"Configured local endpoint; connection not yet verified: {self._endpoint}"
            ),
        )


def _pcm16_rms(chunk: bytes) -> float:
    usable_length = len(chunk) - (len(chunk) % 2)
    if usable_length == 0:
        return 0
    samples = array.array("h")
    samples.frombytes(chunk[:usable_length])
    if not samples:
        return 0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))
