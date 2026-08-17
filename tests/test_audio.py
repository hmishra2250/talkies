from pathlib import Path

import pytest

from talkies import audio
from talkies.audio import AudioError, AudioRecorder, recording_command
from talkies.config import AudioConfig


def test_microphone_recording_command() -> None:
    command = recording_command(Path("voice.wav"), AudioConfig(microphone="mic"))
    assert command.count("-i") == 1
    assert command[command.index("-i") + 1] == "mic"
    assert command[-1] == "voice.wav"
    assert "-filter_complex" not in command


def test_meeting_recording_command_mixes_monitor() -> None:
    command = recording_command(
        Path("meeting.wav"),
        AudioConfig(microphone="mic"),
        include_system_audio=True,
        monitor_source="speakers.monitor",
    )
    assert command.count("-i") == 2
    assert "speakers.monitor" in command
    assert "-filter_complex" in command
    assert "amix=inputs=2" in command[command.index("-filter_complex") + 1]


def test_meeting_refuses_to_start_without_speaker_monitor(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audio.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio, "default_monitor_source", lambda: None)
    recorder = AudioRecorder(AudioConfig())
    with pytest.raises(AudioError, match="monitor source"):
        recorder.start(tmp_path / "meeting.wav", include_system_audio=True)
