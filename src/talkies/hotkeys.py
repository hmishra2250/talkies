from __future__ import annotations

import logging
import os
import re
import selectors
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

LOG = logging.getLogger(__name__)

HotkeyCallback = Callable[[str], None]

KEY_PRESS = "press"
KEY_RELEASE = "release"
DEFAULT_HOLD_KEY = "Control_R"
DEFAULT_DOUBLE_TAP_SECONDS = 0.35
DEFAULT_START_DELAY_SECONDS = 0.12
DEFAULT_TAP_MAX_SECONDS = 0.25
DEFAULT_CAPTURE_MODIFIER_DEBOUNCE_SECONDS = 0.2

_EVENT_RE = re.compile(r"EVENT type \d+ \((KeyPress|KeyRelease)\)")
_DETAIL_RE = re.compile(r"detail:\s+(\d+)")
_XMODMAP_RE = re.compile(r"keycode\s+(\d+)\s+=\s+(.+)")
_KEYCODE_BINDING_RE = re.compile(r"keycode:(\d+)\Z")
_KEYSYM_BINDING_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
MAX_BINDING_PARTS = 5

CaptureState = Literal["idle", "waiting", "captured", "timeout"]

_MODIFIER_KEYS = {
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Meta_L",
    "Meta_R",
    "Super_L",
    "Super_R",
    "Hyper_L",
    "Hyper_R",
    "ISO_Level3_Shift",
    "Mode_switch",
    "Caps_Lock",
}

_RECOMMENDED_KEYS = (
    "F23",
    "F24",
    "Hyper_L",
    "Hyper_R",
    "XF86Assistant",
    "XF86Copilot",
    "XF86LaunchA",
    "XF86LaunchB",
    "XF86Tools",
    "Super_R",
    "Meta_R",
    "Alt_R",
    "Control_R",
    "Menu",
)

_OBSERVED_RECOMMENDATION_KEYS = {
    "F23",
    "F24",
    "Hyper_L",
    "Hyper_R",
    "XF86Assistant",
    "XF86Copilot",
}

_BARE_OBSERVED_RECOMMENDATION_KEYS = {
    "Hyper_L",
    "Hyper_R",
}


@dataclass(frozen=True, slots=True)
class KeyEvent:
    kind: str
    key: str
    when: float
    keycode: int | None = None


@dataclass(frozen=True, slots=True)
class HotkeyAction:
    command: str
    when: float


@dataclass(frozen=True, slots=True)
class HotkeyBinding:
    value: str
    parts: tuple[str, ...]

    @property
    def is_chord(self) -> bool:
        return len(self.parts) > 1


def normalize_binding(binding: str) -> str:
    parts = _parse_binding_parts(binding)
    return "+".join(parts)


def validate_binding(binding: str) -> bool:
    try:
        _parse_binding_parts(binding)
    except ValueError:
        return False
    return True


def parse_binding(binding: str) -> HotkeyBinding:
    parts = _parse_binding_parts(binding)
    return HotkeyBinding("+".join(parts), parts)


def recommended_hold_binding(keymap: dict[int, tuple[str, ...]] | None = None) -> str:
    mapping = resolve_keymap() if keymap is None else keymap
    available = {name for names in mapping.values() for name in names}
    for name in _RECOMMENDED_KEYS:
        if name in available:
            return name
    return DEFAULT_HOLD_KEY


def recommendation(keymap: dict[int, tuple[str, ...]] | None = None) -> str:
    return recommended_hold_binding(keymap)


