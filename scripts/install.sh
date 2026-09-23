#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APT_PACKAGES=(
  ca-certificates
  curl
  ffmpeg
  libglib2.0-bin
  libnotify-bin
  pulseaudio-utils
  x11-utils
  x11-xserver-utils
  xclip
  xdotool
  xinput
)

missing_system_command=false
for command in curl ffmpeg pactl xclip xdotool xprop xinput xmodmap notify-send; do
  if ! command -v "$command" >/dev/null 2>&1; then
    missing_system_command=true
    break
  fi
done

if $missing_system_command; then
  if ! command -v apt-get >/dev/null 2>&1; then
    printf 'Talkies currently supports automatic dependency installation on Debian/Ubuntu.\n' >&2
    printf 'Install these commands, then rerun this script: ffmpeg pactl xclip xdotool xprop notify-send\n' >&2
    exit 1
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    printf 'sudo is required to install missing system packages.\n' >&2
    exit 1
  fi
  printf 'Installing required Ubuntu/Debian packages…\n'
  sudo apt-get update
  sudo apt-get install -y "${APT_PACKAGES[@]}"
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'Installing uv…\n'
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

for command in uv ffmpeg pactl xclip xdotool xprop xinput xmodmap systemctl gsettings notify-send; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$command" >&2
    exit 1
  fi
done

printf 'Installing Talkies in an isolated environment…\n'
uv tool install --force --reinstall-package talkies-local .
TALKIES_BIN="$(uv tool dir)/talkies-local/bin/talkies"
if [[ ! -x "$TALKIES_BIN" ]]; then
  TALKIES_BIN="${HOME}/.local/bin/talkies"
fi

printf 'Preparing the local speech model (one-time download when missing)…\n'
"$TALKIES_BIN" download-model small.en

"$TALKIES_BIN" install --hotkey "${TALKIES_HOTKEY:-<Control><Super>space}"
"$TALKIES_BIN" doctor

printf '\nTalkies is ready. Press Ctrl+Super+Space to start, speak, then press it again.\n'
