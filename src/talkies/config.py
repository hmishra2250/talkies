from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _xdg_path(variable: str, fallback: str) -> Path:
    configured = os.environ.get(variable)
    return Path(configured or Path.home() / fallback).expanduser()


def config_dir() -> Path:
    return _xdg_path("XDG_CONFIG_HOME", ".config") / "talkies"


def state_dir() -> Path:
    return _xdg_path("XDG_STATE_HOME", ".local/state") / "talkies"


def data_dir() -> Path:
    return _xdg_path("XDG_DATA_HOME", ".local/share") / "talkies"


def runtime_dir() -> Path:
    root = os.environ.get("XDG_RUNTIME_DIR")
    return Path(root) / "talkies" if root else state_dir() / "run"


@dataclass(slots=True)
class AudioConfig:
    microphone: str = "default"
    sample_rate: int = 16000
    channels: int = 1


@dataclass(slots=True)
class TranscriptionConfig:
    model: str = "small.en"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "en"
    beam_size: int = 5
    vad_filter: bool = True
    initial_prompt: str = ""
    offline: bool = True


@dataclass(slots=True)
class BehaviorConfig:
    paste: bool = True
    restore_clipboard: bool = True
    clipboard_restore_delay_ms: int = 350
    keep_dictation_audio: bool = False
    keep_note_audio: bool = True
    keep_meeting_audio: bool = True
    spoken_commands: bool = True


@dataclass(slots=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    replacements: dict[str, str] = field(default_factory=dict)


DEFAULT_CONFIG = """# Talkies stays offline after its speech model has been downloaded.

[audio]
microphone = "default"
sample_rate = 16000
channels = 1

[transcription]
# small.en is already fast and accurate on this laptop. For maximum English
# accuracy, try "distil-large-v3"; for multilingual use "large-v3-turbo".
model = "small.en"
device = "auto"
compute_type = "auto"
language = "en"
beam_size = 5
vad_filter = true
initial_prompt = ""
offline = true

[behavior]
paste = true
restore_clipboard = true
clipboard_restore_delay_ms = 350
keep_dictation_audio = false
keep_note_audio = true
keep_meeting_audio = true
spoken_commands = true

# Exact, case-insensitive phrase substitutions applied after transcription.
[replacements]
"talkies" = "Talkies"
"""


def _section(source: dict[str, Any], name: str) -> dict[str, Any]:
    value = source.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    if not path.exists():
        return Config()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    audio = _section(raw, "audio")
    transcription = _section(raw, "transcription")
    behavior = _section(raw, "behavior")
    replacements = _section(raw, "replacements")
    return Config(
        audio=AudioConfig(**{k: v for k, v in audio.items() if k in AudioConfig.__annotations__}),
        transcription=TranscriptionConfig(
            **{k: v for k, v in transcription.items() if k in TranscriptionConfig.__annotations__}
        ),
        behavior=BehaviorConfig(
            **{k: v for k, v in behavior.items() if k in BehaviorConfig.__annotations__}
        ),
        replacements={str(k): str(v) for k, v in replacements.items()},
    )


def ensure_user_dirs() -> None:
    for path in (config_dir(), state_dir(), data_dir(), runtime_dir()):
        path.mkdir(parents=True, exist_ok=True)
    config_path = config_dir() / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
