from __future__ import annotations

import queue
from typing import Any

from talkies import overlay


class FakeProcess:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.terminated = False

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


def test_recording_state_defaults_to_cancelable() -> None:
    state = overlay._normalise_state("recording")

    assert state["status"] == "recording"
    assert state["message"] == "Listening"
    assert state["show_cancel"] is True
    assert state["auto_hide_ms"] == 0


def test_ready_state_hides_without_cancel() -> None:
    state = overlay._normalise_state({"status": "ready", "message": "Pasted", "detail": "12 words"})

    assert state["message"] == "Pasted"
    assert state["detail"] == "12 words"
    assert state["show_cancel"] is False
    assert state["auto_hide_ms"] > 0


def test_handle_publish_enqueues_normalised_state() -> None:
    states: queue.Queue[dict[str, Any]] = queue.Queue()
    actions: queue.Queue[str] = queue.Queue()
    handle = overlay.OverlayHandle(
        state_queue=states,
        action_queue=actions,
        process=FakeProcess(),
        reader_thread=None,
        callback=lambda _action: None,
    )

    handle.publish({"status": "transcribing", "detail": "small.en"})

    state = states.get_nowait()
    assert state["status"] == "transcribing"
    assert state["message"] == "Transcribing"
    assert state["detail"] == "small.en"
    assert state["show_cancel"] is False


def test_action_reader_forwards_cancel_without_escape_binding() -> None:
    states: queue.Queue[object] = queue.Queue()
    actions: queue.Queue[str] = queue.Queue()
    received: list[str] = []
    handle = overlay.OverlayHandle(
        state_queue=states,
        action_queue=actions,
        process=FakeProcess(),
        reader_thread=None,
        callback=received.append,
    )
    actions.put("cancel")
    actions.put(overlay._STOP)

    overlay._read_actions(handle)

    assert received == ["cancel"]


def test_stop_signals_overlay_and_terminates_stubborn_process() -> None:
    states: queue.Queue[str] = queue.Queue()
    actions: queue.Queue[str] = queue.Queue()
    process = FakeProcess()
    handle = overlay.OverlayHandle(
        state_queue=states,
        action_queue=actions,
        process=process,
        reader_thread=None,
        callback=lambda _action: None,
    )

    handle.stop()

    assert states.get_nowait() == overlay._STOP
    assert actions.get_nowait() == overlay._STOP
    assert process.terminated is True


def test_start_overlay_falls_back_to_noop_when_process_cannot_start(monkeypatch) -> None:
    overlay.stop_overlay()

    def fail_context(_method: str) -> object:
        raise RuntimeError("spawn unavailable")

    monkeypatch.setattr(overlay.multiprocessing, "get_context", fail_context)

    handle = overlay.start_overlay(lambda _action: None)
    overlay.publish("recording")

    assert handle.process is None
    assert handle.running is False

    overlay.stop_overlay()
