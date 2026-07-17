from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import pytest
from pydantic import BaseModel, ConfigDict, Field

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.models import PermissionCategory, RiskLevel, ToolCall
from jarvis_assistant.tools import files as file_tools
from jarvis_assistant.tools.base import BaseTool, ToolOutput, ToolValidationError, UnknownToolError
from jarvis_assistant.tools.desktop import ClickNamedControlTool, OpenWebsiteTool
from jarvis_assistant.tools.files import DeletePathTool, WriteTextFileTool
from jarvis_assistant.tools.registry import ToolRegistry
from jarvis_assistant.tools.safe_paths import PathScope


class DangerousArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)


class DangerousTool(BaseTool):
    name = "dangerous_test"
    description = "Test dangerous execution blocking."
    permission_category = PermissionCategory.SYSTEM
    risk_level = RiskLevel.HIGH
    confirmation_required = True
    arguments_model = DangerousArguments
    result_model = ToolOutput
    executions: ClassVar[int] = 0

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> ToolOutput:
        cancellation.raise_if_cancelled()
        cast(DangerousArguments, arguments)
        type(self).executions += 1
        return ToolOutput(message="executed")


def test_tool_argument_schema_rejects_extra_and_bad_url() -> None:
    registry = ToolRegistry()
    registry.register(OpenWebsiteTool())
    with pytest.raises(ToolValidationError):
        registry.validate(
            ToolCall(
                id="bad",
                name="open_website",
                arguments={"url": "file:///C:/secret", "unexpected": True},
            )
        )


def test_unknown_tool_is_rejected() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError):
        registry.validate(ToolCall(id="bad", name="unknown_tool", arguments={}))


@pytest.mark.parametrize(
    "control_name",
    [
        "Authorize",
        "Checkout",
        "Confirm",
        "Send",
        "Sign in",
        "Submit form",
        "DeleteAccount",
        "OK",
        "Password",
        "Post",
        "Publish",
        "Reply",
        "Credentials",
        "Pay now",
        "Purchase",
        "Transfer",
    ],
)
def test_named_control_rejects_sensitive_generic_actions(control_name: str) -> None:
    tool = ClickNamedControlTool()
    with pytest.raises(ToolValidationError):
        tool.validate({"control_name": control_name, "control_type": "button"})


@pytest.mark.parametrize(
    ("control_name", "control_type"),
    [("Settings", "button"), ("Cancel", "any"), ("Accounts", "tab_item")],
)
def test_named_control_allows_only_navigation_safe_actions(
    control_name: str, control_type: str
) -> None:
    ClickNamedControlTool().validate({"control_name": control_name, "control_type": control_type})


async def test_dangerous_tool_does_not_execute_without_confirmation() -> None:
    DangerousTool.executions = 0
    registry = ToolRegistry()
    registry.register(DangerousTool())
    result = await registry.execute(
        ToolCall(id="danger", name="dangerous_test", arguments={"target": "x"}),
        CancellationToken(),
    )
    assert not result.success
    assert result.error_code == "confirmation_required"
    assert DangerousTool.executions == 0


async def test_dangerous_tool_can_execute_only_with_confirmed_flag() -> None:
    DangerousTool.executions = 0
    registry = ToolRegistry()
    registry.register(DangerousTool())
    result = await registry.execute(
        ToolCall(id="danger", name="dangerous_test", arguments={"target": "x"}),
        CancellationToken(),
        confirmed=True,
    )
    assert result.success
    assert DangerousTool.executions == 1


def test_path_scope_blocks_escape(tmp_path: Path) -> None:
    scope = PathScope((tmp_path / "allowed",))
    with pytest.raises(ToolValidationError, match="outside"):
        scope.resolve(str(tmp_path / "outside.txt"))


async def test_delete_tool_rejects_allowed_root_itself(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    tool = DeletePathTool(PathScope((root,)))
    with pytest.raises(ToolValidationError, match="root"):
        await tool.execute(
            tool.validate({"path": str(root), "recursive": True}), CancellationToken()
        )


async def test_create_text_file_never_replaces_target_created_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "note.txt"
    tool = WriteTextFileTool(PathScope((root,)))
    call = ToolCall(
        id="write-race",
        name=tool.name,
        arguments={"path": str(target), "content": "assistant", "mode": "create"},
    )
    _, bound = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    original_commit = file_tools._commit_no_replace

    def racing_commit(temporary_path: Path, destination: Path) -> None:
        destination.write_text("other process", encoding="utf-8")
        original_commit(temporary_path, destination)

    monkeypatch.setattr(file_tools, "_commit_no_replace", racing_commit)

    with pytest.raises(ToolValidationError, match="appeared before the write committed"):
        await tool.execute(bound, CancellationToken())

    assert target.read_text(encoding="utf-8") == "other process"
    assert list(root.glob(f".{target.name}.*.tmp")) == []
