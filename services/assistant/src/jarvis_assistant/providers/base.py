from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..cancellation import CancellationToken
from ..models import (
    LanguageModelRequest,
    LanguageModelResponse,
    ProviderStatus,
    Transcript,
)


class ProviderError(RuntimeError):
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ProviderAuthenticationError(ProviderError):
    code = "provider_authentication_failed"


class ProviderQuotaError(ProviderError):
    code = "provider_quota_exhausted"


class ProviderResponseError(ProviderError):
    code = "provider_malformed_response"


class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio: AsyncIterator[bytes],
        cancellation: CancellationToken,
    ) -> AsyncIterator[Transcript]:
        """Yield partial and final transcripts for a single activated utterance."""
        if False:
            yield Transcript(text="", is_final=True)

    @abstractmethod
    async def status(self) -> ProviderStatus:
        pass


class LanguageModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        request: LanguageModelRequest,
        cancellation: CancellationToken,
    ) -> LanguageModelResponse:
        pass

    @abstractmethod
    async def status(self) -> ProviderStatus:
        pass


class TextToSpeechProvider(ABC):
    @abstractmethod
    async def speak(self, text: str, cancellation: CancellationToken) -> None:
        pass

    @abstractmethod
    async def cancel(self) -> None:
        pass

    @abstractmethod
    async def status(self) -> ProviderStatus:
        pass


class WakeWordProvider(ABC):
    @abstractmethod
    async def detect(self, pcm16_audio: bytes) -> bool:
        pass

    @abstractmethod
    async def reset(self) -> None:
        pass

    async def configure(self, *, phrase: str, sensitivity: float) -> None:
        """Apply live wake-word configuration when supported by the provider."""
        del phrase, sensitivity
        await self.reset()

    @abstractmethod
    async def status(self) -> ProviderStatus:
        pass
