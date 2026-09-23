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

LOG = logging.getLogger(__name__)

HotkeyCallback = Callable[[str], None]

KEY_PRESS = "press"
KEY_RELEASE = "release"
DEFAULT_HOLD_KEY = "Control_R"
DEFAULT_DOUBLE_TAP_SECONDS = 0.35
DEFAULT_START_DELAY_SECONDS = 0.12
DEFAULT_TAP_MAX_SECONDS = 0.25

_EVENT_RE = re.compile(r"EVENT type \d+ \((KeyPress|KeyRelease)\)")
_DETAIL_RE = re.compile(r"detail:\s+(\d+)")
_XMODMAP_RE = re.compile(r"keycode\s+(\d+)\s+=\s+(.+)")


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


class HoldKeyStateMachine:
    """Turn a solo hold key into start/stop actions without stealing modifier shortcuts."""

    def __init__(
        self,
        hold_key: str = DEFAULT_HOLD_KEY,
        *,
        double_tap_seconds: float = DEFAULT_DOUBLE_TAP_SECONDS,
        start_delay_seconds: float = DEFAULT_START_DELAY_SECONDS,
        tap_max_seconds: float = DEFAULT_TAP_MAX_SECONDS,
        cancel_key: str | None = None,
        undo_key: str | None = None,
    ) -> None:
        self.hold_key = hold_key
        self.double_tap_seconds = double_tap_seconds
        self.start_delay_seconds = start_delay_seconds
        self.tap_max_seconds = tap_max_seconds
        self.cancel_key = cancel_key
        self.undo_key = undo_key
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
        if event.key == self.hold_key:
            return self._press_hold(event.when)
        if self._hold_down:
            self._modified = True
            self._pending_start = False
        if self.cancel_key and event.key == self.cancel_key and self.recording:
            self._reset_recording()
            return [HotkeyAction("cancel", event.when)]
        if self.undo_key and event.key == self.undo_key and not self.recording:
            return [HotkeyAction("undo", event.when)]
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
        if event.key != self.hold_key or not self._hold_down:
            return []
        self._hold_down = False
        was_pending = self._pending_start
        self._pending_start = False
        if self._locked:
            return []
        if self._started_by_hold:
            self._started_by_hold = False
            self._last_tap_time = None
            return [HotkeyAction("stop", event.when)]
        if (
            was_pending
            and not self._modified
            and event.when - self._press_time <= self.tap_max_seconds
        ):
            self._last_tap_time = event.when
        elif self._modified:
            self._last_tap_time = None
        return []

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
        cancel_key: str | None = None,
        undo_key: str | None = None,
        xinput_command: tuple[str, ...] = ("xinput", "test-xi2", "--root"),
        poll_seconds: float = 0.02,
    ) -> None:
        self.callback = callback
        self.hold_key = hold_key
        self.xinput_command = xinput_command
        self.poll_seconds = poll_seconds
        self.machine = HoldKeyStateMachine(
            hold_key,
            cancel_key=cancel_key,
            undo_key=undo_key,
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

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

    def run_forever(self) -> None:
        keymap = resolve_keymap()
        if not any(self.hold_key in names for names in keymap.values()):
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
            parser = XInputEventParser(keymap=keymap)
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
                            self._emit(self.machine.feed(event))
                self._emit(self.machine.tick())
        finally:
            selector.close()
            if self._process.poll() is None:
                self._process.terminate()
            self._process.wait(timeout=1)

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
    ) -> None:
        self.keymap = keymap
        self.clock = clock
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
