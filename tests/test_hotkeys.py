import threading
import time

from talkies.hotkeys import (
    HoldKeyStateMachine,
    KeyEvent,
    XInputEventParser,
    XInputHotkeyListener,
    iter_xinput_events,
    parse_xmodmap,
    recommendation,
    validate_binding,
)


def commands(actions):
    return [action.command for action in actions]


def event(kind: str, key: str, when: float) -> KeyEvent:
    return KeyEvent(kind, key, when)


class TrackingLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.depth = 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        return self

    def __exit__(self, *args):
        self.depth -= 1
        self._lock.release()


def test_hold_release_starts_after_delay_and_stops_on_release() -> None:
    machine = HoldKeyStateMachine(start_delay_seconds=0.12)
    assert commands(machine.feed(event("press", "Control_R", 10.0))) == []
    assert commands(machine.tick(10.11)) == []
    assert commands(machine.tick(10.12)) == ["start"]
    assert commands(machine.feed(event("release", "Control_R", 10.8))) == ["stop"]


def test_keycode_binding_can_hold_and_release() -> None:
    machine = HoldKeyStateMachine("keycode:207", start_delay_seconds=0.12)
    assert commands(machine.feed(KeyEvent("press", "Hyper_L", 10.0, 207))) == []
    assert commands(machine.tick(10.12)) == ["start"]
    assert commands(machine.feed(KeyEvent("release", "Hyper_L", 10.8, 207))) == ["stop"]


def test_chord_binding_waits_for_all_parts_and_stops_on_release() -> None:
    machine = HoldKeyStateMachine("Super_L+Shift_L+F23", start_delay_seconds=0.12)
    assert commands(machine.feed(KeyEvent("press", "Super_L", 1.0, 133))) == []
    assert commands(machine.feed(KeyEvent("press", "Shift_L", 1.01, 50))) == []
    assert commands(machine.feed(KeyEvent("press", "F23", 1.02, 191))) == []
    assert commands(machine.tick(1.14)) == ["start"]
    assert commands(machine.feed(KeyEvent("release", "F23", 1.5, 191))) == ["stop"]


def test_set_hold_key_resets_state_and_uses_new_binding() -> None:
    machine = HoldKeyStateMachine("Control_R", start_delay_seconds=0.12)
    assert commands(machine.feed(event("press", "Control_R", 1.0))) == []
    machine.set_hold_key("Hyper_L")
    assert commands(machine.tick(1.2)) == []
    assert commands(machine.feed(event("press", "Hyper_L", 2.0))) == []
    assert commands(machine.tick(2.12)) == ["start"]


def test_quick_double_tap_locks_then_next_tap_stops() -> None:
    machine = HoldKeyStateMachine()
    assert commands(machine.feed(event("press", "Control_R", 1.0))) == []
    assert commands(machine.feed(event("release", "Control_R", 1.05))) == []
    assert commands(machine.feed(event("press", "Control_R", 1.2))) == ["start"]
    assert machine.locked is True
    assert commands(machine.feed(event("release", "Control_R", 1.25))) == []
    assert commands(machine.feed(event("press", "Control_R", 3.0))) == ["stop"]
    assert machine.locked is False


def test_slow_second_tap_does_not_lock() -> None:
    machine = HoldKeyStateMachine(double_tap_seconds=0.35)
    assert commands(machine.feed(event("press", "Control_R", 1.0))) == []
    assert commands(machine.feed(event("release", "Control_R", 1.05))) == []
    assert commands(machine.feed(event("press", "Control_R", 1.5))) == []
    assert machine.locked is False


def test_right_ctrl_modifier_shortcut_does_not_start_or_stop() -> None:
    machine = HoldKeyStateMachine(start_delay_seconds=0.12)
    assert commands(machine.feed(event("press", "Control_R", 20.0))) == []
    assert commands(machine.feed(event("press", "s", 20.04))) == []
    assert commands(machine.tick(20.2)) == []
    assert commands(machine.feed(event("release", "s", 20.25))) == []
    assert commands(machine.feed(event("release", "Control_R", 20.3))) == []
    assert machine.recording is False


def test_key_repeat_is_ignored_while_hold_key_is_down() -> None:
    machine = HoldKeyStateMachine(start_delay_seconds=0.12)
    assert commands(machine.feed(event("press", "Control_R", 1.0))) == []
    assert commands(machine.feed(event("press", "Control_R", 1.02))) == []
    assert commands(machine.tick(1.13)) == ["start"]
    assert commands(machine.feed(event("release", "Control_R", 1.4))) == ["stop"]


