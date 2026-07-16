from __future__ import annotations

from pathlib import Path

from jarvis_assistant.memory import MemoryService
from jarvis_assistant.models import (
    ConversationMessage,
    ConversationRole,
    PermissionLevel,
    RiskLevel,
    ToolResult,
)


async def test_settings_and_tool_policies_round_trip(memory: MemoryService) -> None:
    await memory.set_setting("wake_word_sensitivity", 0.72)
    await memory.set_tool_policy("open_website", enabled=False, permission=PermissionLevel.DISABLED)
    assert await memory.get_setting("wake_word_sensitivity") == 0.72
    assert await memory.get_tool_policy("open_website") == (
        False,
        PermissionLevel.DISABLED,
    )


async def test_preferred_application_map_is_typed_without_redacting_alias_names(
    memory: MemoryService, tmp_path: Path
) -> None:
    applications = {"password manager": str((tmp_path / "manager.exe").resolve())}
    await memory.set_setting("preferred_applications", applications)
    assert await memory.get_setting("preferred_applications") == applications


async def test_conversation_order_and_summary(memory: MemoryService) -> None:
    await memory.add_conversation(ConversationMessage(role=ConversationRole.USER, content="first"))
    await memory.add_conversation(
        ConversationMessage(role=ConversationRole.ASSISTANT, content="second")
    )
    await memory.set_summary("prefers concise replies")
    messages = await memory.recent_conversation(10)
    assert [message.content for message in messages] == ["first", "second"]
    assert await memory.latest_summary() == "prefers concise replies"


async def test_sensitive_tool_history_is_redacted(memory: MemoryService) -> None:
    result = ToolResult(
        tool_call_id="call",
        tool_name="read_clipboard",
        success=True,
        summary="Read clipboard text.",
        data={"text": "private clipboard content"},
    )
    await memory.add_tool_history(
        command_id="command",
        tool_name="read_clipboard",
        arguments={"text": "private input"},
        result=result,
        risk_level=RiskLevel.LOW,
        confirmation_result="yes",
    )
    history = await memory.history()
    serialized = str(history)
    assert "private clipboard content" not in serialized
    assert "private input" not in serialized
    assert "[REDACTED]" in serialized


async def test_clear_local_data_removes_records(memory: MemoryService) -> None:
    await memory.set_setting("voice_muted", True)
    await memory.add_conversation(ConversationMessage(role=ConversationRole.USER, content="hello"))
    await memory.clear_local_data()
    assert await memory.get_settings() == {}
    assert await memory.recent_conversation() == []
