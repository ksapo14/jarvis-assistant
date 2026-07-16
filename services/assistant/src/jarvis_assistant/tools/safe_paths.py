from __future__ import annotations

import os
from pathlib import Path

from .base import ToolValidationError


class PathScope:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        if not roots:
            raise ValueError("at least one allowed file root is required")
        self.roots = tuple(root.expanduser().resolve(strict=False) for root in roots)

    def resolve(
        self,
        raw_path: str,
        *,
        must_exist: bool = False,
        allow_root: bool = True,
    ) -> Path:
        if not raw_path or len(raw_path) > 1_024 or any(char in raw_path for char in "\x00\r\n"):
            raise ToolValidationError("path is empty, too long, or contains prohibited characters")
        expanded = Path(os.path.expandvars(raw_path)).expanduser()
        lexical_path = Path(os.path.abspath(expanded))
        path = lexical_path.resolve(strict=False)
        if not _same_path(lexical_path, path):
            raise ToolValidationError(
                "paths that traverse symbolic links or directory junctions are not allowed"
            )
        if not any(_is_relative_to(path, root) for root in self.roots):
            raise ToolValidationError("path is outside the configured allowed roots")
        if not allow_root and any(path == root for root in self.roots):
            raise ToolValidationError("the root of an allowed directory cannot be targeted")
        if must_exist and not path.exists():
            raise ToolValidationError(f"path does not exist: {path}")
        return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(
        os.path.normpath(str(second))
    )
