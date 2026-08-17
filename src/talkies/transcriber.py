from __future__ import annotations

import logging
import shutil
from pathlib import Path
from threading import Lock

from .config import TranscriptionConfig

LOG = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    pass


class Transcriber:
    def __init__(self, config: TranscriptionConfig) -> None:
        self.config = config
        self._model = None
        self._device = "cpu"
        self._compute_type = "int8"
        self._lock = Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def backend(self) -> str:
        return f"{self._device}/{self._compute_type}"

    def _preferred_backend(self) -> tuple[str, str]:
        device = self.config.device
        if device == "auto":
            device = "cuda" if shutil.which("nvidia-smi") else "cpu"
        compute_type = self.config.compute_type
        if compute_type == "auto":
            compute_type = "int8_float16" if device == "cuda" else "int8"
        return device, compute_type

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(
                    "faster-whisper is not installed; run the Talkies installer again"
                ) from exc
            device, compute_type = self._preferred_backend()
            try:
                self._model = WhisperModel(
                    self.config.model,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=self.config.offline,
                )
                self._device, self._compute_type = device, compute_type
            except Exception:
                if device == "cpu":
                    raise
                LOG.exception("CUDA model load failed; falling back to CPU")
                self._model = WhisperModel(
                    self.config.model,
                    device="cpu",
                    compute_type="int8",
                    local_files_only=self.config.offline,
                )
                self._device, self._compute_type = "cpu", "int8"

    def transcribe(self, audio_path: Path) -> tuple[str, dict[str, object]]:
        self.load()
        assert self._model is not None
        try:
            return self._transcribe(audio_path)
        except Exception as exc:
            if self._device != "cuda":
                raise TranscriptionError(f"Transcription failed: {exc}") from exc
            LOG.exception("CUDA inference failed; retrying on CPU")
            self._model = None
            self.config.device = "cpu"
            self.load()
            try:
                return self._transcribe(audio_path)
            except Exception as retry_exc:
                raise TranscriptionError(f"Transcription failed: {retry_exc}") from retry_exc

    def _transcribe(self, audio_path: Path) -> tuple[str, dict[str, object]]:
        assert self._model is not None
        segments, info = self._model.transcribe(
            str(audio_path),
            language=self.config.language or None,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            initial_prompt=self.config.initial_prompt or None,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        metadata = {
            "language": getattr(info, "language", self.config.language),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "model": self.config.model,
            "backend": self.backend,
        }
        return text.strip(), metadata
