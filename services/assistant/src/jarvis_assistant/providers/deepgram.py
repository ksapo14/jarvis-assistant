from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlencode

from ..cancellation import CancellationToken, OperationCancelled
from ..models import ProviderStatus, Transcript
from .base import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderQuotaError,
    ProviderResponseError,
    ProviderUnavailableError,
    SpeechToTextProvider,
)


class DeepgramSpeechToTextProvider(SpeechToTextProvider):
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = "nova-3",
        sample_rate: int = 16_000,
        endpoint: str = "wss://api.deepgram.com/v1/listen",
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._endpoint = endpoint
        self._connector = connector
        self._connection_verified = False

    async def transcribe(
        self, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> AsyncIterator[Transcript]:
        if not self._api_key:
            raise ProviderUnavailableError("DEEPGRAM_API_KEY is not configured")
        query = urlencode(
            {
                "model": self._model,
                "encoding": "linear16",
                "sample_rate": self._sample_rate,
                "channels": 1,
                "interim_results": "true",
                "endpointing": 300,
                "utterance_end_ms": 1000,
                "vad_events": "true",
                "smart_format": "true",
            }
        )
        connector = self._connector
        if connector is None:
            try:
                from websockets.asyncio.client import connect
            except ImportError as exc:
                raise ProviderUnavailableError("websockets is not installed") from exc
            connector = connect
        try:
            async with connector(
                f"{self._endpoint}?{query}",
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=10,
                close_timeout=2,
                max_size=1_000_000,
            ) as websocket:
                self._connection_verified = True
                sender = asyncio.create_task(self._send_audio(websocket, audio, cancellation))
                watcher = asyncio.create_task(self._close_on_cancel(websocket, cancellation))
                receiver: asyncio.Task[Any] | None = None
                try:
                    iterator = websocket.__aiter__()
                    while True:
                        receiver = asyncio.create_task(anext(iterator))
                        supervised = {receiver}
                        if not sender.done():
                            supervised.add(sender)
                        done, _pending = await asyncio.wait(
                            supervised, return_when=asyncio.FIRST_COMPLETED
                        )
                        if sender in done:
                            sender.result()
                            if receiver not in done:
                                await receiver
                        try:
                            raw_message = receiver.result()
                        except StopAsyncIteration:
                            break
                        cancellation.raise_if_cancelled()
                        transcript = self._parse_message(raw_message)
                        if transcript is not None:
                            yield transcript
                            if transcript.speech_final:
                                break
                finally:
                    if receiver is not None:
                        receiver.cancel()
                    sender.cancel()
                    watcher.cancel()
                    await asyncio.gather(
                        *(task for task in (receiver, sender, watcher) if task is not None),
                        return_exceptions=True,
                    )
        except OperationCancelled:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            cancellation.raise_if_cancelled()
            status_code = getattr(exc, "status_code", None)
            response = getattr(exc, "response", None)
            if response is not None:
                status_code = getattr(response, "status_code", status_code)
            if status_code in {401, 403}:
                raise ProviderAuthenticationError("Deepgram rejected the API key") from exc
            if status_code == 429:
                raise ProviderQuotaError("Deepgram quota or rate limit reached") from exc
            raise ProviderUnavailableError("Deepgram connection failed") from exc

    async def _send_audio(
        self, websocket: Any, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> None:
        async for chunk in audio:
            cancellation.raise_if_cancelled()
            if chunk:
                await websocket.send(chunk)
        await websocket.send(json.dumps({"type": "CloseStream"}))

    @staticmethod
    async def _close_on_cancel(websocket: Any, cancellation: CancellationToken) -> None:
        await cancellation.wait()
        await websocket.close(code=1000, reason="cancelled")

    @staticmethod
    def _parse_message(raw_message: str | bytes) -> Transcript | None:
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            payload = json.loads(raw_message)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("Deepgram returned malformed JSON") from exc
        message_type = payload.get("type")
        if message_type == "UtteranceEnd":
            return Transcript(text="", is_final=True, speech_final=True)
        if message_type != "Results":
            return None
        try:
            alternative = payload["channel"]["alternatives"][0]
            text = alternative.get("transcript", "")
            confidence = alternative.get("confidence")
            return Transcript(
                text=text,
                is_final=bool(payload.get("is_final")),
                confidence=confidence,
                speech_final=bool(payload.get("speech_final")),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "Deepgram result did not match its expected schema"
            ) from exc

    async def status(self) -> ProviderStatus:
        if not self._api_key:
            return ProviderStatus(
                name="deepgram", available=False, detail="DEEPGRAM_API_KEY is missing"
            )
        if not self._connection_verified:
            return ProviderStatus(
                name="deepgram",
                available=False,
                detail=f"Configured for {self._model}; connection not yet verified",
            )
        return ProviderStatus(
            name="deepgram", available=True, detail=f"Connected; model: {self._model}"
        )
