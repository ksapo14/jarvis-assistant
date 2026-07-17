from __future__ import annotations

import httpx
import pytest

from jarvis_assistant.cancellation import CancellationToken
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
    ProviderQuotaError,
    ProviderResponseError,
)
from jarvis_assistant.providers.gemini import SYSTEM_PROMPT, GeminiLanguageModelProvider


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


def test_gemini_prompt_contains_core_safety_rules() -> None:
    lowered = SYSTEM_PROMPT.casefold()
    assert "only" in lowered and "registered tools" in lowered
    assert "never claim an action succeeded" in lowered
    assert "never generate" in lowered and "powershell" in lowered


def test_gemini_malformed_response_is_rejected() -> None:
    with pytest.raises(ProviderResponseError):
        GeminiLanguageModelProvider._parse_response({"candidates": []}, set())
