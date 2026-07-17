from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any, ClassVar

from ..models import ProviderStatus
from .base import ProviderUnavailableError, WakeWordProvider


class OpenWakeWordProvider(WakeWordProvider):
    """openWakeWord adapter with an honest stock-model/custom-model boundary."""

    _STOCK_MODELS: ClassVar[dict[str, str]] = {"hey jarvis": "hey_jarvis"}

    def __init__(
        self,
        model_path: Path | None,
        *,
        melspec_model_path: Path | None = None,
        embedding_model_path: Path | None = None,
        phrase: str = "hey jarvis",
        sensitivity: float = 0.55,
    ) -> None:
        self._model_path = model_path
        self._melspec_model_path = melspec_model_path
        self._embedding_model_path = embedding_model_path
        self._phrase = phrase
        self._sensitivity = sensitivity
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def detect(self, pcm16_audio: bytes) -> bool:
        model = await self._get_model()
        try:
            import numpy as np
        except ImportError as exc:
            raise ProviderUnavailableError("numpy is required for openWakeWord") from exc
        samples = np.frombuffer(pcm16_audio, dtype=np.int16)
        scores = await asyncio.to_thread(model.predict, samples)
        return bool(scores) and max(float(score) for score in scores.values()) >= self._sensitivity

    async def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset"):
            await asyncio.to_thread(self._model.reset)

    async def configure(self, *, phrase: str, sensitivity: float) -> None:
        changed = self._normalize_phrase(phrase) != self._normalize_phrase(self._phrase)
        changed = changed or sensitivity != self._sensitivity
        self._phrase = phrase
        self._sensitivity = sensitivity
        if not changed:
            return
        async with self._load_lock:
            previous = self._model
            self._model = None
        if previous is not None and hasattr(previous, "reset"):
            await asyncio.to_thread(previous.reset)

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        model_spec = self._model_spec()
        feature_model_arguments = self._feature_model_arguments()
        async with self._load_lock:
            if self._model is None:
                try:
                    from openwakeword.model import Model
                except ImportError as exc:
                    raise ProviderUnavailableError("openwakeword is not installed") from exc
                try:
                    self._model = await asyncio.to_thread(
                        Model,
                        wakeword_models=[model_spec],
                        **feature_model_arguments,
                    )
                except Exception as exc:
                    if self._model_path is None:
                        raise ProviderUnavailableError(
                            "The installed openWakeWord package could not load its stock "
                            "'hey_jarvis' model. Install compatible model assets or set "
                            "OPENWAKEWORD_MODEL_PATH to a compatible custom model."
                        ) from exc
                    raise ProviderUnavailableError(
                        f"The custom openWakeWord model is incompatible or unreadable: {model_spec}"
                    ) from exc
        return self._model

    async def status(self) -> ProviderStatus:
        try:
            import openwakeword  # noqa: F401
        except ImportError:
            return ProviderStatus(
                name="openwakeword", available=False, detail="openwakeword is not installed"
            )
        try:
            model_spec = self._model_spec()
            await self._get_model()
        except ProviderUnavailableError as exc:
            return ProviderStatus(name="openwakeword", available=False, detail=str(exc))
        source = "custom model" if self._model_path is not None else "stock model"
        return ProviderStatus(
            name="openwakeword",
            available=True,
            detail=f"{source}: {model_spec}; phrase: {self._phrase}",
        )

    def _model_spec(self) -> str:
        if self._model_path is not None:
            if not self._model_path.is_file():
                raise ProviderUnavailableError(
                    f"Configured wake-word model does not exist: {self._model_path}"
                )
            return str(self._model_path)
        stock_model = self._STOCK_MODELS.get(self._normalize_phrase(self._phrase))
        if stock_model is None:
            raise ProviderUnavailableError(
                f"No stock openWakeWord model matches {self._phrase!r}. The compatible stock "
                "phrase is 'hey jarvis'; configure OPENWAKEWORD_MODEL_PATH for a custom phrase."
            )
        return stock_model

    def _feature_model_arguments(self) -> dict[str, str]:
        paths = (self._melspec_model_path, self._embedding_model_path)
        if not any(paths):
            if getattr(sys, "frozen", False):
                raise ProviderUnavailableError(
                    "The packaged assistant does not redistribute openWakeWord model assets. "
                    "Run scripts/install-wake-model.ps1 and configure the external wake, "
                    "melspectrogram, and embedding model paths."
                )
            return {}
        if not all(paths):
            raise ProviderUnavailableError(
                "Both OPENWAKEWORD_MELSPEC_MODEL_PATH and "
                "OPENWAKEWORD_EMBEDDING_MODEL_PATH are required together."
            )
        assert self._melspec_model_path is not None
        assert self._embedding_model_path is not None
        for label, path in (
            ("melspectrogram", self._melspec_model_path),
            ("embedding", self._embedding_model_path),
        ):
            if not path.is_file():
                raise ProviderUnavailableError(
                    f"Configured openWakeWord {label} model does not exist: {path}"
                )
        suffixes = {
            self._melspec_model_path.suffix.casefold(),
            self._embedding_model_path.suffix.casefold(),
        }
        if suffixes not in ({".onnx"}, {".tflite"}):
            raise ProviderUnavailableError(
                "openWakeWord feature models must both use ONNX or both use TFLite."
            )
        return {
            "melspec_model_path": str(self._melspec_model_path),
            "embedding_model_path": str(self._embedding_model_path),
            "inference_framework": "onnx" if suffixes == {".onnx"} else "tflite",
        }

    @staticmethod
    def _normalize_phrase(phrase: str) -> str:
        return re.sub(r"[_\s]+", " ", phrase.strip().casefold())
