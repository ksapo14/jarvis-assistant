from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from jarvis_assistant.providers.base import ProviderUnavailableError
from jarvis_assistant.providers.openwakeword import OpenWakeWordProvider


@pytest.mark.asyncio
async def test_external_feature_models_are_passed_to_openwakeword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        name: Path("C:/external-openwakeword") / filename
        for name, filename in (
            ("wake", "hey_jarvis.onnx"),
            ("melspec", "melspectrogram.onnx"),
            ("embedding", "embedding_model.onnx"),
        )
    }
    monkeypatch.setattr(Path, "is_file", lambda self: self in paths.values())
    captured: dict[str, Any] = {}

    class FakeModel:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    package = types.ModuleType("openwakeword")
    package.__path__ = []  # type: ignore[attr-defined]
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)

    provider = OpenWakeWordProvider(
        paths["wake"],
        melspec_model_path=paths["melspec"],
        embedding_model_path=paths["embedding"],
    )
    await provider._get_model()

    assert captured == {
        "wakeword_models": [str(paths["wake"])],
        "melspec_model_path": str(paths["melspec"]),
        "embedding_model_path": str(paths["embedding"]),
        "inference_framework": "onnx",
    }


@pytest.mark.asyncio
async def test_frozen_backend_requires_external_feature_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    provider = OpenWakeWordProvider(None, phrase="hey jarvis")

    with pytest.raises(ProviderUnavailableError, match="does not redistribute"):
        await provider._get_model()


def test_spec_does_not_collect_openwakeword_data_indiscriminately() -> None:
    repository = Path(__file__).resolve().parents[3]
    spec = (repository / "scripts" / "jarvis-assistant.spec").read_text(encoding="utf-8")
    build_script = (repository / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert 'collect_all("openwakeword")' not in spec
    assert "onnx|tflite" in build_script
