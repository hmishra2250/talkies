from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

from . import __version__
from .config import ensure_user_dirs, load_config, runtime_dir
from .daemon import Daemon, socket_path
from .install import install
from .store import TranscriptStore


def _start_daemon() -> None:
    result = subprocess.run(
        ["systemctl", "--user", "start", "talkies.service"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        ensure_user_dirs()
        log_path = runtime_dir() / "daemon.log"
        with log_path.open("ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "talkies", "daemon"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )


def send_request(request: dict[str, Any], *, autostart: bool = True) -> dict[str, Any]:
    path = socket_path()
    if not path.exists() and autostart:
        _start_daemon()
        for _ in range(50):
            if path.exists():
                break
            time.sleep(0.1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(900)
    try:
        client.connect(str(path))
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        payload = b""
        while not payload.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            payload += chunk
        return json.loads(payload.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Could not reach Talkies daemon: {exc}"}
    finally:
        client.close()


def print_response(response: dict[str, Any], *, quiet: bool = False) -> int:
    if quiet:
        return 0 if response.get("ok") else 1
    if response.get("text"):
        print(response["text"])
    elif response.get("state"):
        state = response["state"]
        mode = response.get("mode")
        print(f"{state}{f' ({mode})' if mode else ''}")
    if response.get("note_path"):
        print(f"Saved: {response['note_path']}", file=sys.stderr)
    if not response.get("ok"):
        print(f"talkies: {response.get('error', 'unknown error')}", file=sys.stderr)
        return 1
    return 0


def doctor() -> int:
    config = load_config()
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "pactl": bool(shutil.which("pactl")),
        "xclip": bool(shutil.which("xclip")),
        "xdotool": bool(shutil.which("xdotool")),
        "xprop": bool(shutil.which("xprop")),
        "notify-send": bool(shutil.which("notify-send")),
        "X11 session": os.environ.get("XDG_SESSION_TYPE") == "x11"
        or bool(os.environ.get("DISPLAY")),
    }
    try:
        import faster_whisper  # noqa: F401

        checks["faster-whisper"] = True
    except ImportError:
        checks["faster-whisper"] = False
    for name, ok in checks.items():
        print(f"{'✓' if ok else '✗'} {name}")
    print(f"Model: {config.transcription.model}")
    print(f"Offline inference: {'on' if config.transcription.offline else 'off'}")
    response = send_request({"command": "status"}, autostart=False)
    print(f"Daemon: {response.get('state', response.get('error'))}")
    return 0 if all(checks.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talkies",
        description="Private, local-first dictation and meeting transcription",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("toggle", help="toggle dictation and paste into the active window")
    subparsers.add_parser("note", help="toggle a recorded voice note")
    subparsers.add_parser("meeting", help="toggle a microphone + speaker meeting recording")
    start = subparsers.add_parser("start", help="begin recording")
    start.add_argument("--mode", choices=["dictation", "note", "meeting"], default="dictation")
    subparsers.add_parser("stop", help="stop, transcribe, and deliver the active recording")
    subparsers.add_parser("status", help="show daemon and recording state")
    history = subparsers.add_parser("history", help="show recent local transcripts")
    history.add_argument("--limit", type=int, default=10)
    install_parser = subparsers.add_parser(
        "install", help="install the user service and GNOME hotkey"
    )
    install_parser.add_argument("--hotkey", default="<Super><Shift>space")
    download = subparsers.add_parser(
        "download-model", help="explicitly download a speech model for offline use"
    )
    download.add_argument("model", nargs="?", default=None)
    subparsers.add_parser("doctor", help="check local dependencies")
    subparsers.add_parser("daemon", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "daemon":
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        Daemon().serve()
        return 0
    if args.command == "install":
        service_path, command = install(args.hotkey)
        print(f"Installed service: {service_path}")
        print(f"Global dictation hotkey: {args.hotkey}")
        print(f"Hotkey command: {command}")
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "download-model":
        from faster_whisper.utils import download_model

        model = args.model or load_config().transcription.model
        print(f"Downloading {model} for offline use…")
        path = download_model(model)
        print(f"Ready: {path}")
        return 0
    if args.command == "history":
        for item in TranscriptStore().recent(max(1, min(args.limit, 100))):
            preview = str(item["text"]).replace("\n", " ")[:100]
            print(f"{item['id']:>4}  {item['created_at']}  {item['mode']:<9}  {preview}")
        return 0
    request: dict[str, Any]
    if args.command in {None, "toggle"}:
        request = {"command": "toggle", "mode": "dictation"}
    elif args.command in {"note", "meeting"}:
        request = {"command": "toggle", "mode": args.command}
    elif args.command == "start":
        request = {"command": "start", "mode": args.mode}
    else:
        request = {"command": args.command}
    return print_response(send_request(request), quiet=False)


if __name__ == "__main__":
    raise SystemExit(main())
