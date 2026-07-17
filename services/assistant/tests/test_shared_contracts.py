from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from jarvis_assistant.models import EventEnvelope, EventType

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIRECTORY = ROOT / "shared" / "schemas"


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8"))


def _validate(schema: dict[str, object], value: object) -> None:
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_shared_json_schemas_are_valid_draft_2020_12() -> None:
    for path in SCHEMA_DIRECTORY.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_event_and_client_control_match_the_canonical_wire_schema() -> None:
    protocol = _schema("protocol.schema.json")
    definitions = protocol["$defs"]
    assert isinstance(definitions, dict)
    event_schema = definitions["event"]
    client_schema = definitions["clientMessage"]
    assert isinstance(event_schema, dict)
    assert isinstance(client_schema, dict)

    envelope = EventEnvelope(
        type=EventType.CANCELLATION,
        payload={"reason": "Operation cancelled."},
    ).model_dump(mode="json")
    _validate(event_schema, envelope)
    _validate(client_schema, {"type": "authenticate", "token": "x" * 32})
    _validate(client_schema, {"type": "start_listening"})


def test_activity_settings_and_tool_wire_examples_match_shared_schemas() -> None:
    _validate(
        _schema("activity.schema.json"),
        {
            "id": -1,
            "command_id": str(uuid4()),
            "created_at": "2026-07-16T12:00:00+00:00",
            "user_request": "What time is it?",
            "assistant_response": "It is noon.",
            "status": "success",
        },
    )
    _validate(
        _schema("settings.schema.json"),
        {
            "launch_on_startup": False,
            "minimize_to_tray": True,
            "play_activation_sound": True,
            "save_conversation_history": True,
            "developer_mode": False,
            "wake_word_enabled": True,
            "wake_phrase": "hey jarvis",
            "wake_sensitivity": 0.55,
            "microphone_device": None,
            "push_to_talk_shortcut": "Ctrl+Space",
            "global_shortcut": "Ctrl+Shift+J",
            "piper_executable_path": "",
            "piper_model_path": "",
            "speech_rate": 1.0,
            "speech_volume": 0.9,
            "voice_muted": False,
            "preferred_applications": {"editor": "C:\\Program Files\\Editor\\editor.exe"},
            "tool_permissions": {"get_current_datetime": "always_allow"},
        },
    )
    _validate(
        _schema("tool.schema.json"),
        {
            "name": "get_current_datetime",
            "description": "Get the current local date and time.",
            "permission_category": "system",
            "risk_level": "low",
            "confirmation_required": False,
            "timeout_seconds": 2.0,
            "argument_schema": {"type": "object"},
            "result_schema": {"type": "object"},
            "enabled": True,
            "permission": "always_allow",
        },
    )