def _parse_binding_parts(binding: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in binding.split("+"))
    if not parts:
        raise ValueError("Hotkey binding cannot be empty")
    if len(parts) > MAX_BINDING_PARTS:
        raise ValueError("Hotkey binding has too many parts")
    seen: set[str] = set()
    for part in parts:
        if not part:
            raise ValueError("Hotkey binding cannot contain empty parts")
        if part in seen:
            raise ValueError(f"Duplicate hotkey binding part: {part}")
        seen.add(part)
        match = _KEYCODE_BINDING_RE.match(part)
        if match:
            if int(match.group(1)) <= 0:
                raise ValueError(f"Invalid keycode binding part: {part}")
            continue
        if not _KEYSYM_BINDING_RE.match(part):
            raise ValueError(f"Invalid hotkey binding part: {part}")
    return parts


class HoldKeyStateMachine:
    """Turn a solo hold key into start/stop actions without stealing modifier shortcuts."""

    def __init__(
        self,
        hold_key: str = DEFAULT_HOLD_KEY,
        *,
        double_tap_seconds: float = DEFAULT_DOUBLE_TAP_SECONDS,
        start_delay_seconds: float = DEFAULT_START_DELAY_SECONDS,
        tap_max_seconds: float = DEFAULT_TAP_MAX_SECONDS,
    ) -> None:
        self.hold_key = normalize_binding(hold_key)
        self._binding = parse_binding(hold_key)
        self.double_tap_seconds = double_tap_seconds
        self.start_delay_seconds = start_delay_seconds
        self.tap_max_seconds = tap_max_seconds
        self._binding_down_parts: set[str] = set()
        self._hold_down = False
        self._pending_start = False
        self._started_by_hold = False
        self._locked = False
        self._modified = False
        self._press_time = 0.0
        self._last_tap_time: float | None = None

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def recording(self) -> bool:
        return self._started_by_hold or self._locked

    def set_hold_key(self, hold_key: str) -> None:
        self.hold_key = normalize_binding(hold_key)
        self._binding = parse_binding(hold_key)
        self._binding_down_parts.clear()
        self._hold_down = False
        self._reset_recording()

    def feed(self, event: KeyEvent) -> list[HotkeyAction]:
        actions = self.tick(event.when)
        if event.kind == KEY_PRESS:
            actions.extend(self._press(event))
        elif event.kind == KEY_RELEASE:
            actions.extend(self._release(event))
        return actions

    def tick(self, when: float | None = None) -> list[HotkeyAction]:
        current = time.monotonic() if when is None else when
        elapsed = round(current - self._press_time, 6)
        if (
            self._pending_start
            and self._hold_down
            and not self._modified
            and elapsed >= self.start_delay_seconds
        ):
            self._pending_start = False
            self._started_by_hold = True
            self._last_tap_time = None
            return [HotkeyAction("start", current)]
        return []

    def _press(self, event: KeyEvent) -> list[HotkeyAction]:
        matches = self._matching_binding_parts(event)
        if matches:
            was_active = self._binding_is_down()
            self._binding_down_parts.update(matches)
            if not was_active and self._binding_is_down():
                return self._press_hold(event.when)
            return []
        if self._binding_is_down() or self._hold_down:
            self._modified = True
            self._pending_start = False
        if self._binding.value == event.key:
            return self._press_hold(event.when)
        return []

    def _press_hold(self, when: float) -> list[HotkeyAction]:
        if self._hold_down:
            return []
        if self._locked:
            self._locked = False
            self._last_tap_time = None
            return [HotkeyAction("stop", when)]
        if (
            self._last_tap_time is not None
            and when - self._last_tap_time <= self.double_tap_seconds
        ):
            self._hold_down = True
            self._modified = False
            self._pending_start = False
            self._locked = True
            self._last_tap_time = None
            return [HotkeyAction("start", when)]
        self._hold_down = True
        self._modified = False
        self._pending_start = True
        self._press_time = when
        return []

    def _release(self, event: KeyEvent) -> list[HotkeyAction]:
        matches = self._matching_binding_parts(event)
        if matches:
            was_active = self._binding_is_down()
            self._binding_down_parts.difference_update(matches)
            if was_active and not self._binding_is_down():
                return self._release_hold(event.when)
            return []
        return []

    def _release_hold(self, when: float) -> list[HotkeyAction]:
        if not self._hold_down:
            return []
        self._hold_down = False
        was_pending = self._pending_start
        self._pending_start = False
        if self._locked:
            return []
        if self._started_by_hold:
            self._started_by_hold = False
            self._last_tap_time = None
            return [HotkeyAction("stop", when)]
        if (
            was_pending
            and not self._modified
            and when - self._press_time <= self.tap_max_seconds
        ):
            self._last_tap_time = when
        elif self._modified:
            self._last_tap_time = None
        return []

    def _matching_binding_parts(self, event: KeyEvent) -> set[str]:
        event_tokens = {event.key}
        if event.keycode is not None:
            event_tokens.add(f"keycode:{event.keycode}")
        return set(self._binding.parts).intersection(event_tokens)

    def _binding_is_down(self) -> bool:
        return set(self._binding.parts).issubset(self._binding_down_parts)

    def _reset_recording(self) -> None:
        self._pending_start = False
        self._started_by_hold = False
        self._locked = False
        self._last_tap_time = None


