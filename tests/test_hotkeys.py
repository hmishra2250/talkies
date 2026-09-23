from talkies.hotkeys import (
    HoldKeyStateMachine,
    KeyEvent,
    iter_xinput_events,
    parse_xmodmap,
)


def commands(actions):
    return [action.command for action in actions]


def event(kind: str, key: str, when: float) -> KeyEvent:
    return KeyEvent(kind, key, when)


def test_hold_release_starts_after_delay_and_stops_on_release() -> None:
    machine = HoldKeyStateMachine(start_delay_seconds=0.12)
    assert commands(machine.feed(event("press", "Control_R", 10.0))) == []
    assert commands(machine.tick(10.11)) == []
    assert commands(machine.tick(10.12)) == ["start"]
    assert commands(machine.feed(event("release", "Control_R", 10.8))) == ["stop"]


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
