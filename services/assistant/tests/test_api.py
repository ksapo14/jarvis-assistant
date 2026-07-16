from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis_assistant.api import create_app
from jarvis_assistant.config import Settings
from jarvis_assistant.runtime import AssistantRuntime


@pytest.fixture
def api_client(settings: Settings) -> Iterator[tuple[TestClient, str]]:
    token = settings.session_token.get_secret_value()
    runtime = AssistantRuntime.create(settings)
    with TestClient(create_app(runtime)) as client:
        yield client, token


def test_http_api_requires_header_not_query_token(
    api_client: tuple[TestClient, str],
) -> None:
    client, token = api_client
    assert client.get("/v1/health").status_code == 401
    assert client.get(f"/v1/health?token={token}").status_code == 401
    assert client.get("/v1/health", headers={"X-Assistant-Token": token}).status_code == 200


def test_websocket_authenticates_in_first_message(
    api_client: tuple[TestClient, str],
) -> None:
    client, token = api_client
    with client.websocket_connect("/v1/events") as websocket:
        websocket.send_json({"type": "authenticate", "token": token})
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "status_changed"
        assert snapshot["payload"]["snapshot"] is True


def test_websocket_rejects_wrong_token(api_client: tuple[TestClient, str]) -> None:
    client, _ = api_client
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/v1/events") as websocket:
            websocket.send_json({"type": "authenticate", "token": "wrong"})
            websocket.receive_json()
    assert error.value.code == 4401


def test_tools_endpoint_persists_policy_and_blocks_high_auto_allow(
    api_client: tuple[TestClient, str],
) -> None:
    client, token = api_client
    headers = {"X-Assistant-Token": token}
    response = client.patch(
        "/v1/tools/open_website",
        headers=headers,
        json={"enabled": False, "permission": "disabled"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    high = client.patch(
        "/v1/tools/delete_path",
        headers=headers,
        json={"permission": "always_allow"},
    )
    assert high.status_code == 400


def test_provider_and_audio_status_endpoints(api_client: tuple[TestClient, str]) -> None:
    client, token = api_client
    headers = {"X-Assistant-Token": token}
    providers = client.get("/v1/providers/status", headers=headers)
    audio = client.get("/v1/audio/devices", headers=headers)
    assert providers.status_code == 200
    assert len(providers.json()) == 4
    assert audio.status_code == 200
    assert "devices" in audio.json()


def test_settings_patch_is_strict_and_supports_desktop_fields(
    api_client: tuple[TestClient, str], settings: Settings
) -> None:
    client, token = api_client
    headers = {"X-Assistant-Token": token}
    response = client.patch(
        "/v1/settings",
        headers=headers,
        json={
            "launch_on_startup": True,
            "wake_phrase": "computer",
            "wake_sensitivity": 0.7,
            "save_conversation_history": False,
            "preferred_applications": {"editor": str((settings.data_dir / "editor.exe").resolve())},
        },
    )
    assert response.status_code == 200
    assert response.json()["wake_phrase"] == "computer"
    assert response.json()["preferred_applications"]["editor"].endswith("editor.exe")
    invalid = client.patch("/v1/settings", headers=headers, json={"unknown_setting": True})
    assert invalid.status_code == 422


def test_mock_text_command_is_accepted(api_client: tuple[TestClient, str]) -> None:
    client, token = api_client
    response = client.post(
        "/v1/command",
        headers={"X-Assistant-Token": token},
        json={"text": "What time is it?"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_clear_data_resets_runtime_settings_and_owned_screenshots(
    api_client: tuple[TestClient, str], settings: Settings
) -> None:
    client, token = api_client
    headers = {"X-Assistant-Token": token}
    assert (
        client.patch(
            "/v1/settings",
            headers=headers,
            json={"developer_mode": True, "launch_on_startup": True},
        ).status_code
        == 200
    )
    screenshots = settings.data_dir / "screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "private.png").write_bytes(b"private")
    response = client.delete("/v1/data", headers=headers)
    assert response.status_code == 200
    assert not screenshots.exists()
    snapshot = client.get("/v1/settings", headers=headers).json()
    assert snapshot["developer_mode"] is False
    assert snapshot["launch_on_startup"] is False
    assert "launch_development_command" not in snapshot["tool_permissions"]


def test_authenticated_shutdown_quiesces_runtime(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    token = settings.session_token.get_secret_value()
    with TestClient(create_app(runtime)) as client:
        assert runtime._started
        response = client.post("/v1/shutdown", headers={"X-Assistant-Token": token})
        assert response.status_code == 200
        assert response.json() == {"shutting_down": True}
        assert not runtime._started
