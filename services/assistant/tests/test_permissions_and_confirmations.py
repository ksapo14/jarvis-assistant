from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_assistant.confirmations import (
    ConfirmationError,
    ConfirmationManager,
    action_fingerprint,
)
from jarvis_assistant.events import EventBus
from jarvis_assistant.memory import MemoryService
from jarvis_assistant.models import (
    ConfirmationDecision,
    PermissionCategory,
    PermissionLevel,
    RiskLevel,
    ToolCall,
    ToolDescriptor,
)
from jarvis_assistant.permissions import PermissionManager
from jarvis_assistant.tools.desktop import ClickNamedControlTool, TypeTextTool


def descriptor(
    risk: RiskLevel,
    *,
    required: bool = False,
    name: str = "sample_tool",
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="A test tool",
        argument_schema={"type": "object", "properties": {}},
        result_schema={"type": "object", "properties": {}},
        permission_category=PermissionCategory.SYSTEM,
        risk_level=risk,
        confirmation_required=required,
        timeout_seconds=10,
    )


async def test_permission_defaults(memory: MemoryService) -> None:
    manager = PermissionManager(memory)
    low = await manager.authorize(descriptor(RiskLevel.LOW))
    medium = await manager.authorize(descriptor(RiskLevel.MEDIUM, name="medium_tool"))
    assert low.allowed and not low.requires_confirmation
    assert medium.allowed and medium.requires_confirmation


async def test_disabled_permission_blocks(memory: MemoryService) -> None:
    manager = PermissionManager(memory)
    item = descriptor(RiskLevel.LOW)
    await manager.update_policy(item, enabled=False, permission=PermissionLevel.DISABLED)
    decision = await manager.authorize(item)
    assert not decision.allowed


async def test_high_risk_cannot_be_permanently_approved(memory: MemoryService) -> None:
    manager = PermissionManager(memory)
    item = descriptor(RiskLevel.HIGH)
    with pytest.raises(ValueError, match="high-risk"):
        await manager.update_policy(item, permission=PermissionLevel.ALWAYS_ALLOW)
    await manager.update_policy(item, permission=PermissionLevel.ALLOW_SESSION)
    manager.grant_for_session(item.name)
    decision = await manager.authorize(item)
    assert decision.requires_confirmation


@pytest.mark.parametrize("tool", [TypeTextTool(), ClickNamedControlTool()])
@pytest.mark.parametrize(
    "permission", [PermissionLevel.ALWAYS_ALLOW, PermissionLevel.ALLOW_SESSION]
)
async def test_required_confirmation_cannot_be_bypassed_by_permission_grants(
    tool: TypeTextTool | ClickNamedControlTool,
    permission: PermissionLevel,
) -> None:
    memory = MemoryService(":memory:")
    await memory.initialize()
    manager = PermissionManager(memory)
    try:
        await manager.update_policy(tool.descriptor, permission=permission)
        if permission is PermissionLevel.ALLOW_SESSION:
            manager.grant_for_session(tool.name)

        decision = await manager.authorize(tool.descriptor)

        assert decision.allowed
        assert decision.requires_confirmation
        assert "requires confirmation" in decision.reason
    finally:
        await memory.close()


async def test_confirmation_token_is_hashed_and_action_bound(tmp_path: Path) -> None:
    database_path = tmp_path / "confirmations.sqlite3"
    memory = MemoryService(database_path)
    await memory.initialize()
    manager = ConfirmationManager(memory, EventBus(), timeout_seconds=10)
    call = ToolCall(id="one", name="delete_path", arguments={"path": "C:/safe/a.txt"})
    request = await manager.create(call, RiskLevel.HIGH, "Delete C:/safe/a.txt?")
    raw_database = database_path.read_bytes()
    assert request.confirmation_token.encode() not in raw_database
    with pytest.raises(ConfirmationError, match="action changed"):
        await manager.decide(
            request.id,
            ConfirmationDecision(
                decision="yes",
                confirmation_token=request.confirmation_token,
                action_fingerprint=action_fingerprint(
                    call.model_copy(update={"arguments": {"path": "C:/safe/b.txt"}})
                ),
            ),
        )
    await memory.close()


async def test_clear_yes_or_no_decision_resolves_wait(memory: MemoryService) -> None:
    manager = ConfirmationManager(memory, EventBus(), timeout_seconds=10)
    call = ToolCall(id="one", name="create_folder", arguments={"path": "C:/safe/new"})
    request = await manager.create(call, RiskLevel.MEDIUM, "Create folder?")
    await manager.decide(
        request.id,
        ConfirmationDecision(
            decision="yes",
            confirmation_token=request.confirmation_token,
            action_fingerprint=request.action_fingerprint,
        ),
    )
    assert await manager.wait(request) == "yes"
