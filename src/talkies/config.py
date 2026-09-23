from __future__ import annotations

import json
import os
import re
import stat
import tempfile
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
    beam_size: int = 1
    vad_filter: bool = True
    initial_prompt: str = ""
    offline: bool = True
    model_idle_timeout_seconds: int = 60


@dataclass(slots=True)
class BehaviorConfig:
    paste: bool = True
    restore_clipboard: bool = True
    clipboard_restore_delay_ms: int = 350
    keep_dictation_audio: bool = True
    dictation_audio_retention_days: int = 14
    keep_note_audio: bool = True
    keep_meeting_audio: bool = True
    spoken_commands: bool = True
    hold_key: str = "Control_R"
    hold_hotkey: bool = True


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
# Benchmark other models on corrected recordings before changing the default.
model = "small.en"
device = "auto"
compute_type = "auto"
language = "en"
beam_size = 1
vad_filter = true
initial_prompt = ""
offline = true
# Release all model, CUDA, and VAD memory after this many idle seconds.
# Set to 0 to keep the model warm indefinitely.
model_idle_timeout_seconds = 60

[behavior]
paste = true
restore_clipboard = true
clipboard_restore_delay_ms = 350
keep_dictation_audio = true
dictation_audio_retention_days = 14
keep_note_audio = true
keep_meeting_audio = true
spoken_commands = true
hold_key = "Control_R"
hold_hotkey = true

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


def save_replacements(replacements: dict[str, str], path: Path | None = None) -> None:
    path = path or config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_CONFIG
    section = re.search(r"(?m)^\[replacements\][ \t]*$", original)
    lines = "".join(
        f"{json.dumps(source)} = {json.dumps(target)}\n"
        for source, target in sorted(replacements.items(), key=lambda pair: pair[0].casefold())
    )
    if section:
        next_section = re.search(r"(?m)^\[[^\n]+\][ \t]*$", original[section.end():])
        end = section.end() + next_section.start() if next_section else len(original)
        updated = original[:section.end()] + "\n" + lines + original[end:]
    else:
        updated = original.rstrip() + "\n\n[replacements]\n" + lines
    tomllib.loads(updated)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as handle:
        handle.write(updated)
        temporary = Path(handle.name)
    try:
        temporary.chmod(stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_hold_key(binding: str, path: Path | None = None) -> None:
    """Persist the selected X11 binding without rewriting unrelated TOML settings."""
    path = path or config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_CONFIG
    section = re.search(r"(?m)^\[behavior\][ \t]*$", original)
    setting = f"hold_key = {json.dumps(binding)}\n"
    if section:
        next_section = re.search(r"(?m)^\[[^\n]+\][ \t]*$", original[section.end():])
        end = section.end() + next_section.start() if next_section else len(original)
        body = original[section.end():end]
        match = re.search(r"(?m)^[ \t]*hold_key[ \t]*=[^\n]*(?:\n|$)", body)
        if match:
            body = body[:match.start()] + setting + body[match.end():]
        else:
            body = body.rstrip() + "\n" + setting
        updated = original[:section.end()] + body + original[end:]
    else:
        updated = original.rstrip() + "\n\n[behavior]\n" + setting
    tomllib.loads(updated)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as handle:
        handle.write(updated)
        temporary = Path(handle.name)
    try:
        temporary.chmod(stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
