from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .application_aliases import normalize_preferred_applications
from .known_folders import default_user_file_roots


def _default_data_dir() -> Path:
    root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "JarvisAssistant"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    environment: Literal["development", "production", "test", "mock"] = Field(
        default="development", alias="ASSISTANT_ENV"
    )
    mock_mode: bool = Field(default=False, alias="ASSISTANT_MOCK_MODE")
    host: str = Field(default="127.0.0.1", alias="ASSISTANT_HOST")
    port: int = Field(default=8765, ge=0, le=65535, alias="ASSISTANT_PORT")
    session_token: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(32)),
        alias="ASSISTANT_SESSION_TOKEN",
    )
    parent_pid: int | None = Field(default=None, gt=0, alias="ASSISTANT_PARENT_PID")
    readiness_file: Path | None = Field(default=None, alias="ASSISTANT_READY_FILE")
    readiness_nonce: SecretStr | None = Field(default=None, alias="ASSISTANT_READY_NONCE")
    data_dir: Path = Field(default_factory=_default_data_dir, alias="ASSISTANT_DATA_DIR")
    log_level: str = Field(default="INFO", alias="ASSISTANT_LOG_LEVEL")
    log_max_bytes: int = Field(default=5_000_000, ge=100_000, alias="ASSISTANT_LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, ge=1, le=20, alias="ASSISTANT_LOG_BACKUPS")

    stt_provider: Literal["deepgram", "willow", "mock"] = Field(
        default="deepgram", alias="ASSISTANT_STT_PROVIDER"
    )
    llm_provider: Literal["gemini", "mock"] = Field(
        default="gemini", alias="ASSISTANT_LLM_PROVIDER"
    )
    tts_provider: Literal["piper", "mock"] = Field(default="piper", alias="ASSISTANT_TTS_PROVIDER")
    deepgram_api_key: SecretStr | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    deepgram_model: str = Field(default="nova-3", alias="DEEPGRAM_MODEL")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    use_credential_manager: bool = Field(default=False, alias="ASSISTANT_USE_CREDENTIAL_MANAGER")
    willow_endpoint: str = Field(
        default="http://127.0.0.1:19000/v1/audio/transcriptions",
        validation_alias=AliasChoices("WILLOW_WIS_URL", "WILLOW_ENDPOINT"),
    )
    piper_executable_path: Path | None = Field(default=None, alias="PIPER_EXECUTABLE_PATH")
    piper_model_path: Path | None = Field(default=None, alias="PIPER_MODEL_PATH")

    wake_word_enabled: bool = Field(default=True, alias="WAKE_WORD_ENABLED")
    wake_word_phrase: str = Field(
        default="hey jarvis",
        validation_alias=AliasChoices("ASSISTANT_WAKE_PHRASE", "WAKE_WORD_PHRASE"),
    )
    wake_word_model_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENWAKEWORD_MODEL_PATH", "WAKE_WORD_MODEL_PATH"),
    )
    wake_word_melspec_model_path: Path | None = Field(
        default=None, alias="OPENWAKEWORD_MELSPEC_MODEL_PATH"
    )
    wake_word_embedding_model_path: Path | None = Field(
        default=None, alias="OPENWAKEWORD_EMBEDDING_MODEL_PATH"
    )
    wake_word_sensitivity: float = Field(
        default=0.55,
        ge=0,
        le=1,
        validation_alias=AliasChoices("ASSISTANT_WAKE_SENSITIVITY", "WAKE_WORD_SENSITIVITY"),
    )
    wake_word_cooldown_seconds: float = Field(
        default=2.5, ge=0.5, le=30, alias="WAKE_WORD_COOLDOWN_SECONDS"
    )
    microphone_device: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ASSISTANT_MICROPHONE_DEVICE", "MICROPHONE_DEVICE"),
    )
    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000, alias="AUDIO_SAMPLE_RATE")
    speech_timeout_seconds: float = Field(default=20, ge=2, le=120, alias="SPEECH_TIMEOUT_SECONDS")
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0, alias="SPEECH_RATE")
    speech_volume: float = Field(default=1.0, ge=0, le=1, alias="SPEECH_VOLUME")
    launch_on_startup: bool = Field(default=False, alias="ASSISTANT_LAUNCH_ON_STARTUP")
    minimize_to_tray: bool = Field(default=True, alias="ASSISTANT_MINIMIZE_TO_TRAY")
    play_activation_sound: bool = Field(default=True, alias="ASSISTANT_PLAY_ACTIVATION_SOUND")
    save_conversation_history: bool = Field(
        default=True, alias="ASSISTANT_SAVE_CONVERSATION_HISTORY"
    )
    developer_mode: bool = Field(default=False, alias="ASSISTANT_DEVELOPER_MODE")
    push_to_talk_shortcut: str = Field(
        default="Ctrl+Space", alias="ASSISTANT_PUSH_TO_TALK_SHORTCUT"
    )
    global_shortcut: str = Field(default="Ctrl+Shift+J", alias="ASSISTANT_GLOBAL_SHORTCUT")
    confirmation_timeout_seconds: float = Field(
        default=30, ge=5, le=300, alias="CONFIRMATION_TIMEOUT_SECONDS"
    )
    max_history_messages: int = Field(default=20, ge=2, le=100, alias="MAX_HISTORY_MESSAGES")
    allowed_file_roots_json: str = Field(default="[]", alias="ALLOWED_FILE_ROOTS_JSON")
    trusted_script_roots_json: str = Field(default="[]", alias="TRUSTED_SCRIPT_ROOTS_JSON")
    trusted_script_allowlist_json: str = Field(default="[]", alias="TRUSTED_SCRIPT_ALLOWLIST_JSON")
    trusted_python_executable_path: Path | None = Field(
        default=None, alias="TRUSTED_PYTHON_EXECUTABLE_PATH"
    )
    development_commands_json: str = Field(default="{}", alias="DEVELOPMENT_COMMANDS_JSON")
    preferred_applications: dict[str, str] = Field(
        default_factory=dict, alias="PREFERRED_APPLICATIONS_JSON"
    )

    @field_validator("preferred_applications", mode="before")
    @classmethod
    def valid_preferred_applications(cls, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid preferred application JSON") from exc
        return normalize_preferred_applications(value)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "127.0.0.1":
            raise ValueError(
                "ASSISTANT_HOST must use IPv4 loopback 127.0.0.1 for the desktop contract"
            )
        return normalized

    @field_validator("session_token")
    @classmethod
    def strong_session_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("ASSISTANT_SESSION_TOKEN must contain at least 32 characters")
        return value

    @field_validator("port")
    @classmethod
    def valid_loopback_port(cls, value: int) -> int:
        if 0 < value < 1024:
            raise ValueError("ASSISTANT_PORT must be 0 or at least 1024")
        return value

    @model_validator(mode="after")
    def valid_readiness_contract(self) -> Settings:
        if (self.readiness_file is None) != (self.readiness_nonce is None):
            raise ValueError(
                "ASSISTANT_READY_FILE and ASSISTANT_READY_NONCE must be configured together"
            )
        if self.readiness_file is not None:
            if not self.readiness_file.is_absolute():
                raise ValueError("ASSISTANT_READY_FILE must be an absolute path")
            assert self.readiness_nonce is not None
            if len(self.readiness_nonce.get_secret_value()) < 32:
                raise ValueError("ASSISTANT_READY_NONCE must contain at least 32 characters")
        return self

    @model_validator(mode="after")
    def select_mock_providers(self) -> Settings:
        if self.environment == "mock":
            self.mock_mode = True
        if self.mock_mode:
            self.stt_provider = "mock"
            self.llm_provider = "mock"
            self.tts_provider = "mock"
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / "assistant.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def allowed_file_roots(self) -> tuple[Path, ...]:
        values = self._parse_json_collection(self.allowed_file_roots_json, list)
        defaults = default_user_file_roots()
        return tuple(Path(value).expanduser().resolve() for value in (values or defaults))

    @property
    def trusted_script_roots(self) -> tuple[Path, ...]:
        values = self._parse_json_collection(self.trusted_script_roots_json, list)
        return tuple(Path(value).expanduser().resolve() for value in values)

    @property
    def trusted_script_allowlist(self) -> tuple[Path, ...]:
        values = self._parse_json_collection(self.trusted_script_allowlist_json, list)
        return tuple(Path(value).expanduser().resolve() for value in values)

    @property
    def development_commands(self) -> dict[str, list[str]]:
        raw = self._parse_json_collection(self.development_commands_json, dict)
        parsed: dict[str, list[str]] = {}
        for name, value in raw.items():
            if (
                isinstance(name, str)
                and isinstance(value, list)
                and all(isinstance(item, str) for item in value)
            ):
                parsed[name] = value
        return parsed

    @staticmethod
    def _parse_json_collection(value: str, expected: type[list[Any]] | type[dict[str, Any]]) -> Any:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON configuration") from exc
        if not isinstance(parsed, expected):
            raise ValueError(f"configuration must be a JSON {expected.__name__}")
        return parsed