def test_cancel_key_cancels_active_hold_recording() -> None:
    machine = HoldKeyStateMachine(cancel_key="BackSpace", start_delay_seconds=0.1)
    assert commands(machine.feed(event("press", "Control_R", 1.0))) == []
    assert commands(machine.tick(1.1)) == ["start"]
    assert commands(machine.feed(event("press", "BackSpace", 1.2))) == ["cancel"]
    assert machine.recording is False
    assert commands(machine.feed(event("release", "Control_R", 1.3))) == []


def test_undo_key_emits_only_while_idle() -> None:
    machine = HoldKeyStateMachine(undo_key="Menu", start_delay_seconds=0.1)
    assert commands(machine.feed(event("press", "Menu", 1.0))) == ["undo"]
    assert commands(machine.feed(event("press", "Control_R", 2.0))) == []
    assert commands(machine.tick(2.1)) == ["start"]
    assert commands(machine.feed(event("press", "Menu", 2.2))) == []


def test_parse_xmodmap_ignores_no_symbol_entries() -> None:
    keymap = parse_xmodmap(
        [
            "keycode 105 = Control_R NoSymbol Control_R",
            "keycode 38 = a A a A",
        ]
    )
    assert keymap[105] == ("Control_R", "Control_R")
    assert keymap[38] == ("a", "A", "a", "A")


def test_iter_xinput_events_reads_keypress_and_keyrelease_details() -> None:
    lines = [
        "EVENT type 2 (KeyPress)",
        "    device: 3 (10)",
        "    detail: 105",
        "EVENT type 3 (KeyRelease)",
        "    detail: 105",
    ]
    events = list(iter_xinput_events(lines, keymap={105: ("Control_R",)}, clock=lambda: 42.0))
    assert events == [
        KeyEvent("press", "Control_R", 42.0, 105),
        KeyEvent("release", "Control_R", 42.0, 105),
    ]


def test_iter_xinput_events_ignores_unknown_keycodes() -> None:
    lines = ["EVENT type 2 (KeyPress)", "    detail: 999"]
    assert list(iter_xinput_events(lines, keymap={})) == []


def test_parser_can_return_unknown_keycodes_for_listener_capture() -> None:
    parser = XInputEventParser(keymap={}, include_unknown=True, clock=lambda: 7.0)
    assert parser.feed_line("EVENT type 2 (KeyPress)") is None
    assert parser.feed_line("    detail: 207") == KeyEvent("press", "keycode:207", 7.0, 207)


def test_validate_binding_accepts_names_chords_and_keycodes() -> None:
    assert validate_binding("Control_R") is True
    assert validate_binding("Super_L+Shift_L+F23") is True
    assert validate_binding("keycode:207") is True
    assert validate_binding("") is False
    assert validate_binding("a++b") is False
    assert validate_binding("+Control_R") is False
    assert validate_binding("Control_R+") is False
    assert validate_binding("keycode:0") is False
    assert validate_binding("Control-R") is False
    assert validate_binding("a+b+c+d+e+f") is False
    assert validate_binding("Super_L+Super_L") is False


def test_recommendation_prefers_copilot_like_key() -> None:
    keymap = {
        105: ("Control_R",),
        207: ("Hyper_L",),
    }
    assert recommendation(keymap) == "Hyper_L"


def test_listener_capture_records_copilot_chord() -> None:
    captured: list[str] = []
    listener = XInputHotkeyListener(captured.append)
    listener.begin_capture()
    listener._handle_event(KeyEvent("press", "Super_L", 1.0, 133))
    listener._handle_event(KeyEvent("press", "Shift_L", 1.01, 50))
    listener._handle_event(KeyEvent("press", "F23", 1.02, 191))
    assert listener.capture_status() == {
        "state": "captured",
        "binding": "Super_L+Shift_L+F23",
    }
    assert captured == []


def test_listener_capture_keeps_copilot_chord_when_modifiers_release_after_capture() -> None:
    listener = XInputHotkeyListener(lambda _command: None)
    listener.begin_capture()
    listener._handle_event(KeyEvent("press", "Super_L", 1.0, 133))
    listener._handle_event(KeyEvent("press", "Shift_L", 1.01, 50))
    listener._handle_event(KeyEvent("press", "F23", 1.02, 191))
    listener._handle_event(KeyEvent("release", "Super_L", 1.03, 133))
    listener._handle_event(KeyEvent("release", "Shift_L", 1.04, 50))
    assert listener.capture_status() == {
        "state": "captured",
        "binding": "Super_L+Shift_L+F23",
    }


