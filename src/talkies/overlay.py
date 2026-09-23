from __future__ import annotations

import atexit
import logging
import multiprocessing
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any

LOG = logging.getLogger(__name__)

ActionCallback = Callable[[str], None]
OverlayState = dict[str, Any] | str

_STOP = "__talkies_overlay_stop__"
_DEFAULT_READY_MS = 1400
_current_overlay: OverlayHandle | None = None
_current_lock = threading.Lock()


def start_overlay(callback: ActionCallback) -> OverlayHandle:
    """Start the optional desktop overlay and return its process handle.

    The UI runs in a child process so Tkinter owns its own main thread. The daemon can safely call
    publish() from any thread, and user actions are delivered to callback on a small reader thread.
    """

    global _current_overlay
    with _current_lock:
        if _current_overlay and _current_overlay.running:
            return _current_overlay
        handle = _create_overlay(callback)
        _current_overlay = handle
        atexit.register(handle.stop)
        return handle


def publish(state: OverlayState) -> None:
    """Publish state to the active overlay, if it is running."""

    with _current_lock:
        handle = _current_overlay
    if handle:
        handle.publish(state)


def stop_overlay() -> None:
    """Stop the active overlay, if one exists."""

    global _current_overlay
    with _current_lock:
        handle = _current_overlay
        _current_overlay = None
    if handle:
        handle.stop()


@dataclass(slots=True)
class OverlayHandle:
    state_queue: Any
    action_queue: Any
    process: BaseProcess | None
    reader_thread: threading.Thread | None
    callback: ActionCallback
    _closed: bool = False

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.is_alive() and not self._closed)

    def publish(self, state: OverlayState) -> None:
        if self._closed:
            return
        try:
            self.state_queue.put(_normalise_state(state), block=False)
        except Exception:
            LOG.debug("Could not publish overlay state", exc_info=True)

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        for target in (self.state_queue, self.action_queue):
            with suppress(Exception):
                target.put(_STOP, block=False)
        if self.process:
            self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.5)


def _create_overlay(callback: ActionCallback) -> OverlayHandle:
    try:
        context = multiprocessing.get_context("spawn")
        state_queue = context.Queue()
        action_queue = context.Queue()
        process = context.Process(
            target=_run_overlay,
            args=(state_queue, action_queue),
            daemon=True,
        )
        process.start()
    except Exception:
        LOG.info("Talkies overlay is unavailable", exc_info=True)
        return OverlayHandle(
            state_queue=_NullQueue(),
            action_queue=_NullQueue(),
            process=None,
            reader_thread=None,
            callback=callback,
        )

    handle = OverlayHandle(
        state_queue=state_queue,
        action_queue=action_queue,
        process=process,
        reader_thread=None,
        callback=callback,
    )
    reader = threading.Thread(target=_read_actions, args=(handle,), daemon=True)
    handle.reader_thread = reader
    reader.start()
    return handle


def _read_actions(handle: OverlayHandle) -> None:
    while not handle._closed:
        try:
            action = handle.action_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        if action == _STOP:
            return
        try:
            handle.callback(str(action))
        except Exception:
            LOG.exception("Overlay action callback failed")


def _normalise_state(state: OverlayState) -> dict[str, Any]:
    if isinstance(state, str):
        state = {"status": state}
    status = str(state.get("status", "ready")).casefold()
    message = state.get("message")
    detail = state.get("detail")
    normalised = {
        "status": status,
        "message": str(message) if message is not None else _default_message(status),
        "detail": str(detail) if detail is not None else "",
        "show_cancel": bool(state.get("show_cancel", status == "recording")),
        "auto_hide_ms": int(
            state.get("auto_hide_ms", _DEFAULT_READY_MS if status in {"ready", "cancelled"} else 0)
        ),
    }
    return normalised


def _default_message(status: str) -> str:
    return {
        "recording": "Listening",
        "transcribing": "Transcribing",
        "ready": "Ready",
        "cancelled": "Cancelled",
        "hidden": "",
    }.get(status, status.capitalize())


def _run_overlay(state_queue: Any, action_queue: Any) -> None:
    try:
        import tkinter as tk
        from tkinter import font
    except Exception:
        return

    try:
        root = tk.Tk()
    except Exception:
        return
    root.withdraw()
    root.title("Talkies")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    window = tk.Toplevel(root)
    window.withdraw()
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.configure(bg="#171717")

    base_font = font.nametofont("TkDefaultFont")
    title_font = base_font.copy()
    title_font.configure(size=12, weight="bold")
    detail_font = base_font.copy()
    detail_font.configure(size=9)

    frame = tk.Frame(window, bg="#171717", padx=14, pady=10)
    frame.grid(row=0, column=0)
    indicator = tk.Canvas(frame, width=12, height=12, bg="#171717", highlightthickness=0)
    dot = indicator.create_oval(2, 2, 10, 10, fill="#3ddc84", outline="")
    indicator.grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="n")
    message_var = tk.StringVar(value="")
    detail_var = tk.StringVar(value="")
    tk.Label(frame, textvariable=message_var, font=title_font, bg="#171717", fg="#f6f6f6").grid(
        row=0, column=1, sticky="w"
    )
    tk.Label(frame, textvariable=detail_var, font=detail_font, bg="#171717", fg="#b7b7b7").grid(
        row=1, column=1, sticky="w"
    )
    cancel_button = tk.Button(
        frame,
        text="Cancel",
        command=lambda: action_queue.put("cancel"),
        bg="#2a2a2a",
        fg="#f6f6f6",
        activebackground="#3a3a3a",
        activeforeground="#ffffff",
        relief="flat",
        padx=8,
        pady=3,
    )
    cancel_button.grid(row=0, column=2, rowspan=2, padx=(12, 0))

    hide_after_id: str | None = None

    def show(state: dict[str, Any]) -> None:
        nonlocal hide_after_id
        if hide_after_id is not None:
            root.after_cancel(hide_after_id)
            hide_after_id = None
        if state["status"] == "hidden":
            window.withdraw()
            return
        message_var.set(state["message"])
        detail_var.set(state["detail"])
        indicator.itemconfigure(dot, fill=_status_colour(state["status"]))
        if state["show_cancel"]:
            cancel_button.grid()
        else:
            cancel_button.grid_remove()
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = int((screen_width - width) / 2)
        y = max(24, screen_height - height - 96)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        auto_hide_ms = state["auto_hide_ms"]
        if auto_hide_ms > 0:
            hide_after_id = root.after(auto_hide_ms, window.withdraw)

    def poll() -> None:
        while True:
            try:
                item = state_queue.get_nowait()
            except queue.Empty:
                break
            if item == _STOP:
                root.destroy()
                return
            show(_normalise_state(item))
        root.after(80, poll)

    root.after(80, poll)
    root.mainloop()


def _status_colour(status: str) -> str:
    return {
        "recording": "#ff5c5c",
        "transcribing": "#f6c945",
        "ready": "#3ddc84",
        "cancelled": "#b7b7b7",
    }.get(status, "#3ddc84")


class _NullQueue:
    def put(self, _item: object, *, block: bool = True) -> None:
        return None
