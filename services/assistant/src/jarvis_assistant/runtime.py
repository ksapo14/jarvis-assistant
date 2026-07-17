from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import SecretStr

from .audio import AudioCapture, normalize_microphone_device
from .config import Settings
from .confirmations import ConfirmationManager
from .events import EventBus
from .memory import MemoryService
from .models import ProviderStatus
from .orchestrator import AssistantOrchestrator
from .permissions import PermissionManager
from .powershell import PowerShellRunner
from .providers.base import (
    LanguageModelProvider,
    SpeechToTextProvider,
    TextToSpeechProvider,
    WakeWordProvider,
)
from .providers.deepgram import DeepgramSpeechToTextProvider
from .providers.gemini import GeminiLanguageModelProvider
from .providers.mock import (
    MockLanguageModelProvider,
    MockSpeechToTextProvider,
    MockTextToSpeechProvider,
    MockWakeWordProvider,
)
from .providers.openwakeword import OpenWakeWordProvider
from .providers.piper import PiperTextToSpeechProvider
from .providers.willow import WillowSpeechToTextProvider
from .secrets_store import WindowsCredentialStore
from .state import StateMachine
from .tools.registry import ToolRegistry
from .tools.windows import build_windows_tools


@dataclass(slots=True)
class AssistantRuntime:
    settings: Settings
    event_bus: EventBus
    state: StateMachine
    memory: MemoryService
    permissions: PermissionManager
    confirmations: ConfirmationManager
    registry: ToolRegistry
    orchestrator: AssistantOrchestrator
    _started: bool = False

    @classmethod
    def create(cls, settings: Settings | None = None) -> AssistantRuntime:
        settings = settings or Settings()
        _hydrate_provider_secrets(settings)
        event_bus = EventBus()
        state = StateMachine(event_bus)
        memory = MemoryService(settings.database_path)
        permissions = PermissionManager(memory)
        confirmations = ConfirmationManager(
            memory,
            event_bus,
            timeout_seconds=settings.confirmation_timeout_seconds,
        )
        powershell = PowerShellRunner()
        registry = ToolRegistry()
        for tool in build_windows_tools(settings, powershell):
            registry.register(tool)
        speech_to_text = _speech_to_text(settings)
        language_model = _language_model(settings)
        text_to_speech = _text_to_speech(settings)
        wake_word = _wake_word(settings)
        audio_capture = AudioCapture(
            sample_rate=settings.sample_rate,
            device=normalize_microphone_device(settings.microphone_device),
        )
        orchestrator = AssistantOrchestrator(
            settings=settings,
            event_bus=event_bus,
            state=state,
            memory=memory,
            permissions=permissions,
            confirmations=confirmations,
            registry=registry,
            speech_to_text=speech_to_text,
            language_model=language_model,
            text_to_speech=text_to_speech,
            wake_word=wake_word,
            audio_capture=audio_capture,
        )
        return cls(
            settings=settings,
            event_bus=event_bus,
            state=state,
            memory=memory,
            permissions=permissions,
            confirmations=confirmations,
            registry=registry,
            orchestrator=orchestrator,
        )

    async def start(self) -> None:
        if self._started:
            return
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        await self.memory.initialize()
        await self.orchestrator.hydrate_persisted_settings()
        await self.orchestrator.start()
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self.orchestrator.shutdown()
        await self.memory.close()
        self._started = False

    async def provider_statuses(self) -> list[ProviderStatus]:
        return list(
            await asyncio.gather(
                self.orchestrator.speech_to_text.status(),
                self.orchestrator.language_model.status(),
                self.orchestrator.text_to_speech.status(),
                self.orchestrator.wake_word.status(),
            )
        )


def _speech_to_text(settings: Settings) -> SpeechToTextProvider:
    if settings.stt_provider == "mock":
        return MockSpeechToTextProvider()
    if settings.stt_provider == "willow":
        return WillowSpeechToTextProvider(
            settings.willow_endpoint, sample_rate=settings.sample_rate
        )
    return DeepgramSpeechToTextProvider(
        settings.deepgram_api_key.get_secret_value() if settings.deepgram_api_key else None,
        model=settings.deepgram_model,
        sample_rate=settings.sample_rate,
    )


def _hydrate_provider_secrets(settings: Settings) -> None:
    """Read allowlisted provider keys from Credential Manager only when explicitly enabled."""
    if not settings.use_credential_manager:
        return
    if settings.deepgram_api_key is None:
        value = WindowsCredentialStore.get("DEEPGRAM_API_KEY")
        if value:
            settings.deepgram_api_key = SecretStr(value)
    if settings.gemini_api_key is None:
        value = WindowsCredentialStore.get("GEMINI_API_KEY")
        if value:
            settings.gemini_api_key = SecretStr(value)


def _language_model(settings: Settings) -> LanguageModelProvider:
    if settings.llm_provider == "mock":
        return MockLanguageModelProvider()
    return GeminiLanguageModelProvider(
        settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None,
        model=settings.gemini_model,
    )


def _text_to_speech(settings: Settings) -> TextToSpeechProvider:
    if settings.tts_provider == "mock":
        return MockTextToSpeechProvider()
    return PiperTextToSpeechProvider(
        settings.piper_executable_path,
        settings.piper_model_path,
        speech_rate=settings.speech_rate,
        volume=settings.speech_volume,
    )


def _wake_word(settings: Settings) -> WakeWordProvider:
    if settings.mock_mode:
        return MockWakeWordProvider()
    return OpenWakeWordProvider(
        settings.wake_word_model_path,
        melspec_model_path=settings.wake_word_melspec_model_path,
        embedding_model_path=settings.wake_word_embedding_model_path,
        phrase=settings.wake_word_phrase,
        sensitivity=settings.wake_word_sensitivity,
    )
