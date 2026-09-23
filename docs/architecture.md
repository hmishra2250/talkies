# Architecture

## Runtime Path

```text
Ctrl+Super+Space -> talkies CLI -> Unix socket -> resident daemon
Configured hold key -> daemon X11 xinput listener -> Talkies service

Talkies service
  -> ffmpeg/PulseAudio capture
  -> faster-whisper/CTranslate2
  -> SQLite history + retained audio + Markdown notes
  -> xclip + xdotool injection
  -> localhost dashboard + overlay events
```

The CLI remains cheap enough for the GNOME fallback shortcut. The daemon owns recording state,
starts the X11 hold-key listener, starts the localhost dashboard, serves a `0600` Unix socket under
the Talkies runtime directory, and warms the speech model when recording starts.

Model loading uses faster-whisper in offline mode after download. After
`transcription.model_idle_timeout_seconds` seconds of idle time, the daemon replaces its own process
to release model, CUDA, and VAD memory that ordinary Python cleanup may not return to the OS.

## Current Linux Features

- **Hold dictation:** `XInputHotkeyListener` watches X11 key press/release events through
  `xinput test-xi2 --root`. The default `behavior.hold_key` is `Control_R`, but the dashboard can
  capture a real physical key or key combination (including Copilot variants), apply it live, and
  persist it without changing unrelated configuration.
- **Double-tap lock:** a quick double tap of the hold key starts dictation without continuing to
  hold the key. Pressing the hold key again stops and pastes.
- **Fallback toggle:** the installer registers the GNOME custom shortcut `Ctrl+Super+Space`, which
  runs `talkies toggle`.
- **Cancel and undo:** `talkies cancel` saves the active recording as `cancelled` without pasting.
  `talkies undo` pastes the most recent cancelled transcript, or a specific cancelled transcript id.
- **Dashboard:** the daemon starts `http://127.0.0.1:8765/` with stats, history search, status
  filters, retained audio playback, correction editing, clipboard delivery, hotkey capture and
  settings, and an editable dictionary.
- **History:** `TranscriptStore` stores transcript text, raw text, status, duration, app name,
  retained audio path, note path, and edit timestamp in SQLite.
- **Benchmarks:** dashboard corrections set `edited_at`; `talkies export-benchmark` writes corrected
  retained-audio pairs, and `talkies benchmark` compares local models with word error rate, elapsed
  time, and process peak RSS.

## Modes

| Mode | Input | Output | Audio retention |
| --- | --- | --- | --- |
| `dictation` | Default microphone | History, optional paste into active window | Kept for 14 days by default |
| `note` | Default microphone | Markdown note + history | Kept by default |
| `meeting` | Default microphone + default sink monitor | Markdown meeting transcript + history | Kept by default |

## Failure Boundaries

- Audio startup checks that `ffmpeg` remains alive before reporting recording state.
- Failed transcriptions are saved with status `failed`, and the raw audio path is kept.
- Cancelled dictations are transcribed and saved before returning control to the user.
- Clipboard restoration is delayed until the target application has consumed the paste.
- A single operation lock prevents overlapping stop/transcribe requests.
- The dashboard binds only to localhost and rejects non-local hosts; write requests require
  same-origin localhost access.

## Verified Limits

- The implementation is verified on Linux/X11. Wayland, macOS, iOS, and Android are not supported
  yet.
- The hold-key listener depends on `xinput` and `xmodmap`; it does not run in a pure Wayland session.
- GNOME custom shortcuts are toggle-only because they do not expose key-release events.
- Transcription still runs after stop/cancel. The code does not perform true partial streaming while
  the user speaks.
- The meeting recorder uses the default input/output devices selected when recording starts.
- The daemon handles one recording stop/transcribe operation at a time.

## Roadmap

1. Stabilize the Linux/X11 experience: hold-key tuning, overlay polish, dashboard editing, and
   benchmark reports based on the user's corrected audio.
2. Add a real streaming pipeline: voice activity segmentation, partial transcript assembly, and
   bounded latency after releasing the hold key.
3. Improve recognition with measured evidence: corrected-audio benchmarks first, then vocabulary
   biasing, then model changes or fine-tuning only when the benchmark supports the change.
4. Add Wayland support with the correct permission model for capture and injection.
5. Add macOS support with native hotkey, capture, paste, packaging, and model-cache adapters.
6. Explore mobile support as a separate native shell around the same privacy model, benchmark
   format, and correction loop.
