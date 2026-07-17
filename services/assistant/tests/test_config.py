from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis_assistant.config import Settings
from jarvis_assistant.models import SettingPatch
from jarvis_assistant.runtime import _hydrate_provider_secrets
from jarvis_assistant.secrets_store import WindowsCredentialStore


def test_mock_mode_selects_all_mock_providers(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        ASSISTANT_MOCK_MODE=True,
        ASSISTANT_DATA_DIR=tmp_path,
        ASSISTANT_SESSION_TOKEN="x" * 32,
    )
    assert settings.stt_provider == "mock"
    assert settings.llm_provider == "mock"
    assert settings.tts_provider == "mock"


def test_non_loopback_bind_is_rejected() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(
            _env_file=None,
            ASSISTANT_HOST="0.0.0.0",
            ASSISTANT_SESSION_TOKEN="x" * 32,
        )


def test_weak_session_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(_env_file=None, ASSISTANT_SESSION_TOKEN="short")


def test_allowed_roots_and_commands_are_strict_json(tmp_path: Path) -> None:
    commands = {"tests": ["python", "-m", "pytest"]}
    settings = Settings(
        _env_file=None,
        ASSISTANT_SESSION_TOKEN="x" * 32,
        ALLOWED_FILE_ROOTS_JSON=json.dumps([str(tmp_path)]),
        DEVELOPMENT_COMMANDS_JSON=json.dumps(commands),
        PREFERRED_APPLICATIONS_JSON=json.dumps(
            {"Work Editor": str((tmp_path / "editor.exe").resolve())}
        ),
    )
    assert settings.allowed_file_roots == (tmp_path.resolve(),)
    assert settings.development_commands == commands
    assert settings.preferred_applications == {
        "work editor": str((tmp_path / "editor.exe").resolve())
    }


def test_api_keys_are_secret_values() -> None:
    settings = Settings(
        _env_file=None,
        ASSISTANT_SESSION_TOKEN="x" * 32,
        GEMINI_API_KEY="gemini-secret",
        DEEPGRAM_API_KEY="deepgram-secret",
    )
    rendered = repr(settings)
    assert "gemini-secret" not in rendered
    assert "deepgram-secret" not in rendered


def test_credential_manager_can_supply_missing_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GEMINI_API_KEY": "gemini-from-credential-manager",
        "DEEPGRAM_API_KEY": "deepgram-from-credential-manager",
    }
    monkeypatch.setattr(WindowsCredentialStore, "get", lambda name: values[name])
    settings = Settings(
        _env_file=None,
        ASSISTANT_SESSION_TOKEN="x" * 32,
        ASSISTANT_USE_CREDENTIAL_MANAGER=True,
    )

    _hydrate_provider_secrets(settings)

    assert settings.gemini_api_key is not None
    assert settings.deepgram_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == values["GEMINI_API_KEY"]
    assert settings.deepgram_api_key.get_secret_value() == values["DEEPGRAM_API_KEY"]


@pytest.mark.parametrize("shortcut", ["", " ", "Ctrl", "Ctrl+", "Ctrl+Shift"])
def test_setting_patch_rejects_partial_shortcuts(shortcut: str) -> None:
    with pytest.raises(ValidationError, match="shortcut"):
        SettingPatch(global_shortcut=shortcut)
    with pytest.raises(ValidationError, match="shortcut"):
        SettingPatch(push_to_talk_shortcut=shortcut)


def test_setting_patch_accepts_complete_shortcuts() -> None:
    patch = SettingPatch(global_shortcut="Ctrl+Shift+J", push_to_talk_shortcut="CmdOrCtrl+Space")
    assert patch.global_shortcut == "Ctrl+Shift+J"
    assert patch.push_to_talk_shortcut == "CmdOrCtrl+Space"