def test_listener_capture_records_single_modifier_on_release() -> None:
    listener = XInputHotkeyListener(lambda _command: None)
    now = time.monotonic()
    listener.begin_capture()
    listener._handle_event(KeyEvent("press", "Hyper_L", now, 207))
    assert listener.capture_status() == {"state": "waiting"}
    listener._handle_event(KeyEvent("release", "Hyper_L", now + 0.05, 207))
    assert listener.capture_status() == {"state": "waiting"}
    listener._pending_capture_deadline = 0
    assert listener.capture_status() == {"state": "captured", "binding": "Hyper_L"}


def test_listener_capture_replaces_transient_modifier_with_following_hyper_key() -> None:
    listener = XInputHotkeyListener(lambda _command: None, capture_modifier_debounce_seconds=0.2)
    now = time.monotonic()
    listener.begin_capture()
    listener._handle_event(KeyEvent("press", "Shift_L", now, 50))
    listener._handle_event(KeyEvent("release", "Shift_L", now + 0.02, 50))
    assert listener.capture_status() == {"state": "waiting"}
    listener._handle_event(KeyEvent("press", "Hyper_L", now + 0.10, 207))
    listener._handle_event(KeyEvent("release", "Hyper_L", now + 0.12, 207))
    listener._pending_capture_deadline = 0
    assert listener.capture_status() == {"state": "captured", "binding": "Hyper_L"}
    assert listener.recommendation() == "Hyper_L"


def test_listener_recommends_bare_hyper_even_with_synthetic_modifiers() -> None:
    listener = XInputHotkeyListener(lambda _command: None)
    now = time.monotonic()
    listener.begin_capture(timeout_seconds=0.1)
    listener._handle_event(KeyEvent("press", "Shift_L", now, 50))
    listener._handle_event(KeyEvent("press", "Super_L", now + 0.01, 133))
    listener._handle_event(KeyEvent("press", "Hyper_L", now + 0.02, 207))
    listener._capture_deadline = 0
    assert listener.capture_status() == {"state": "timeout"}
    assert listener.recommendation() == "Hyper_L"


def test_listener_capture_does_not_recommend_accidental_shift() -> None:
    listener = XInputHotkeyListener(lambda _command: None, capture_modifier_debounce_seconds=0.2)
    listener.begin_capture()
    listener._handle_event(KeyEvent("press", "Shift_L", 1.0, 50))
    listener._handle_event(KeyEvent("release", "Shift_L", 1.02, 50))
    listener._pending_capture_deadline = 0
    assert listener.capture_status() == {"state": "captured", "binding": "Shift_L"}
    assert listener.recommendation() == "Control_R"


def test_listener_capture_timeout_resets_pressed_state_before_normal_use() -> None:
    captured: list[str] = []
    listener = XInputHotkeyListener(captured.append, hold_key="Hyper_L")
    listener.begin_capture(timeout_seconds=0.1)
    listener._handle_event(KeyEvent("press", "Hyper_L", 1.0, 207))
    listener._capture_deadline = 0
    assert listener.capture_status() == {"state": "timeout"}
    listener._emit(listener.machine.tick(1.3))
    listener._handle_event(KeyEvent("release", "Hyper_L", 1.4, 207))
    assert captured == []


def test_listener_set_hold_key_rebinds_without_restart() -> None:
    captured: list[str] = []
    listener = XInputHotkeyListener(captured.append)
    listener.set_hold_key("Hyper_L")
    listener._handle_event(KeyEvent("press", "Control_R", 1.0, 105))
    listener._handle_event(KeyEvent("release", "Control_R", 1.2, 105))
    assert captured == []
    listener._handle_event(KeyEvent("press", "Hyper_L", 2.0, 207))
    listener._emit(listener.machine.tick(2.12))
    listener._handle_event(KeyEvent("release", "Hyper_L", 2.5, 207))
    assert captured == ["start", "stop"]


def test_listener_emits_callback_after_releasing_lock() -> None:
    tracker = TrackingLock()
    emitted: list[tuple[str, int]] = []

    def callback(command: str) -> None:
        emitted.append((command, tracker.depth))

    listener = XInputHotkeyListener(callback, hold_key="Hyper_L")
    listener._lock = tracker
    listener._handle_event(KeyEvent("press", "Hyper_L", 1.0, 207))
    listener._handle_event(KeyEvent("release", "Hyper_L", 1.2, 207))

    assert emitted == [("start", 0), ("stop", 0)]
