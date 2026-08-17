from pathlib import Path

from talkies.config import Config
from talkies.daemon import TalkiesService


class FakeRecorder:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.system_audio = False

    def start(self, path: Path, *, include_system_audio: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF" + b"0" * 100)
        self.path = path
        self.system_audio = include_system_audio

    def stop(self) -> Path:
        assert self.path is not None
        return self.path


class FakeTranscriber:
    loaded = True
    backend = "cpu/int8"

    def transcribe(self, _path: Path):
        return "hello new line world", {"model": "fake"}


class FailingTranscriber(FakeTranscriber):
    def transcribe(self, _path: Path):
        raise RuntimeError("model failed")


class FakeInjector:
    def __init__(self) -> None:
        self.value = ""
        self.target = None

    def capture_target(self) -> str:
        return "42"

    def inject(self, text: str, *, paste: bool = True, target_window_id: str | None = None) -> None:
        self.value = text
        self.target = target_window_id


def service(tmp_path: Path, monkeypatch) -> TalkiesService:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    instance = TalkiesService(Config())
    instance.recorder = FakeRecorder()
    instance.transcriber = FakeTranscriber()
    instance.injector = FakeInjector()
    return instance


def test_dictation_toggle_records_transcribes_and_injects(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    started = instance.handle({"command": "toggle", "mode": "dictation"})
    assert started["state"] == "recording"
    finished = instance.handle({"command": "toggle", "mode": "dictation"})
    assert finished["text"] == "hello\nworld"
    assert instance.injector.value == "hello\nworld"
    assert instance.injector.target == "42"
    assert finished["audio_path"] is None


def test_meeting_uses_system_audio_and_writes_note(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.handle({"command": "start", "mode": "meeting"})
    assert instance.recorder.system_audio is True
    finished = instance.handle({"command": "stop"})
    assert finished["note_path"]
    assert Path(finished["note_path"]).exists()
    assert finished["audio_path"]


def test_failed_transcription_reports_and_keeps_raw_audio(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.transcriber = FailingTranscriber()
    instance.handle({"command": "start", "mode": "note"})
    path = instance.active.path
    finished = instance.handle({"command": "stop"})
    assert finished["ok"] is False
    assert "Raw audio was kept" in finished["error"]
    assert path.exists()
