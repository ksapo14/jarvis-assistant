from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis_assistant.config import Settings
from jarvis_assistant.memory import MemoryService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        ASSISTANT_ENV="test",
        ASSISTANT_MOCK_MODE=True,
        ASSISTANT_DATA_DIR=tmp_path,
        ASSISTANT_SESSION_TOKEN="test-session-token-that-is-at-least-32-chars",
        WAKE_WORD_ENABLED=False,
        ALLOWED_FILE_ROOTS_JSON=f'["{tmp_path.as_posix()}"]',
    )


@pytest.fixture
async def memory(tmp_path: Path) -> AsyncIterator[MemoryService]:
    service = MemoryService(tmp_path / "memory.sqlite3")
    await service.initialize()
    try:
        yield service
    finally:
        await service.close()
