from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ALIAS_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
_MAX_ALIASES = 64


def normalize_preferred_applications(value: Any) -> dict[str, str]:
    """Validate user aliases without accepting arguments or shell syntax."""
    if not isinstance(value, dict):
        raise ValueError("preferred applications must be an object")
    if len(value) > _MAX_ALIASES:
        raise ValueError(f"preferred applications may contain at most {_MAX_ALIASES} aliases")

    normalized: dict[str, str] = {}
    for raw_alias, raw_path in value.items():
        if not isinstance(raw_alias, str) or not isinstance(raw_path, str):
            raise ValueError("preferred application aliases and paths must be strings")
        alias = " ".join(raw_alias.strip().split()).casefold()
        if not _ALIAS_PATTERN.fullmatch(alias):
            raise ValueError("preferred application alias contains unsupported characters")
        if any(character in raw_path for character in "\x00\r\n"):
            raise ValueError("preferred application path contains a prohibited character")
        executable = Path(raw_path.strip()).expanduser()
        if not executable.is_absolute() or executable.suffix.casefold() != ".exe":
            raise ValueError("preferred application paths must be absolute .exe paths")
        rendered = str(executable)
        if len(rendered) > 1_024:
            raise ValueError("preferred application path is too long")
        normalized[alias] = rendered
    return normalized
