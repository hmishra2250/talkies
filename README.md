# Talkies

Talkies is a private, local-first dictation and meeting transcription tool for Linux. Audio is
captured with PipeWire/PulseAudio through `ffmpeg`, transcribed on this laptop with
[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper), and pasted into the currently
focused application without sending speech to a cloud service.

The first working use case is a two-press global dictation shortcut:

1. Press **Ctrl+Super+Space** and speak.
2. Press **Ctrl+Super+Space** again.
3. Talkies transcribes locally and pastes into the window that has focus.

It also records voice notes and meetings, including the default speaker/meeting audio.

## Why this shape

- **Local-first:** recordings, models, history, and notes stay under your home directory.
- **Fast after warm-up:** a background daemon keeps the Whisper model loaded.
- **Works across X11 apps:** clipboard transfer plus `xdotool` avoids app-specific plugins and
  selects the terminal paste shortcut for common terminal windows.
- **Recoverable:** every transcript is stored in a local SQLite history before temporary audio is
  removed.
- **Extensible:** dictation, notes, and meetings share one audio/transcription pipeline.

This is an initial personal build, not a claim of complete feature parity with Wispr Flow.
Wispr-style contextual rewriting and true partial streaming are explicit next steps. The local
Whisper model determines most raw recognition quality.

## Install on this laptop

Prerequisites already present on the target Ubuntu installation: `uv`, `ffmpeg`, `pactl`, `xclip`,
and `xdotool`.

```bash
./scripts/install.sh
```

This creates an isolated `uv` tool environment, a systemd **user** service, the default config,
and a GNOME global shortcut. It does not use `sudo`.

The shortcut intentionally avoids **Super+Shift+Space**, which GNOME reserves for switching to
the previous keyboard input source. Shortcut auto-repeat is debounced, and Talkies reuses one
transient desktop notification instead of stacking one notification per event.

`small.en` is already cached on the target laptop. Talkies runs inference in explicit offline mode,
so the daemon does not perform hidden update checks. To opt into downloading a different model,
run `talkies download-model MODEL` once and then change the config.

## Use

```bash
talkies toggle          # start/stop dictation; paste into the active window
talkies note            # start/stop a voice note; save Markdown + audio
talkies meeting         # start/stop mic + default speaker capture
talkies stop            # stop whichever mode is active
talkies status
talkies history
talkies doctor
talkies download-model distil-large-v3  # the only model command that needs the network
```

Meeting transcripts are written to `~/.local/share/talkies/meetings/`; voice notes go to
`~/.local/share/talkies/notes/`. History is stored at
`~/.local/state/talkies/history.sqlite3`.

## Tune recognition

Edit `~/.config/talkies/config.toml`, then restart the service:

```bash
systemctl --user restart talkies
```

Recommended models for the RTX 4070 Laptop GPU:

| Model | Use |
| --- | --- |
| `small.en` | Default: quickest English dictation and already cached |
| `distil-large-v3` | Better English accuracy with good GPU speed |
| `large-v3-turbo` | Strong multilingual quality |

Set names and product vocabulary in `[replacements]`. Say “new line” or “new paragraph” to
control layout. Set `behavior.paste = false` for clipboard-only operation.

## Privacy and files

- Dictation audio is deleted after its transcript is safely written to history by default.
- Note and meeting audio is retained by default.
- No telemetry or cloud API exists in the application.
- Model download is the only expected network use.

## Limitations

- The current global injector targets this laptop's **GNOME X11** session. A future Wayland mode
  should use `ydotool`/uinput because GNOME does not expose the protocol used by `wtype`.
- Meeting mode mixes the default microphone with the current default sink monitor. Switching the
  audio output device after recording starts will not switch the capture source.
- A two-press toggle is used rather than press-and-hold because GNOME custom shortcuts do not
  expose key-up events.
- The daemon processes one transcription at a time.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

See [`docs/architecture.md`](docs/architecture.md) for component boundaries and the roadmap.
