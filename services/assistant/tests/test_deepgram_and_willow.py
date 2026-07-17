from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.providers.base import (
    ProviderAuthenticationError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from jarvis_assistant.providers.deepgram import DeepgramSpeechToTextProvider
from jarvis_assistant.providers.willow import WillowSpeechToTextProvider


async def empty_audio() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def one_audio_chunk() -> AsyncIterator[bytes]:
    yield b"\x00\x00" * 100


def test_deepgram_parses_partial_and_final_results() -> None:
    partial = DeepgramSpeechToTextProvider._parse_message(
        json.dumps(
            {
                "type": "Results",
                "is_final": False,
                "speech_final": False,
                "channel": {"alternatives": [{"transcript": "hello", "confidence": 0.8}]},
            }
        )
    )
    assert partial is not None
    assert partial.text == "hello"
    assert not partial.is_final


def test_deepgram_rejects_malformed_messages() -> None:
    with pytest.raises(ProviderResponseError):
        DeepgramSpeechToTextProvider._parse_message("not-json")
    with pytest.raises(ProviderResponseError):
        DeepgramSpeechToTextProvider._parse_message(
            json.dumps({"type": "Results", "channel": {"alternatives": []}})
        )


async def test_deepgram_missing_api_key_fails_without_network() -> None:
    provider = DeepgramSpeechToTextProvider(None)
    with pytest.raises(ProviderUnavailableError, match="DEEPGRAM_API_KEY"):
        async for _ in provider.transcribe(empty_audio(), CancellationToken()):
            pass


async def test_deepgram_maps_websocket_auth_failure() -> None:
    class AuthFailure(Exception):
        status_code = 401

    class FailedConnection:
        async def __aenter__(self) -> object:
            raise AuthFailure("no")

        async def __aexit__(self, *arguments: object) -> None:
            return None

    def connector(*arguments: object, **kwargs: object) -> FailedConnection:
        return FailedConnection()

    provider = DeepgramSpeechToTextProvider("secret", connector=connector)
    with pytest.raises(ProviderAuthenticationError):
        async for _ in provider.transcribe(empty_audio(), CancellationToken()):
            pass


async def test_willow_network_error_is_normalized() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request_message: httpx.Response(503, json={"error": "offline"})
        )
    )
    provider = WillowSpeechToTextProvider("http://127.0.0.1:19000/stt", client=client)
    with pytest.raises(ProviderUnavailableError, match="Willow"):
        async for _ in provider.transcribe(one_audio_chunk(), CancellationToken()):
            pass
    await client.aclose()
