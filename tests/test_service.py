from pathlib import Path
from threading import Event

from talkies.audio import AudioError
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


class ColdTranscriber(FakeTranscriber):
    def __init__(self) -> None:
        self.loaded = False
        self.did_load = Event()

    def load(self) -> None:
        self.loaded = True
        self.did_load.set()


class EmptyRecorder(FakeRecorder):
    process = None

    def stop(self) -> Path:
        assert self.path is not None
        self.path.write_bytes(b"")
        raise AudioError("The recording was empty")


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
    monkeypatch.setattr("talkies.daemon.notify", lambda *_args, **_kwargs: None)
    instance = TalkiesService(Config())
    instance.recorder = FakeRecorder()
    instance.transcriber = FakeTranscriber()
    instance.injector = FakeInjector()
    return instance


def test_dictation_toggle_records_transcribes_and_injects(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    started = instance.handle({"command": "toggle", "mode": "dictation", "request_time": 1.0})
    assert started["state"] == "recording"
    finished = instance.handle({"command": "toggle", "mode": "dictation", "request_time": 2.0})
    assert finished["text"] == "hello\nworld"
    assert instance.injector.value == "hello\nworld"
    assert instance.injector.target == "42"
    assert finished["audio_path"] is not None
    assert Path(finished["audio_path"]).exists()


def test_cancel_saves_without_paste_and_undo_restores(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.handle({"command": "start", "mode": "dictation"})
    cancelled = instance.handle({"command": "cancel"})
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    assert instance.injector.value == ""
    assert instance.store.get(cancelled["transcript_id"])["status"] == "cancelled"
    restored = instance.handle({"command": "undo"})
    assert restored["ok"] is True
    assert instance.injector.value == "hello\nworld"
    assert instance.store.get(cancelled["transcript_id"])["status"] == "pasted"


def test_key_repeat_does_not_toggle_recording(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    started = instance.handle({"command": "toggle", "mode": "dictation", "request_time": 10.0})
    repeated = instance.handle({"command": "toggle", "mode": "dictation", "request_time": 10.1})
    assert started["state"] == "recording"
    assert repeated["state"] == "recording"
    assert repeated["ignored"] is True


def test_recording_start_warms_cold_model_in_background(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    cold = ColdTranscriber()
    instance.transcriber = cold
    instance.handle({"command": "start", "mode": "dictation"})
    assert cold.did_load.wait(timeout=1)
    assert cold.loaded is True


def test_loaded_model_recycles_only_after_idle_timeout(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.config.transcription.model_idle_timeout_seconds = 60
    instance._last_model_activity = 100
    assert instance.should_recycle_model(now=159.9) is False
    assert instance.should_recycle_model(now=160) is True
    instance.handle({"command": "start", "mode": "dictation"})
    assert instance.should_recycle_model(now=1000) is False


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


def test_empty_recording_recovers_to_idle(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.recorder = EmptyRecorder()
    instance.handle({"command": "start", "mode": "dictation"})
    path = instance.active.path
    failed = instance.handle({"command": "stop"})
    assert failed["ok"] is False
    assert instance.status()["state"] == "idle"
    assert not path.exists()
