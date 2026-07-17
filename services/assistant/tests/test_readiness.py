from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis_assistant.config import Settings
from jarvis_assistant.main import _bind_listener, _write_readiness


def test_backend_owns_ephemeral_port_binding() -> None:
    listener = _bind_listener("127.0.0.1", 0)
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert port >= 1024
    finally:
        listener.close()


def test_readiness_file_is_atomic_and_contains_process_identity(tmp_path: Path) -> None:
    path = tmp_path / "ready.json"
    nonce = "n" * 64

    _write_readiness(path, nonce, 49152)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "nonce": nonce,
        "port": 49152,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_readiness_configuration_requires_complete_strong_contract(tmp_path: Path) -> None:
    common = {
        "_env_file": None,
        "ASSISTANT_SESSION_TOKEN": "s" * 64,
        "ASSISTANT_DATA_DIR": tmp_path / "data",
    }
    settings = Settings(
        **common,
        ASSISTANT_PORT=0,
        ASSISTANT_READY_FILE=tmp_path / "ready.json",
        ASSISTANT_READY_NONCE="n" * 64,
    )
    assert settings.port == 0

    with pytest.raises(ValidationError, match="configured together"):
        Settings(**common, ASSISTANT_READY_FILE=tmp_path / "orphan.json")
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(
            **common,
            ASSISTANT_READY_FILE=tmp_path / "weak.json",
            ASSISTANT_READY_NONCE="weak",
        )
    with pytest.raises(ValidationError, match="0 or at least 1024"):
        Settings(**common, ASSISTANT_PORT=80)
