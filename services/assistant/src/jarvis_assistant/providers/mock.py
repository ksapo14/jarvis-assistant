from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator

from ..cancellation import CancellationToken
from ..models import (
    LanguageModelRequest,
    LanguageModelResponse,
    ProviderStatus,
    ToolCall,
    Transcript,
)
from .base import (
    LanguageModelProvider,
    SpeechToTextProvider,
    TextToSpeechProvider,
    WakeWordProvider,
)


class MockSpeechToTextProvider(SpeechToTextProvider):
    def __init__(self, transcript: str = "What time is it?") -> None:
        self.transcript = transcript

    async def transcribe(
        self, audio: AsyncIterator[bytes], cancellation: CancellationToken
    ) -> AsyncIterator[Transcript]:
        async for _ in audio:
            cancellation.raise_if_cancelled()
            break
        partial = self.transcript[: max(1, len(self.transcript) // 2)]
        yield Transcript(text=partial, is_final=False, confidence=1.0)
        yield Transcript(text=self.transcript, is_final=True, confidence=1.0, speech_final=True)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="mock-stt", available=True, detail="Mock mode")


class MockLanguageModelProvider(LanguageModelProvider):
    def __init__(self, responses: list[LanguageModelResponse] | None = None) -> None:
        self._responses = deque(responses or [])

    async def complete(
        self, request: LanguageModelRequest, cancellation: CancellationToken
    ) -> LanguageModelResponse:
        cancellation.raise_if_cancelled()
        if self._responses:
            return self._responses.popleft()
        latest = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        normalized = latest.casefold()
        tool_names = {tool.name for tool in request.tools}
        if any(message.role == "tool" for message in request.messages):
            tool_message = next(
                message for message in reversed(request.messages) if message.role == "tool"
            )
            return LanguageModelResponse(
                text=f"Done. {tool_message.content}",
                spoken_text="Done.",
                finish_reason="stop",
            )
        if "time" in normalized and "get_current_datetime" in tool_names:
            return LanguageModelResponse(
                tool_calls=[ToolCall(id="mock-time", name="get_current_datetime", arguments={})],
                finish_reason="tool_call",
            )
        return LanguageModelResponse(
            text=f"Mock mode heard: {latest}",
            spoken_text=f"I heard: {latest}",
            finish_reason="stop",
        )

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="mock-gemini", available=True, detail="Mock mode")


class MockTextToSpeechProvider(TextToSpeechProvider):
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, text: str, cancellation: CancellationToken) -> None:
        cancellation.raise_if_cancelled()
        self.spoken.append(text)

    async def cancel(self) -> None:
        return

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="mock-piper", available=True, detail="Mock mode")


class MockWakeWordProvider(WakeWordProvider):
    def __init__(self, detect_next: bool = False) -> None:
        self.detect_next = detect_next

    async def detect(self, pcm16_audio: bytes) -> bool:
        result = self.detect_next and bool(pcm16_audio)
        self.detect_next = False
        return result

    async def reset(self) -> None:
        self.detect_next = False

    async def configure(self, *, phrase: str, sensitivity: float) -> None:
        del phrase, sensitivity
        await self.reset()

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name="mock-wake-word", available=True, detail="Mock mode")
