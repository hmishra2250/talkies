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


class SilentTranscriber(FakeTranscriber):
    def transcribe(self, _path: Path):
        return "", {"model": "fake"}


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


class FakeHotkeys:
    running = True

    def __init__(self) -> None:
        self.hold_key = "Control_R"

    def recommendation(self) -> str:
        return "Hyper_L"

    def begin_capture(self) -> dict[str, str]:
        return {"state": "waiting"}

    def capture_status(self) -> dict[str, str]:
        return {"state": "captured", "binding": "Hyper_L"}

    def set_hold_key(self, binding: str) -> None:
        self.hold_key = binding


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


def test_dashboard_copy_does_not_claim_paste(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    transcript_id = instance.store.save(
        mode="dictation", text="remember this", audio_path=None,
        note_path=None, metadata={}, status="cancelled",
    )
    result = instance.handle({"command": "paste_transcript", "transcript_id": transcript_id})
    assert result["delivery"] == "clipboard"
    assert instance.injector.value == "remember this"
    assert instance.store.get(transcript_id)["status"] == "cancelled"


def test_dictionary_updates_persist_and_apply_to_dictation(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    result = instance.handle(
        {"command": "set_replacement", "phrase": "hello", "replacement": "Howdy"}
    )
    assert result["ok"] is True
    assert instance.config.replacements["hello"] == "Howdy"
    assert "Howdy" in (tmp_path / "config/talkies/config.toml").read_text()
    instance.handle({"command": "start", "mode": "dictation"})
    finished = instance.handle({"command": "stop"})
    assert finished["text"] == "Howdy\nworld"


def test_hotkey_capture_and_save_apply_live_and_persist(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    listener = FakeHotkeys()
    instance.hotkeys = listener
    assert instance.hotkey_info()["recommendation"] == "Hyper_L"
    assert instance.start_hotkey_capture() == {"state": "waiting"}
    assert instance.hotkey_capture_status()["binding"] == "Hyper_L"
    instance.set_hotkey("Super_L + Shift_L + F23")
    assert listener.hold_key == "Super_L+Shift_L+F23"
    assert instance.config.behavior.hold_key == listener.hold_key
    assert (tmp_path / "config/talkies/config.toml").exists()
    assert 'hold_key = "Super_L+Shift_L+F23"' in (
        tmp_path / "config/talkies/config.toml"
    ).read_text()


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
    states: list[dict[str, object]] = []
    monkeypatch.setattr("talkies.daemon.publish", states.append)
    instance.handle({"command": "start", "mode": "dictation"})
    path = instance.active.path
    failed = instance.handle({"command": "stop"})
    assert failed["ok"] is False
    assert instance.status()["state"] == "idle"
    assert not path.exists()
    assert states[-1] == {
        "status": "ready", "message": "Recording ended", "detail": "No audio captured"
    }


def test_no_speech_recording_closes_listening_overlay(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.transcriber = SilentTranscriber()
    states: list[dict[str, object]] = []
    monkeypatch.setattr("talkies.daemon.publish", states.append)
    instance.handle({"command": "start", "mode": "dictation"})
    finished = instance.handle({"command": "stop"})
    assert finished["ok"] is True
    assert finished["text"] == ""
    assert instance.status()["state"] == "idle"
    assert states[-1] == {"status": "ready", "message": "No speech detected", "detail": "Try again"}


def _write_wav(path: Path, seconds: float = 1.0) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * int(16000 * seconds))


def test_model_is_not_recycled_while_a_long_recording_transcribes(
    tmp_path: Path, monkeypatch
) -> None:
    instance = service(tmp_path, monkeypatch)
    instance.config.transcription.model_idle_timeout_seconds = 60
    recycle_checks: list[bool] = []

    class WatchedTranscriber(FakeTranscriber):
        def transcribe(self, path: Path):
            # The recording outlasted the idle timeout before transcription began.
            recycle_checks.append(instance.should_recycle_model(now=10**9))
            return super().transcribe(path)

    instance.transcriber = WatchedTranscriber()
    instance.handle({"command": "start", "mode": "dictation"})
    finished = instance.handle({"command": "stop"})
    assert recycle_checks == [False]
    assert finished["text"] == "hello\nworld"


def test_startup_recovers_interrupted_audio_and_retry_transcribes_it(
    tmp_path: Path, monkeypatch
) -> None:
    service(tmp_path, monkeypatch)
    recording = tmp_path / "data" / "talkies" / "recordings"
    lost = recording / "2026-09-30_20-49-29-000082-dictation.wav"
    _write_wav(lost, seconds=2)
    (recording / "2026-09-30_20-50-00-000000-dictation.wav").write_bytes(b"")

    instance = TalkiesService(Config())
    instance.transcriber = FakeTranscriber()
    failed = instance.store.search(status="failed")
    assert [item["audio_path"] for item in failed] == [str(lost)]
    assert failed[0]["created_at"].startswith("2026-09-30T20:49:29")
    assert failed[0]["duration_seconds"] == 2

    retried = instance.handle({"command": "retry_transcript", "transcript_id": failed[0]["id"]})
    assert retried["ok"] is True
    item = instance.store.get(int(failed[0]["id"]))
    assert item["status"] == "saved"
    assert item["text"] == "hello\nworld"
    assert lost.exists()

    again = TalkiesService(Config())
    assert again.store.search(status="failed") == []


def test_retry_reports_missing_audio(tmp_path: Path, monkeypatch) -> None:
    instance = service(tmp_path, monkeypatch)
    transcript_id = instance.store.save(
        mode="dictation",
        text="",
        audio_path=tmp_path / "gone.wav",
        note_path=None,
        metadata={},
        status="failed",
    )
    response = instance.handle({"command": "retry_transcript", "transcript_id": transcript_id})
    assert response["ok"] is False
    assert "no longer available" in response["error"]
