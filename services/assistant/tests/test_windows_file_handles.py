from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.models import ToolCall
from jarvis_assistant.tools import windows_file_ops
from jarvis_assistant.tools.files import DeletePathTool, MovePathTool, WriteTextFileTool
from jarvis_assistant.tools.safe_paths import PathScope

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows handle semantics")


@pytest.mark.parametrize(
    ("mode", "expected_content"),
    [("overwrite", "updated"), ("append", "confirmedupdated")],
)
async def test_existing_write_handle_blocks_target_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_content: str,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("confirmed", encoding="utf-8")
    replacement = root / "replacement.txt"
    replacement.write_text("do not touch", encoding="utf-8")
    tool = WriteTextFileTool(PathScope((root,)))
    call = ToolCall(
        id="write-handle-race",
        name=tool.name,
        arguments={"path": str(target), "content": "updated", "mode": mode},
    )
    _, bound = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    original_write = windows_file_ops._write_handle
    swap_blocked: list[bool] = []

    def attack_then_write(handle: object, data: bytes, *, append: bool) -> None:
        try:
            os.replace(replacement, target)
        except OSError:
            swap_blocked.append(True)
        else:
            swap_blocked.append(False)
        original_write(handle, data, append=append)  # type: ignore[arg-type]

    monkeypatch.setattr(windows_file_ops, "_write_handle", attack_then_write)
    await tool.execute(bound, CancellationToken())

    assert swap_blocked == [True]
    assert target.read_text(encoding="utf-8") == expected_content
    assert replacement.read_text(encoding="utf-8") == "do not touch"


async def test_handle_rename_locks_source_and_destination_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("confirmed", encoding="utf-8")
    destination = root / "destination.txt"
    tool = MovePathTool(PathScope((root,)))
    call = ToolCall(
        id="move-handle-race",
        name=tool.name,
        arguments={"source": str(source), "destination": str(destination)},
    )
    _, bound = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    original_rename = windows_file_ops._rename_handle
    source_swap_blocked: list[bool] = []
    parent_swap_blocked: list[bool] = []

    def attack_then_rename(
        source_handle: object, parent_handle: object, destination_path: Path
    ) -> None:
        try:
            source.rename(root / "stolen.txt")
        except OSError:
            source_swap_blocked.append(True)
        else:
            source_swap_blocked.append(False)
        try:
            root.rename(tmp_path / "redirected")
        except OSError:
            parent_swap_blocked.append(True)
        else:
            parent_swap_blocked.append(False)
        original_rename(  # type: ignore[arg-type]
            source_handle, parent_handle, destination_path
        )

    monkeypatch.setattr(windows_file_ops, "_rename_handle", attack_then_rename)
    await tool.execute(bound, CancellationToken())

    assert source_swap_blocked == [True]
    assert parent_swap_blocked == [True]
    assert destination.read_text(encoding="utf-8") == "confirmed"
    assert not source.exists()


async def test_delete_handle_blocks_replacement_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("confirmed", encoding="utf-8")
    replacement = root / "replacement.txt"
    replacement.write_text("do not delete", encoding="utf-8")
    tool = DeletePathTool(PathScope((root,)))
    call = ToolCall(
        id="delete-handle-race",
        name=tool.name,
        arguments={"path": str(target), "recursive": False},
    )
    _, bound = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    original_mark_delete = windows_file_ops._mark_delete
    swap_blocked: list[bool] = []

    def attack_then_delete(handle: object) -> None:
        try:
            os.replace(replacement, target)
        except OSError:
            swap_blocked.append(True)
        else:
            swap_blocked.append(False)
        original_mark_delete(handle)  # type: ignore[arg-type]

    monkeypatch.setattr(windows_file_ops, "_mark_delete", attack_then_delete)
    await tool.execute(bound, CancellationToken())

    assert swap_blocked == [True]
    assert not target.exists()
    assert replacement.read_text(encoding="utf-8") == "do not delete"


async def test_recursive_delete_locks_confirmed_root_during_child_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "tree"
    target.mkdir()
    (target / "child.txt").write_text("child", encoding="utf-8")
    outside = root / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    tool = DeletePathTool(PathScope((root,)))
    call = ToolCall(
        id="recursive-delete-handle-race",
        name=tool.name,
        arguments={"path": str(target), "recursive": True},
    )
    _, bound = await tool.bind_confirmation(
        call, tool.validate(call.arguments), CancellationToken()
    )
    original_mark_delete: Callable[[object], None] = windows_file_ops._mark_delete
    root_swap_blocked: list[bool] = []

    def attack_then_delete(handle: object) -> None:
        if not root_swap_blocked:
            try:
                target.rename(root / "swapped-tree")
            except OSError:
                root_swap_blocked.append(True)
            else:
                root_swap_blocked.append(False)
        original_mark_delete(handle)

    monkeypatch.setattr(windows_file_ops, "_mark_delete", attack_then_delete)
    await tool.execute(bound, CancellationToken())

    assert root_swap_blocked == [True]
    assert not target.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
