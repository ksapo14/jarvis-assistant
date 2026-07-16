from __future__ import annotations

import os
from pathlib import Path


def _record_startup_failure(error: BaseException) -> None:
    """Leave a minimal local diagnostic when a windowless frozen process cannot initialize."""
    configured = os.getenv("ASSISTANT_DATA_DIR")
    if not configured:
        return
    try:
        from jarvis_assistant.logging_config import redact

        log_dir = Path(configured).resolve(strict=False) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        message = str(redact(f"{type(error).__name__}: {error}"))
        (log_dir / "startup-error.txt").write_text(message[:8_000], encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        from jarvis_assistant.main import main

        main()
    except BaseException as error:
        _record_startup_failure(error)
        raise
