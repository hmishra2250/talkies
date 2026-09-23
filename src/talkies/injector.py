from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

LOG = logging.getLogger(__name__)


class InjectionError(RuntimeError):
    pass


TERMINAL_WINDOW_CLASSES = {
    "alacritty",
    "gnome-terminal",
    "gnome-terminal-server",
    "kitty",
    "konsole",
    "org.gnome.console",
    "terminator",
    "tilix",
    "xterm",
}


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    if input_text is not None:
        # xclip forks a selection-owning child. Never give that child captured pipes:
        # subprocess.run would wait forever for the inherited pipe to close.
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def active_window_id() -> str | None:
    result = _run(["xdotool", "getactivewindow"])
    window_id = result.stdout.strip()
    return window_id if result.returncode == 0 and window_id else None


def window_app_name(window_id: str | None) -> str | None:
    if not window_id or not shutil.which("xprop"):
        return None
    result = _run(["xprop", "-id", window_id, "WM_CLASS"])
    if result.returncode != 0:
        return None
    names = re.findall(r'"([^"]+)"', result.stdout)
    return names[-1] if names else None


def active_window_paste_shortcut(window_id: str | None = None) -> str:
    window_id = window_id or active_window_id()
    if not window_id:
        return "ctrl+v"
    if shutil.which("xprop"):
        result = _run(["xprop", "-id", window_id, "WM_CLASS"])
    else:
        result = _run(["xdotool", "getwindowname", window_id])
    window_class = result.stdout.strip().casefold()
    if any(name in window_class for name in TERMINAL_WINDOW_CLASSES):
        return "ctrl+shift+v"
    return "ctrl+v"


@dataclass(slots=True)
class TextInjector:
    restore_clipboard: bool = True
    restore_delay_ms: int = 350

    def capture_target(self) -> str | None:
        return active_window_id() if shutil.which("xdotool") else None

    def inject(
        self,
        text: str,
        *,
        paste: bool = True,
        target_window_id: str | None = None,
    ) -> None:
        if not text:
            return
        if not shutil.which("xclip"):
            raise InjectionError("xclip is required for clipboard injection")
        previous = ""
        if self.restore_clipboard:
            result = _run(["xclip", "-selection", "clipboard", "-o"])
            if result.returncode == 0:
                previous = result.stdout
        clipboard_changed = False
        try:
            result = _run(["xclip", "-selection", "clipboard", "-in"], input_text=text)
            if result.returncode != 0:
                message = (result.stderr or "").strip() or "Could not update the clipboard"
                raise InjectionError(message)
            clipboard_changed = True
            if paste:
                if not shutil.which("xdotool"):
                    raise InjectionError(
                        "xdotool is required for pasting into the active X11 window"
                    )
                target = target_window_id if target_window_id is not None else active_window_id()
                if not target:
                    raise InjectionError("Could not identify the target window")
                current = active_window_id()
                if current != target:
                    LOG.warning(
                        "Focus changed during transcription; pasting into the new active "
                        "window instead"
                    )
                    target = current
                    if not target:
                        raise InjectionError("Could not identify the target window")
                result = _run(
                    [
                        "xdotool",
                        "key",
                        "--clearmodifiers",
                        active_window_paste_shortcut(target),
                    ]
                )
                if result.returncode != 0:
                    message = result.stderr.strip() or "Could not paste into the active window"
                    raise InjectionError(message)
        finally:
            if self.restore_clipboard and clipboard_changed and paste:
                time.sleep(self.restore_delay_ms / 1000)
                _run(["xclip", "-selection", "clipboard", "-in"], input_text=previous)
