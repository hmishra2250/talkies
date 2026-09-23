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
import webbrowser
from pathlib import Path
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
    request = {**request, "request_time": time.monotonic()}
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
    subparsers.add_parser("cancel", help="save the active recording without pasting")
    undo = subparsers.add_parser("undo", help="paste the last cancelled dictation")
    undo.add_argument("transcript_id", nargs="?", type=int)
    dashboard = subparsers.add_parser("dashboard", help="open the local dashboard")
    dashboard.add_argument("--no-open", action="store_true", help="print the local URL only")
    subparsers.add_parser("status", help="show daemon and recording state")
    history = subparsers.add_parser("history", help="show recent local transcripts")
    history.add_argument("--limit", type=int, default=10)
    install_parser = subparsers.add_parser(
        "install", help="install the user service and GNOME hotkey"
    )
    install_parser.add_argument("--hotkey", default="<Control><Super>space")
    download = subparsers.add_parser(
        "download-model", help="explicitly download a speech model for offline use"
    )
    download.add_argument("model", nargs="?", default=None)
    benchmark = subparsers.add_parser("benchmark", help="compare local models on corrected audio")
    benchmark.add_argument("manifest", type=Path)
    benchmark.add_argument("--model", action="append", dest="models")
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--device")
    benchmark.add_argument("--compute-type")
    benchmark.add_argument("--language")
    benchmark.add_argument("--beam-size", type=int)
    benchmark.add_argument("--online", action="store_true", help="allow model download")
    export = subparsers.add_parser(
        "export-benchmark", help="write a manifest of corrected transcripts with retained audio"
    )
    export.add_argument("output", type=Path)
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
        try:
            path = download_model(model, local_files_only=True)
            print(f"Already available offline: {model}")
        except Exception:
            print(f"Downloading {model} for offline use…")
            path = download_model(model)
        print(f"Ready: {path}")
        return 0
    if args.command == "dashboard":
        response = send_request({"command": "status"})
        if not response.get("ok"):
            return print_response(response)
        url = response.get("dashboard_url") or "http://127.0.0.1:8765/"
        print(url)
        if not args.no_open:
            webbrowser.open(str(url))
        return 0
    if args.command == "benchmark":
        from .benchmark import (
            BenchmarkOptions,
            benchmark_to_json,
            load_pairs,
            run_benchmark,
            write_report,
        )

        config = load_config().transcription
        options = BenchmarkOptions(
            device=args.device or config.device,
            compute_type=args.compute_type or config.compute_type,
            language=args.language or config.language,
            beam_size=args.beam_size if args.beam_size is not None else config.beam_size,
            vad_filter=config.vad_filter,
            initial_prompt=config.initial_prompt,
            offline=not args.online,
        )
        try:
            report = run_benchmark(
                load_pairs(args.manifest),
                args.models or [config.model],
                options,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"talkies: benchmark failed: {exc}", file=sys.stderr)
            return 1
        if args.output:
            write_report(report, args.output)
            print(args.output)
        else:
            print(benchmark_to_json(report), end="")
        return 0 if all(item["status"] == "ok" for item in report["models"]) else 1
    if args.command == "export-benchmark":
        pairs = TranscriptStore().corrected_pairs()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(pairs, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(pairs)} corrected pairs to {args.output}")
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
    elif args.command == "undo":
        request = {"command": "undo", "transcript_id": args.transcript_id}
    else:
        request = {"command": args.command}
    return print_response(send_request(request), quiet=False)


if __name__ == "__main__":
    raise SystemExit(main())
