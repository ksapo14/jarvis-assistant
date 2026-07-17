from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.config import Settings
from jarvis_assistant.models import (
    ConversationMessage,
    ConversationRole,
    LanguageModelRequest,
    PermissionCategory,
    RiskLevel,
    ToolDescriptor,
)
from jarvis_assistant.providers.base import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderQuotaError,
    ProviderResponseError,
)
from jarvis_assistant.providers.gemini import SYSTEM_PROMPT, GeminiLanguageModelProvider
from jarvis_assistant.runtime import AssistantRuntime


def tool_descriptor(name: str = "get_current_datetime") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="Get time",
        argument_schema={
            "title": "Arguments",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
        permission_category=PermissionCategory.SYSTEM,
        risk_level=RiskLevel.LOW,
        confirmation_required=False,
        timeout_seconds=5,
    )


def request() -> LanguageModelRequest:
    return LanguageModelRequest(
        messages=[ConversationMessage(role=ConversationRole.USER, content="What time is it?")],
        tools=[tool_descriptor()],
    )


async def test_gemini_parses_structured_function_call() -> None:
    async def handler(request_message: httpx.Request) -> httpx.Response:
        assert request_message.headers["x-goog-api-key"] == "secret"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "get_current_datetime",
                                        "args": {},
                                    }
                                }
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiLanguageModelProvider("secret", client=client)
    response = await provider.complete(request(), CancellationToken())
    assert response.tool_calls[0].name == "get_current_datetime"
    await client.aclose()


async def test_gemini_rejects_unknown_tool_call() -> None:
    transport = httpx.MockTransport(
        lambda request_message: httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "arbitrary_shell", "args": {}}}]
                        }
                    }
                ]
            },
        )
    )
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiLanguageModelProvider("secret", client=client)
    with pytest.raises(ProviderResponseError, match="unknown or disabled"):
        await provider.complete(request(), CancellationToken())
    await client.aclose()


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, ProviderAuthenticationError), (429, ProviderQuotaError)],
)
async def test_gemini_maps_auth_and_quota_errors(
    status_code: int, error_type: type[Exception]
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request_message: httpx.Response(status_code, json={"error": "failed"})
        )
    )
    provider = GeminiLanguageModelProvider("secret", client=client, max_retries=0)
    with pytest.raises(error_type):
        await provider.complete(request(), CancellationToken())
    await client.aclose()


async def test_gemini_reports_safe_actionable_client_error() -> None:
    private_content = "the user's private request"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request_message: httpx.Response(
                400,
                json={
                    "error": {
                        "status": "INVALID_ARGUMENT",
                        "message": (
                            'Unknown name "additionalProperties" at '
                            f"'tools[0].function_declarations[0]': {private_content}"
                        ),
                    }
                },
            )
        )
    )
    provider = GeminiLanguageModelProvider("secret", client=client, max_retries=0)
    with pytest.raises(ProviderError) as caught:
        await provider.complete(request(), CancellationToken())
    detail = str(caught.value)
    assert detail == (
        "Gemini request failed with HTTP 400 (INVALID_ARGUMENT): "
        "unsupported tool-schema field additionalProperties"
    )
    assert private_content not in detail
    assert "secret" not in detail
    await client.aclose()


def test_gemini_does_not_reflect_unrecognized_error_content() -> None:
    response = httpx.Response(
        400,
        json={
            "error": {
                "status": "PRIVATE_REQUEST_CONTENT",
                "message": "arbitrary private response content",
            }
        },
    )
    assert GeminiLanguageModelProvider._client_error_message(response) == (
        "Gemini request failed with HTTP 400"
    )


@pytest.mark.parametrize("malformed_status", [["INVALID_ARGUMENT"], {"code": "INVALID_ARGUMENT"}])
def test_gemini_ignores_non_string_error_status(malformed_status: object) -> None:
    response = httpx.Response(
        400,
        json={"error": {"status": malformed_status, "message": "private content"}},
    )
    assert GeminiLanguageModelProvider._client_error_message(response) == (
        "Gemini request failed with HTTP 400"
    )


def test_gemini_removes_additional_properties_recursively() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "options": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "properties": {
                    "nested": {
                        "type": "object",
                        "additionalProperties": False,
                    }
                },
            }
        },
    }
    cleaned = GeminiLanguageModelProvider._clean_schema(schema)
    assert "additionalProperties" not in str(cleaned)
    assert cleaned["properties"]["options"]["properties"]["nested"]["type"] == "object"


def test_gemini_full_registry_payload_uses_supported_object_schema_dialect() -> None:
    data_dir = Path.cwd()
    settings = Settings(
        _env_file=None,
        ASSISTANT_ENV="test",
        ASSISTANT_MOCK_MODE=True,
        ASSISTANT_DATA_DIR=data_dir,
        ASSISTANT_SESSION_TOKEN="test-session-token-that-is-at-least-32-chars",
        WAKE_WORD_ENABLED=False,
        ALLOWED_FILE_ROOTS_JSON=f'["{data_dir.as_posix()}"]',
    )
    runtime = AssistantRuntime.create(settings)
    request_payload = GeminiLanguageModelProvider("secret")._build_payload(
        LanguageModelRequest(
            messages=[ConversationMessage(role=ConversationRole.USER, content="help")],
            tools=runtime.registry.descriptors(),
        )
    )
    declarations = request_payload["tools"][0]["functionDeclarations"]
    assert declarations
    assert "additionalProperties" not in str(declarations)


def test_gemini_prompt_contains_core_safety_rules() -> None:
    lowered = SYSTEM_PROMPT.casefold()
    assert "only" in lowered and "registered tools" in lowered
    assert "never claim an action succeeded" in lowered
    assert "never generate" in lowered and "powershell" in lowered


def test_gemini_malformed_response_is_rejected() -> None:
    with pytest.raises(ProviderResponseError):
        GeminiLanguageModelProvider._parse_response({"candidates": []}, set())


async def test_gemini_configured_status_is_degraded_before_first_request() -> None:
    status = await GeminiLanguageModelProvider("secret", model="gemini-3.1-flash-lite").status()
    assert status.available is False
    assert status.detail == ("Configured for gemini-3.1-flash-lite; connection not yet verified")


async def test_gemini_missing_key_status_is_unavailable() -> None:
    status = await GeminiLanguageModelProvider(None).status()
    assert status.available is False
    assert status.detail == "GEMINI_API_KEY is missing"
