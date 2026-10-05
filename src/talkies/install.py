from __future__ import annotations

import ast
import shlex
import subprocess
import sys
from pathlib import Path

from .config import ensure_user_dirs

KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/talkies/"


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)


def _service_text() -> str:
    executable = f"{shlex.quote(sys.executable)} -m talkies daemon"
    # Start with the desktop session rather than default.target: by the time
    # graphical-session.target is reached, GNOME has imported DISPLAY and
    # XAUTHORITY into the user manager. Starting earlier left the hotkey
    # listener and overlay unable to authenticate with X after a reboot.
    return f"""[Unit]
Description=Talkies local speech service
PartOf=graphical-session.target
After=graphical-session.target pipewire.service

[Service]
Type=simple
ExecStart={executable}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
"""


def install_service() -> Path:
    ensure_user_dirs()
    service_path = Path.home() / ".config/systemd/user/talkies.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(_service_text(), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    # reenable drops links left by older units that were wanted by default.target.
    _run(["systemctl", "--user", "reenable", "talkies.service"])
    _run(["systemctl", "--user", "restart", "talkies.service"])
    return service_path


def install_hotkey(binding: str, command: str) -> None:
    schema = "org.gnome.settings-daemon.plugins.media-keys"
    result = _run(["gsettings", "get", schema, "custom-keybindings"])
    raw = result.stdout.strip()
    paths: list[str]
    if raw.startswith("@as"):
        paths = []
    else:
        value = ast.literal_eval(raw)
        paths = [str(item) for item in value]
    if KEYBINDING_PATH not in paths:
        paths.append(KEYBINDING_PATH)
    _run(["gsettings", "set", schema, "custom-keybindings", repr(paths)])
    custom_schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
    target = f"{custom_schema}:{KEYBINDING_PATH}"
    _run(["gsettings", "set", target, "name", "Talkies dictation"])
    _run(["gsettings", "set", target, "command", command])
    _run(["gsettings", "set", target, "binding", binding])


def install(binding: str) -> tuple[Path, str]:
    service_path = install_service()
    command_path = Path(sys.executable).parent / "talkies"
    if not command_path.exists():
        command = f"{shlex.quote(sys.executable)} -m talkies toggle"
    else:
        command = f"{shlex.quote(str(command_path))} toggle"
    install_hotkey(binding, command)
    return service_path, command
