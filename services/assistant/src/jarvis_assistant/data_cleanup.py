from __future__ import annotations

import os
import shutil
from pathlib import Path


def clear_app_owned_screenshots(data_dir: Path) -> None:
    """Remove only the canonical app-owned screenshots directory, never a reparse target."""
    canonical_data_dir = data_dir.resolve(strict=False)
    lexical_directory = Path(os.path.abspath(data_dir / "screenshots"))
    canonical_directory = lexical_directory.resolve(strict=False)
    if not _same_path(lexical_directory, canonical_directory):
        if lexical_directory.exists() or lexical_directory.is_symlink():
            try:
                lexical_directory.unlink()
            except (IsADirectoryError, PermissionError):
                lexical_directory.rmdir()
        return
    try:
        canonical_directory.relative_to(canonical_data_dir)
    except ValueError as exc:
        raise ValueError("screenshots directory must stay inside assistant data") from exc
    if not canonical_directory.exists():
        return
    if not canonical_directory.is_dir():
        raise ValueError("the app-owned screenshots path is not a directory")
    shutil.rmtree(canonical_directory)


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(
        os.path.normpath(str(second))
    )
