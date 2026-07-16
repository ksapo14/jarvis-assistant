from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..cancellation import CancellationToken
from ..models import PermissionCategory, RiskLevel, ToolCall
from ..process_io import launch_associated_target
from .base import BaseTool, ToolExecutionError, ToolValidationError
from .safe_paths import PathScope
from .windows_file_ops import (
    delete_path as delete_path_by_handle,
)
from .windows_file_ops import (
    move_path as move_path_by_handle,
)
from .windows_file_ops import (
    windows_path_identity,
    write_existing_text,
)


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class PathResult(MessageResult):
    path: str


class SearchResult(MessageResult):
    matches: list[dict[str, object]]
    truncated: bool


class OpenPathArguments(StrictArguments):
    path: str = Field(min_length=1, max_length=1_024)


class OpenPathTool(BaseTool):
    name = "open_file_or_folder"
    description = (
        "Open an existing non-executable file or folder in its default Windows application."
    )
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.LOW
    arguments_model = OpenPathArguments
    result_model = PathResult

    enforce_timeout = False
    _SAFE_DOCUMENT_SUFFIXES = frozenset(
        {
            ".avi",
            ".bmp",
            ".csv",
            ".doc",
            ".docx",
            ".flac",
            ".gif",
            ".jpeg",
            ".jpg",
            ".json",
            ".m4a",
            ".md",
            ".mkv",
            ".mov",
            ".mp3",
            ".mp4",
            ".pdf",
            ".png",
            ".ppt",
            ".pptx",
            ".rtf",
            ".txt",
            ".wav",
            ".webp",
            ".xls",
            ".xlsx",
            ".yaml",
            ".yml",
        }
    )

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(OpenPathArguments, arguments)
        bound = values.model_copy(
            update={"path": str(self._scope.resolve(values.path, must_exist=True))}
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> PathResult:
        values = cast(OpenPathArguments, arguments)
        path = self._scope.resolve(values.path, must_exist=True)
        if path.is_file() and path.suffix.casefold() not in self._SAFE_DOCUMENT_SUFFIXES:
            raise ToolValidationError(
                "file type is not on the conservative document/media allowlist; scripts, "
                "installers, shortcuts, HTML, control-panel items, and executables cannot be opened"
            )
        cancellation.raise_if_cancelled()
        try:
            await launch_associated_target(str(path), cancellation)
        except (RuntimeError, TimeoutError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return PathResult(message=f"Opened {path}.", path=str(path))

    def preview(self, arguments: BaseModel) -> str:
        return f"Open {cast(OpenPathArguments, arguments).path}?"


class SearchFilesArguments(StrictArguments):
    query: str = Field(min_length=1, max_length=200)
    root: str | None = Field(default=None, max_length=1_024)
    max_results: int = Field(default=25, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def query_is_filename_text(cls, value: str) -> str:
        if any(char in value for char in "\x00\r\n/\\"):
            raise ValueError("query must be filename text, not a path")
        return value


class SearchFilesTool(BaseTool):
    name = "search_local_files"
    description = "Search filenames within the user's configured allowed folders."
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.LOW
    arguments_model = SearchFilesArguments
    result_model = SearchResult
    timeout_seconds = 15

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> SearchResult:
        values = cast(SearchFilesArguments, arguments)
        roots = (
            (self._scope.resolve(values.root, must_exist=True),)
            if values.root
            else self._scope.roots
        )
        return await asyncio.to_thread(self._search, roots, values, cancellation)

    def _search(
        self, roots: tuple[Path, ...], values: SearchFilesArguments, cancellation: CancellationToken
    ) -> SearchResult:
        needle = values.query.casefold()
        matches: list[dict[str, object]] = []
        truncated = False
        for root in roots:
            if not root.exists():
                continue
            for current_root, directories, files in os.walk(root, followlinks=False):
                cancellation.raise_if_cancelled()
                safe_directories: list[str] = []
                for directory in directories:
                    if directory.startswith("."):
                        continue
                    try:
                        self._scope.resolve(str(Path(current_root) / directory), must_exist=True)
                    except ToolValidationError:
                        continue
                    safe_directories.append(directory)
                directories[:] = safe_directories
                for name in (*directories, *files):
                    if needle in name.casefold():
                        path = Path(current_root) / name
                        matches.append(
                            {
                                "name": name,
                                "path": str(path),
                                "is_directory": path.is_dir(),
                            }
                        )
                        if len(matches) >= values.max_results:
                            truncated = True
                            return SearchResult(
                                message=f"Found at least {len(matches)} matches.",
                                matches=matches,
                                truncated=truncated,
                            )
        return SearchResult(
            message=f"Found {len(matches)} matches.", matches=matches, truncated=truncated
        )


class CreateFolderArguments(StrictArguments):
    path: str = Field(min_length=1, max_length=1_024)


class CreateFolderTool(BaseTool):
    name = "create_folder"
    description = "Create one new folder within an allowed user directory."
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = CreateFolderArguments
    result_model = PathResult
    enforce_timeout = False

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(CreateFolderArguments, arguments)
        bound = values.model_copy(update={"path": str(self._scope.resolve(values.path))})
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> PathResult:
        path = self._scope.resolve(cast(CreateFolderArguments, arguments).path)
        cancellation.raise_if_cancelled()
        try:
            await asyncio.to_thread(path.mkdir, parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise ToolExecutionError(f"path already exists: {path}") from exc
        except OSError as exc:
            raise ToolExecutionError(f"could not create folder: {exc}") from exc
        return PathResult(message=f"Created folder {path}.", path=str(path))

    def preview(self, arguments: BaseModel) -> str:
        return f"Create the folder {cast(CreateFolderArguments, arguments).path}?"


class WriteTextFileArguments(StrictArguments):
    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=1_000_000)
    mode: Literal["create", "overwrite", "append"] = "create"
    target_identity: dict[str, int] | None = Field(
        default=None, json_schema_extra={"internal": True}
    )


class WriteTextFileTool(BaseTool):
    name = "write_text_file"
    description = "Create, overwrite, or append UTF-8 plain text in an allowed user directory."
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = WriteTextFileArguments
    result_model = PathResult
    timeout_seconds = 15
    enforce_timeout = False
    _TEXT_SUFFIXES = frozenset(
        {
            ".txt",
            ".md",
            ".json",
            ".csv",
            ".log",
            ".yaml",
            ".yml",
            ".py",
            ".ts",
            ".tsx",
            ".css",
            ".html",
        }
    )

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(WriteTextFileArguments, arguments)
        path = self._scope.resolve(values.path)
        if values.mode == "create":
            if path.exists():
                raise ToolValidationError("file already exists; choose overwrite explicitly")
            identity = None
        else:
            if not path.is_file():
                raise ToolValidationError(f"{values.mode} requires an existing regular text file")
            identity = await asyncio.to_thread(_path_identity, path)
        bound = values.model_copy(update={"path": str(path), "target_identity": identity})
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> PathResult:
        values = cast(WriteTextFileArguments, arguments)
        path = self._scope.resolve(values.path)
        if path.suffix.casefold() not in self._TEXT_SUFFIXES:
            raise ToolValidationError("file extension is not approved for plain-text editing")
        if not path.parent.is_dir():
            raise ToolValidationError("parent directory does not exist")
        if values.mode == "create" or os.name != "nt":
            await asyncio.to_thread(
                _require_expected_identity,
                path,
                values.target_identity,
                expect_absent=values.mode == "create",
            )
        cancellation.raise_if_cancelled()
        await asyncio.to_thread(self._write, path, values)
        return PathResult(message=f"Wrote text to {path}.", path=str(path))

    @staticmethod
    def _write(path: Path, values: WriteTextFileArguments) -> None:
        if os.name == "nt" and values.mode != "create":
            if values.target_identity is None:
                raise ToolValidationError("the target has no confirmed file identity")
            write_existing_text(
                path,
                values.target_identity,
                values.content,
                append=values.mode == "append",
            )
            return
        if values.mode == "append":
            with path.open("a", encoding="utf-8", newline="") as output:
                output.write(values.content)
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                output.write(values.content)
                output.flush()
                os.fsync(output.fileno())
            if values.mode == "create":
                _commit_no_replace(Path(temporary_name), path)
            else:
                os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass

    def preview(self, arguments: BaseModel) -> str:
        values = cast(WriteTextFileArguments, arguments)
        digest = hashlib.sha256(values.content.encode("utf-8")).hexdigest()
        return (
            f"{values.mode.capitalize()} the exact text file {values.path} with "
            f"{len(values.content)} UTF-8 characters (SHA-256 {digest}); "
            f"confirmed target identity {values.target_identity}?"
        )


class MovePathArguments(StrictArguments):
    source: str = Field(min_length=1, max_length=1_024)
    destination: str = Field(min_length=1, max_length=1_024)
    source_identity: dict[str, int] | None = Field(
        default=None, json_schema_extra={"internal": True}
    )


class MovePathTool(BaseTool):
    name = "move_or_rename_path"
    description = "Move or rename one file or folder between configured allowed user directories."
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.MEDIUM
    confirmation_required = True
    arguments_model = MovePathArguments
    result_model = PathResult
    timeout_seconds = 30
    enforce_timeout = False

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(MovePathArguments, arguments)
        bound = values.model_copy(
            update={
                "source": str(
                    source := self._scope.resolve(values.source, must_exist=True, allow_root=False)
                ),
                "destination": str(self._scope.resolve(values.destination, allow_root=False)),
                "source_identity": await asyncio.to_thread(_path_identity, source),
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> PathResult:
        values = cast(MovePathArguments, arguments)
        source = self._scope.resolve(values.source, must_exist=True, allow_root=False)
        destination = self._scope.resolve(values.destination, allow_root=False)
        if values.source_identity is None:
            raise ToolValidationError("move source is not bound to a confirmed identity")
        cancellation.raise_if_cancelled()
        if source.drive.casefold() != destination.drive.casefold():
            raise ToolValidationError("cross-volume moves are not supported safely")
        try:
            if os.name == "nt":
                await asyncio.to_thread(
                    move_path_by_handle, source, destination, values.source_identity
                )
            else:
                await asyncio.to_thread(_require_expected_identity, source, values.source_identity)
                if destination.exists():
                    raise ToolValidationError(
                        "destination already exists; overwrite is not supported"
                    )
                if not destination.parent.is_dir():
                    raise ToolValidationError("destination parent directory does not exist")
                await asyncio.to_thread(source.rename, destination)
        except OSError as exc:
            raise ToolExecutionError(f"could not move path safely: {exc}") from exc
        return PathResult(message=f"Moved {source} to {destination}.", path=str(destination))

    def preview(self, arguments: BaseModel) -> str:
        values = cast(MovePathArguments, arguments)
        return (
            f"Move or rename {values.source} with confirmed identity "
            f"{values.source_identity} to {values.destination}?"
        )


class DeletePathArguments(StrictArguments):
    path: str = Field(min_length=1, max_length=1_024)
    recursive: bool = False
    target_identity: dict[str, int] | None = Field(
        default=None, json_schema_extra={"internal": True}
    )


class DeletePathTool(BaseTool):
    name = "delete_path"
    description = "Permanently delete one file or folder inside an allowed user directory."
    permission_category = PermissionCategory.FILES
    risk_level = RiskLevel.HIGH
    confirmation_required = True
    arguments_model = DeletePathArguments
    result_model = PathResult
    timeout_seconds = 60
    enforce_timeout = False

    def __init__(self, scope: PathScope) -> None:
        self._scope = scope

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        cancellation.raise_if_cancelled()
        values = cast(DeletePathArguments, arguments)
        bound = values.model_copy(
            update={
                "path": str(
                    path := self._scope.resolve(values.path, must_exist=True, allow_root=False)
                ),
                "target_identity": await asyncio.to_thread(_path_identity, path),
            }
        )
        return call.model_copy(update={"arguments": bound.model_dump(mode="json")}), bound

    async def execute(self, arguments: BaseModel, cancellation: CancellationToken) -> PathResult:
        values = cast(DeletePathArguments, arguments)
        path = self._scope.resolve(values.path, must_exist=True, allow_root=False)
        if values.target_identity is None:
            raise ToolValidationError("delete target is not bound to a confirmed identity")
        cancellation.raise_if_cancelled()
        if os.name == "nt":
            await asyncio.to_thread(
                delete_path_by_handle,
                path,
                values.target_identity,
                recursive=values.recursive,
            )
        else:
            await asyncio.to_thread(_require_expected_identity, path, values.target_identity)
            if path.is_symlink() or path.is_file():
                await asyncio.to_thread(path.unlink)
            elif path.is_dir():
                if any(path.iterdir()) and not values.recursive:
                    raise ToolValidationError(
                        "folder is not empty; recursive must be explicitly true"
                    )
                if values.recursive:
                    await asyncio.to_thread(shutil.rmtree, path)
                else:
                    await asyncio.to_thread(path.rmdir)
            else:
                raise ToolValidationError("target is not a regular file or directory")
        return PathResult(message=f"Deleted {path}.", path=str(path))

    def preview(self, arguments: BaseModel) -> str:
        values = cast(DeletePathArguments, arguments)
        detail = " and everything inside it" if values.recursive else ""
        return (
            f"Permanently delete {values.path}{detail} with confirmed identity "
            f"{values.target_identity}? This cannot be undone."
        )


def _path_identity(path: Path) -> dict[str, int]:
    if os.name == "nt":
        return windows_path_identity(path)
    information = path.stat(follow_symlinks=False)
    return {
        "device": int(information.st_dev),
        "inode": int(information.st_ino),
        "file_type": int(stat.S_IFMT(information.st_mode)),
        "size": int(information.st_size),
        "mtime_ns": int(information.st_mtime_ns),
    }


def _commit_no_replace(temporary_path: Path, destination: Path) -> None:
    """Atomically publish a new file without replacing a racing destination."""
    try:
        if os.name == "nt":
            # Windows rename is atomic and fails when the destination exists.
            os.rename(temporary_path, destination)
        else:
            # link(2) provides portable no-replace publication for the test/dev hosts.
            os.link(temporary_path, destination)
    except FileExistsError as exc:
        raise ToolValidationError(
            "the target appeared before the write committed; request a fresh confirmation"
        ) from exc


def _require_expected_identity(
    path: Path,
    expected: dict[str, int] | None,
    *,
    expect_absent: bool = False,
) -> None:
    if expect_absent:
        if path.exists() or path.is_symlink():
            raise ToolValidationError(
                "the target appeared after confirmation; request a fresh confirmation"
            )
        return
    if expected is None:
        raise ToolValidationError("the target has no confirmed file identity")
    try:
        actual = _path_identity(path)
    except FileNotFoundError as exc:
        raise ToolValidationError(
            "the target disappeared after confirmation; request a fresh confirmation"
        ) from exc
    if actual != expected:
        raise ToolValidationError(
            "the target changed after confirmation; request a fresh confirmation"
        )
