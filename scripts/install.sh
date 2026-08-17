#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

for command in uv ffmpeg pactl xclip xdotool xprop systemctl gsettings notify-send; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$command" >&2
    exit 1
  fi
done

uv tool install --force --reinstall-package talkies-local .
"${HOME}/.local/bin/talkies" install --hotkey "${TALKIES_HOTKEY:-<Control><Super>space}"
"${HOME}/.local/bin/talkies" doctor

printf '\nTalkies is ready. Press Ctrl+Super+Space to start, speak, then press it again.\n'
