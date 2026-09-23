# Talkies

Talkies is a private, local-first dictation and meeting transcription tool for Linux/X11. It
captures audio with `ffmpeg`, transcribes locally with
[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper), stores history in SQLite, and pastes
dictation into the focused X11 application without sending speech to a cloud service.

The main dictation flow is now press-and-hold:

1. Hold **Right Ctrl** and speak.
2. Release **Right Ctrl** to transcribe and paste.
3. Double-tap **Right Ctrl** to lock recording hands-free, then tap **Right Ctrl** again to stop.

The installed GNOME shortcut, **Ctrl+Super+Space**, remains as a fallback toggle: press once to
start, speak, then press again to stop and paste.

## Install

On Ubuntu or Debian, clone the repository and run the installer:

```bash
git clone https://github.com/hmishra2250/talkies.git && cd talkies && ./install.sh
```

For an existing checkout, run:

```bash
./install.sh
```

The installer:

1. Installs missing Ubuntu/Debian audio and X11 packages with `apt` when necessary.
2. Installs `uv` when it is missing.
3. Installs Talkies as an isolated `uv tool`.
4. Downloads the default `small.en` speech model once.
5. Installs and starts the `talkies.service` systemd user service.
6. Registers the GNOME fallback shortcut, then runs `talkies doctor`.

System packages may require your `sudo` password. The Talkies daemon runs as your normal user.

## Use

```bash
talkies toggle          # fallback start/stop dictation toggle
talkies start           # start dictation
talkies stop            # stop, transcribe, and paste/save
talkies cancel          # stop without pasting; keep the transcript in history
talkies undo            # paste the most recent cancelled dictation
talkies note            # start/stop a voice note; save Markdown + audio
talkies meeting         # start/stop mic + default speaker capture
talkies dashboard       # open the localhost dashboard
talkies status
talkies history
talkies doctor
```

The dashboard is served by the daemon on `http://127.0.0.1:8765/`. It shows words today and this
week, average words per minute, transcript counts, searchable history, retained audio playback,
status filters, correction editing, clipboard delivery, and an editable replacement dictionary.
Use `talkies dashboard --no-open` to print the URL without opening a browser.

Voice notes are written to `~/.local/share/talkies/notes/`. Meeting transcripts are written to
`~/.local/share/talkies/meetings/`. History is stored at
`~/.local/state/talkies/history.sqlite3`.

## Cancel, undo, and corrections

`talkies cancel` stops the active recording, transcribes it, marks the item as `cancelled`, and
does not paste it. `talkies undo` restores the last cancelled dictation by pasting it into the
current target.

Corrections are made in the dashboard: select a history item, edit the text, and save the
correction. Corrected transcripts with retained audio can become benchmark pairs:

The dashboard's **Copy for paste** action puts saved text on the clipboard; paste it into the
desired application. **Mark cancelled** and **Restore** change an item's history status. The
`talkies undo` command directly pastes the most recent cancelled dictation into the focused app.

```bash
talkies export-benchmark ~/.local/share/talkies/benchmark/manifest.json
talkies benchmark ~/.local/share/talkies/benchmark/manifest.json --model small.en --output ~/.local/share/talkies/benchmark/report.json
```

`talkies benchmark` uses already-downloaded models by default. Add `--online` only when you want it
to download a missing model.

## Configure

Edit `~/.config/talkies/config.toml`, then restart the service:

```bash
systemctl --user restart talkies.service
```

Useful defaults:

| Setting | Default | Meaning |
| --- | --- | --- |
| `behavior.hold_key` | `"Control_R"` | X11 key name for press-and-hold dictation |
| `behavior.hold_hotkey` | `true` | Enables the `xinput` hold listener when `DISPLAY` is set |
| `behavior.keep_dictation_audio` | `true` | Keeps dictation audio for correction, replay, and benchmarks |
| `behavior.dictation_audio_retention_days` | `14` | Prunes retained dictation audio after 14 days |
| `transcription.model` | `"small.en"` | Local faster-whisper model |
| `transcription.beam_size` | `1` | Default dictation decoding beam size |

Set names and product vocabulary in `[replacements]`. Say "new line" or "new paragraph" to control
layout. Set `behavior.paste = false` for clipboard-only operation.

To try another model, download it once and then change `transcription.model`:

```bash
talkies download-model distil-large-v3
```

## Privacy and files

- Dictation, note, and meeting audio stay under `~/.local/share/talkies/`.
- Dictation audio is retained for 14 days by default, then pruned from history rows.
- Cancelled dictations are saved in history so they can be undone or corrected.
- The dashboard only binds to localhost and rejects non-local hosts.
- Normal inference runs in offline mode after the model is downloaded.
- No telemetry or cloud API exists in the application.

## Limitations

- Talkies is currently verified for Linux/X11. Wayland, macOS, and mobile are roadmap targets, not
  supported platforms.
- The hold key listener uses `xinput test-xi2 --root` and `xmodmap`, so it needs an X11 session.
- GNOME custom shortcuts do not expose key-up events; use **Ctrl+Super+Space** as a toggle
  fallback rather than a hold shortcut.
- Dictation is transcribed after recording stops. True partial streaming is not implemented.
- Meeting mode captures the default microphone and default sink monitor selected at start time.
- The daemon processes one stop/transcribe operation at a time.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

See [`docs/architecture.md`](docs/architecture.md) for component boundaries and the long-term
roadmap.