class XInputHotkeyListener:
    """Listen to X11 key events from xinput and call back with hotkey actions."""

    def __init__(
        self,
        callback: HotkeyCallback,
        *,
        hold_key: str = DEFAULT_HOLD_KEY,
        xinput_command: tuple[str, ...] = ("xinput", "test-xi2", "--root"),
        poll_seconds: float = 0.02,
        capture_modifier_debounce_seconds: float = DEFAULT_CAPTURE_MODIFIER_DEBOUNCE_SECONDS,
    ) -> None:
        self.callback = callback
        self.hold_key = hold_key
        self.xinput_command = xinput_command
        self.poll_seconds = poll_seconds
        self.capture_modifier_debounce_seconds = capture_modifier_debounce_seconds
        self.machine = HoldKeyStateMachine(hold_key)
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._keymap: dict[int, tuple[str, ...]] = {}
        self._pressed: dict[str, KeyEvent] = {}
        self._capture_state: CaptureState = "idle"
        self._capture_binding: str | None = None
        self._capture_deadline = 0.0
        self._capture_events: list[KeyEvent] = []
        self._pending_capture_binding: str | None = None
        self._pending_capture_deadline = 0.0
        self._observed_recommendation: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=1)

    def set_hold_key(self, binding: str) -> None:
        normalized = normalize_binding(binding)
        with self._lock:
            self.hold_key = normalized
            self.machine.set_hold_key(normalized)

    def begin_capture(self, timeout_seconds: float = 8) -> dict[str, str]:
        with self._lock:
            self._capture_state = "waiting"
            self._capture_binding = None
            self._capture_deadline = time.monotonic() + timeout_seconds
            self._capture_events.clear()
            self._pending_capture_binding = None
            self._pending_capture_deadline = 0.0
            self._pressed.clear()
            self.machine.set_hold_key(self.hold_key)
            return self.capture_status()

    def capture_status(self) -> dict[str, str]:
        with self._lock:
            now = time.monotonic()
            self._expire_capture(now)
            self._finalize_pending_capture(now)
            status = {"state": self._capture_state}
            if self._capture_binding:
                status["binding"] = self._capture_binding
            return status

    def recommendation(self) -> str:
        with self._lock:
            if self._observed_recommendation:
                return self._observed_recommendation
            return recommended_hold_binding(self._keymap)

    def run_forever(self) -> None:
        keymap = resolve_keymap()
        with self._lock:
            self._keymap = keymap
        if not _binding_found_in_keymap(self.hold_key, keymap):
            LOG.warning("Hold key %s was not found in the X11 keymap", self.hold_key)
        try:
            self._process = subprocess.Popen(
                self.xinput_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except FileNotFoundError:
            LOG.warning("xinput is not installed; hold hotkey listener is disabled")
            return
        if self._process.stdout is None:
            return
        try:
            parser = XInputEventParser(keymap=keymap, include_unknown=True)
            selector = selectors.DefaultSelector()
            selector.register(self._process.stdout, selectors.EVENT_READ)
            pending = b""
            while not self._stop.is_set() and self._process.poll() is None:
                for key, _mask in selector.select(timeout=self.poll_seconds):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        event = parser.feed_line(line.decode("utf-8", errors="replace"))
                        if event:
                            self._handle_event(event)
                with self._lock:
                    now = time.monotonic()
                    self._expire_capture(now)
                    self._finalize_pending_capture(now)
                    if self._capture_state == "waiting":
                        continue
                    actions = self.machine.tick()
                self._emit(actions)
        finally:
            selector.close()
            if self._process.poll() is None:
                self._process.terminate()
            self._process.wait(timeout=1)

    def _handle_event(self, event: KeyEvent) -> None:
        actions: list[HotkeyAction] = []
        with self._lock:
            self._track_pressed(event)
            self._observe_recommendation(event)
            if self._capture_state == "waiting":
                self._handle_capture_event(event)
                return
            actions = self.machine.feed(event)
        self._emit(actions)

    def _track_pressed(self, event: KeyEvent) -> None:
        token = _event_binding_token(event)
        if event.kind == KEY_PRESS:
            self._pressed[token] = event
        elif event.kind == KEY_RELEASE:
            self._pressed.pop(token, None)

    def _handle_capture_event(self, event: KeyEvent) -> None:
        now = event.when
        if self._expire_capture(now):
            return
        if self._pending_capture_binding:
            if self._finalize_pending_capture(now):
                return
            self._pending_capture_binding = None
            self._pending_capture_deadline = 0.0
            self._capture_events.clear()
        if event.kind == KEY_PRESS:
            if not any(_same_physical_key(event, previous) for previous in self._capture_events):
                self._capture_events.append(event)
            if not _is_modifier(event.key):
                self._finish_capture(_binding_from_events(self._capture_events, event))
        elif event.kind == KEY_RELEASE and self._capture_events:
            pressed_keys = {_event_binding_token(pressed) for pressed in self._capture_events}
            if _event_binding_token(event) in pressed_keys and all(
                _is_modifier(pressed.key) for pressed in self._capture_events
            ):
                self._pending_capture_binding = _event_binding_token(self._capture_events[0])
                self._pending_capture_deadline = now + self.capture_modifier_debounce_seconds

    def _expire_capture(self, now: float) -> bool:
        if self._capture_state == "waiting" and now >= self._capture_deadline:
            self._capture_state = "timeout"
            self._capture_binding = None
            self._capture_events.clear()
            self._pending_capture_binding = None
            self._pending_capture_deadline = 0.0
            self._pressed.clear()
            self.machine.set_hold_key(self.hold_key)
            return True
        return False

    def _finalize_pending_capture(self, now: float) -> bool:
        if (
            self._capture_state == "waiting"
            and self._pending_capture_binding
            and now >= self._pending_capture_deadline
        ):
            self._finish_capture(self._pending_capture_binding)
            self._pending_capture_binding = None
            self._pending_capture_deadline = 0.0
            return True
        return False

    def _finish_capture(self, binding: str) -> None:
        self._capture_binding = normalize_binding(binding)
        if _is_observed_recommendation_binding(self._capture_binding):
            self._observed_recommendation = self._capture_binding
        self._capture_state = "captured"
        self._capture_events.clear()
        self._pending_capture_binding = None
        self._pending_capture_deadline = 0.0

    def _observe_recommendation(self, event: KeyEvent) -> None:
        if self._observed_recommendation or event.kind != KEY_PRESS:
            return
        if event.key in _BARE_OBSERVED_RECOMMENDATION_KEYS:
            self._observed_recommendation = _event_binding_token(event)
        elif event.key in _OBSERVED_RECOMMENDATION_KEYS:
            modifiers = [
                pressed
                for token, pressed in self._pressed.items()
                if token != _event_binding_token(event) and _is_modifier(pressed.key)
            ]
            self._observed_recommendation = _binding_from_events([*modifiers, event], event)

    def _emit(self, actions: Iterable[HotkeyAction]) -> None:
        for action in actions:
            try:
                self.callback(action.command)
            except Exception:
                LOG.exception("Hotkey callback failed for %s", action.command)


def resolve_keymap() -> dict[int, tuple[str, ...]]:
    try:
        result = subprocess.run(
            ("xmodmap", "-pke"),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {}
    if result.returncode != 0:
        return {}
    return parse_xmodmap(result.stdout.splitlines())


def parse_xmodmap(lines: Iterable[str]) -> dict[int, tuple[str, ...]]:
    keymap: dict[int, tuple[str, ...]] = {}
    for line in lines:
        match = _XMODMAP_RE.match(line.strip())
        if not match:
            continue
        names = tuple(name for name in match.group(2).split() if name != "NoSymbol")
        if names:
            keymap[int(match.group(1))] = names
    return keymap


class XInputEventParser:
    def __init__(
        self,
        *,
        keymap: dict[int, tuple[str, ...]],
        clock: Callable[[], float] = time.monotonic,
        include_unknown: bool = False,
    ) -> None:
        self.keymap = keymap
        self.clock = clock
        self.include_unknown = include_unknown
        self._event_kind: str | None = None

    def feed_line(self, line: str) -> KeyEvent | None:
        event_match = _EVENT_RE.search(line)
        if event_match:
            self._event_kind = KEY_PRESS if event_match.group(1) == "KeyPress" else KEY_RELEASE
            return None
        if self._event_kind is None:
            return None
        detail_match = _DETAIL_RE.search(line)
        if not detail_match:
            return None
        kind = self._event_kind
        self._event_kind = None
        keycode = int(detail_match.group(1))
        names = self.keymap.get(keycode, ())
        if not names:
            if self.include_unknown:
                return KeyEvent(kind, f"keycode:{keycode}", self.clock(), keycode)
            return None
        return KeyEvent(kind, names[0], self.clock(), keycode)


def iter_xinput_events(
    lines: Iterable[str],
    *,
    keymap: dict[int, tuple[str, ...]],
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[KeyEvent]:
    parser = XInputEventParser(keymap=keymap, clock=clock)
    for line in lines:
        event = parser.feed_line(line)
        if event:
            yield event


def _binding_found_in_keymap(binding: str, keymap: dict[int, tuple[str, ...]]) -> bool:
    try:
        parsed = parse_binding(binding)
    except ValueError:
        return False
    available = {name for names in keymap.values() for name in names}
    available.update(f"keycode:{keycode}" for keycode in keymap)
    return all(part in available for part in parsed.parts)


def _event_binding_token(event: KeyEvent) -> str:
    if event.key.startswith("keycode:"):
        return event.key
    return event.key


def _same_physical_key(left: KeyEvent, right: KeyEvent) -> bool:
    if left.keycode is not None and right.keycode is not None:
        return left.keycode == right.keycode
    return left.key == right.key


def _is_modifier(key: str) -> bool:
    return key in _MODIFIER_KEYS


def _is_observed_recommendation_binding(binding: str) -> bool:
    try:
        parsed = parse_binding(binding)
    except ValueError:
        return False
    return any(part in _OBSERVED_RECOMMENDATION_KEYS for part in parsed.parts)


def _binding_from_events(events: Iterable[KeyEvent], base_event: KeyEvent) -> str:
    base_token = _event_binding_token(base_event)
    modifiers: list[str] = []
    for event in events:
        token = _event_binding_token(event)
        if token == base_token or not _is_modifier(event.key):
            continue
        if token not in modifiers:
            modifiers.append(token)
    return "+".join([*modifiers, base_token])
