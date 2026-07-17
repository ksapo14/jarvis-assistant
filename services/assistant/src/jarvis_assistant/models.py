from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .application_aliases import normalize_preferred_applications


def utc_now() -> datetime:
    return datetime.now(UTC)


class AssistantState(StrEnum):
    IDLE = "idle"
    WAKE_WORD_DETECTED = "wake_word_detected"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    ERROR = "error"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PermissionLevel(StrEnum):
    DISABLED = "disabled"
    ASK_EVERY_TIME = "ask_every_time"
    ALLOW_SESSION = "allow_session"
    ALWAYS_ALLOW = "always_allow"


class PermissionCategory(StrEnum):
    APPLICATIONS = "applications"
    FILES = "files"
    WINDOWS = "windows"
    INPUT = "input"
    CLIPBOARD = "clipboard"
    SCREEN_CAPTURE = "screen_capture"
    SYSTEM = "system"
    DEVELOPMENT = "development"
    COMMUNICATIONS = "communications"


class EventType(StrEnum):
    STATUS_CHANGED = "status_changed"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    ASSISTANT_RESPONSE = "assistant_response"
    TOOL_PROPOSAL = "tool_proposal"
    CONFIRMATION_REQUEST = "confirmation_request"
    CONFIRMATION_DECISION = "confirmation_decision"
    TOOL_EXECUTION_RESULT = "tool_execution_result"
    SETTINGS_UPDATED = "settings_updated"
    CANCELLATION = "cancellation"
    ERROR = "error"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EventType
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    is_final: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    speech_final: bool = False


class ConversationRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any]
    provider_metadata: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ConversationRole
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    argument_schema: dict[str, Any]
    result_schema: dict[str, Any]
    permission_category: PermissionCategory
    risk_level: RiskLevel
    confirmation_required: bool
    timeout_seconds: float = Field(gt=0, le=300)
    enabled: bool = True


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    tool_name: str
    success: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = Field(default=0, ge=0)


class LanguageModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ConversationMessage]
    tools: list[ToolDescriptor] = Field(default_factory=list)
    long_term_context: str | None = None
    max_spoken_words: int = Field(default=45, ge=5, le=200)


class LanguageModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    spoken_text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    finish_reason: str | None = None

    @field_validator("spoken_text")
    @classmethod
    def spoken_text_must_be_bounded(cls, value: str) -> str:
        if len(value) > 2_000:
            raise ValueError("spoken_text is unexpectedly long")
        return value


class ProviderStatus(BaseModel):
    name: str
    available: bool
    detail: str


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    confirmation_token: str = Field(min_length=32, exclude=True)
    tool_call: ToolCall
    risk_level: RiskLevel
    prompt: str
    action_fingerprint: str
    expires_at: datetime


class ConfirmationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["yes", "no"]
    confirmation_token: str = Field(min_length=32)
    action_fingerprint: str


class CommandAccepted(BaseModel):
    command_id: UUID
    status: Literal["accepted"] = "accepted"


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)

    @field_validator("text")
    @classmethod
    def reject_only_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("command cannot be empty")
        return value


class SettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    launch_on_startup: bool | None = None
    minimize_to_tray: bool | None = None
    play_activation_sound: bool | None = None
    save_conversation_history: bool | None = None
    developer_mode: bool | None = None
    wake_word_enabled: bool | None = None
    wake_phrase: str | None = Field(default=None, min_length=1, max_length=100)
    wake_sensitivity: float | None = Field(default=None, ge=0, le=1)
    microphone_device: str | None = Field(default=None, max_length=300)
    push_to_talk_shortcut: str | None = Field(default=None, min_length=1, max_length=100)
    global_shortcut: str | None = Field(default=None, min_length=1, max_length=100)
    piper_executable_path: str | None = Field(default=None, max_length=1_024)
    piper_model_path: str | None = Field(default=None, max_length=1_024)
    speech_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    speech_volume: float | None = Field(default=None, ge=0, le=1)
    voice_muted: bool | None = None
    preferred_applications: dict[str, str] | None = None

    @field_validator("preferred_applications", mode="before")
    @classmethod
    def valid_preferred_applications(cls, value: Any) -> dict[str, str] | None:
        if value is None:
            return None
        return normalize_preferred_applications(value)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        aliases = {
            "save_history": "save_conversation_history",
            "wake_word_sensitivity": "wake_sensitivity",
        }
        for legacy, canonical in aliases.items():
            if legacy in normalized:
                normalized.setdefault(canonical, normalized[legacy])
                normalized.pop(legacy)
        return normalized

    @model_validator(mode="after")
    def reject_null_for_non_clearable_settings(self) -> SettingPatch:
        clearable = {"microphone_device", "piper_executable_path", "piper_model_path"}
        for name in self.model_fields_set - clearable:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    @field_validator("push_to_talk_shortcut", "global_shortcut")
    @classmethod
    def reject_incomplete_shortcuts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        _validate_shortcut(value)
        return value


_SHORTCUT_MODIFIERS = {
    "alt": "alt",
    "cmdorctrl": "ctrl",
    "commandorcontrol": "ctrl",
    "control": "ctrl",
    "ctrl": "ctrl",
    "meta": "super",
    "shift": "shift",
    "super": "super",
    "win": "super",
    "windows": "super",
}
_SHORTCUT_FUNCTION_KEYS = {f"f{number}" for number in range(1, 25)}
_SHORTCUT_NAMED_KEYS = {
    "arrowdown",
    "arrowleft",
    "arrowright",
    "arrowup",
    "backspace",
    "capslock",
    "delete",
    "down",
    "end",
    "enter",
    "esc",
    "escape",
    "home",
    "insert",
    "left",
    "numlock",
    "pagedown",
    "pageup",
    "pause",
    "printscreen",
    "return",
    "right",
    "scrolllock",
    "space",
    "tab",
    "up",
}


def _validate_shortcut(value: str) -> None:
    parts = [part.strip().casefold() for part in value.split("+")]
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError("shortcut must include complete modifier and key segments")
    modifiers = [_SHORTCUT_MODIFIERS.get(part) for part in parts[:-1]]
    if any(modifier is None for modifier in modifiers):
        raise ValueError("shortcut modifiers must precede the final key")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("shortcut modifiers cannot be repeated")
    key = parts[-1]
    if key in _SHORTCUT_MODIFIERS:
        raise ValueError("shortcut must include a non-modifier key")
    if not (
        (len(key) == 1 and key.isascii() and key.isalnum())
        or key in _SHORTCUT_FUNCTION_KEYS
        or key in _SHORTCUT_NAMED_KEYS
    ):
        raise ValueError("shortcut key is not supported")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    state: AssistantState
    version: str
    mock_mode: bool
