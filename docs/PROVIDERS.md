# Provider adapters

Speech recognition, reasoning, speech synthesis, and wake detection are separate injected interfaces. The orchestrator consumes domain models only; it does not import vendor SDK types or know provider credentials.

## Contracts

```python
class SpeechToTextProvider(ABC):
    async def transcribe(audio, cancellation) -> AsyncIterator[Transcript]: ...
    async def status() -> ProviderStatus: ...

class LanguageModelProvider(ABC):
    async def complete(request, cancellation) -> LanguageModelResponse: ...
    async def status() -> ProviderStatus: ...

class TextToSpeechProvider(ABC):
    async def speak(text, cancellation) -> None: ...
    async def cancel() -> None: ...
    async def status() -> ProviderStatus: ...

class WakeWordProvider(ABC):
    async def detect(pcm16_audio) -> bool: ...
    async def reset() -> None: ...
    async def status() -> ProviderStatus: ...
```

All implementations must honor cancellation, return bounded canonical data, report status without secrets, and translate vendor exceptions into provider error categories.

## Deepgram STT

Selected with:

```dotenv
ASSISTANT_STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=...
```

The adapter opens a Deepgram live WebSocket only after wake/push-to-talk activation. It sends 16 kHz mono linear PCM, enables interim results and endpointing, emits partial/final `Transcript` models, closes on end-of-speech/silence/timeout, and sends no pre-wake buffer.

Failure handling distinguishes authentication, quota/rate limit, network disconnect, silence timeout, malformed messages, and cancellation. Logs include request lifecycle/model—not audio, auth headers, or transcript content by default.

The implementation uses a narrow WebSocket transport instead of exposing an SDK client to the rest of the application, limiting vendor-version impact.

## Willow WIS STT

Selected with:

```dotenv
ASSISTANT_STT_PROVIDER=willow
WILLOW_WIS_URL=http://127.0.0.1:19000/v1/audio/transcriptions
```

The user brief names “Willow for local transcription” while its detailed runtime flow requires Deepgram. Willow is an ecosystem around dedicated voice hardware and a separately deployed Willow Inference Server (WIS), not a drop-in embedded Windows Python model. The adapter therefore supports a user-operated compatible WIS endpoint but is optional and disabled by default.

If WIS is remote, treat it as a network provider and secure it accordingly. The project does not install, authenticate, or maintain that server.

## Gemini reasoning

Used automatically outside mock mode and configured with:

```dotenv
ASSISTANT_LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

The adapter calls Google's documented GenerateContent/function-calling REST schema through `httpx`. This keeps automatic function execution structurally impossible and avoids leaking vendor SDK types into the runtime. The bounded `google-genai` package is installed as a compatibility dependency on architectures where its complete wheel chain is available; Windows ARM64 uses the same official REST surface without it. The adapter supplies a system instruction, bounded context, and declarations for enabled tools. It walks all returned parts and validates either a non-empty bounded response or known function proposals before local policy sees them.

The system instruction requires Gemini to:

- Act as a Windows desktop assistant.
- Use registered tools only.
- Never claim success before a successful tool result.
- Ask for/accept local confirmation policy.
- Keep spoken text concise and keep technical details on screen.
- Explain failures plainly.
- Avoid destructive actions unless requested and locally confirmed.
- Never bypass permissions, confirmations, or enabled-tool filtering.

Local validation remains authoritative even if the prompt is ignored. Retries are bounded and apply to transient failures/rate limits, not malformed or unsafe calls.

## Piper TTS

Selected with:

```dotenv
ASSISTANT_TTS_PROVIDER=piper
PIPER_EXECUTABLE_PATH=C:\Tools\piper\piper.exe
PIPER_MODEL_PATH=C:\Voices\voice.onnx
```

Piper is run locally with an argument array and stdin text. Generated WAV output uses private temporary files, is queued, and can be interrupted. Wake detection remains paused until playback stops. Rate and volume settings are validated and applied locally. Long technical details are reduced to a concise spoken summary by the orchestrator while the UI receives full text.

Piper and voice models are not bundled. Run `scripts/install-piper.ps1`, obtain a compatible voice plus metadata, and review its license. A missing binary/model produces provider status and a UI error; the response remains readable.

## openWakeWord

Selected with:

```dotenv
ASSISTANT_WAKE_PHRASE=hey jarvis
ASSISTANT_WAKE_SENSITIVITY=0.55
OPENWAKEWORD_MODEL_PATH=
OPENWAKEWORD_MELSPEC_MODEL_PATH=
OPENWAKEWORD_EMBEDDING_MODEL_PATH=
```

openWakeWord consumes local 16 kHz PCM frames. The upstream project provides a trained “Hey Jarvis” model, but its pretrained assets are CC BY-NC-SA 4.0 and are not redistributed in the repository or PyInstaller/Tauri packages. After reviewing those terms, run `scripts/install-wake-model.ps1 -AcceptModelLicense` for a one-time download into per-user application data; the script prints the three external paths to add to `.env`. Packaged mode fails closed if the feature paths are absent. The stock model is not presented as a reliable bare “Jarvis” model; supply compatible external wake and feature models for a custom phrase.

Detection has configurable threshold, cooldown, microphone, and reset behavior. The service pauses while listening/thinking/executing/speaking and restarts only after returning idle. When unavailable, push-to-talk continues to work.

## Mock providers

```dotenv
ASSISTANT_ENV=mock
ASSISTANT_STT_PROVIDER=mock
ASSISTANT_LLM_PROVIDER=mock
ASSISTANT_TTS_PROVIDER=mock
```

Mocks follow the same interfaces and emit the same event sequence. Deterministic command patterns can propose safe and confirmation-requiring tools. Mock speech emits completion without audio hardware. This is the default automated-test path and never uses vendor credits.

Run it with:

```powershell
.\scripts\dev.ps1 -Mock
```

## Add or swap a provider

1. Implement the appropriate abstract interface in `jarvis_assistant.providers`.
2. Keep SDK/client initialization inside the adapter.
3. Add configuration using `SecretStr` for credentials and forbid unknown values.
4. Add it to the bootstrap provider factory by explicit name.
5. Convert all incoming/outgoing values to domain Pydantic models.
6. Add status checks that never perform expensive work or reveal secrets.
7. Map authentication, quota, transient network, timeout, malformed response, missing asset, and cancellation errors.
8. Mock the transport in automated tests; do not require real credits.
9. Document what leaves the device, retention implications, installation/license requirements, and hardware needs.

For LLMs, never pass Python callables to an automatic function executor. Return proposals to the local registry. For STT, never start a cloud stream before activation. For TTS/wake, make local/remote behavior explicit.
