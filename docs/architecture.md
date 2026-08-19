# Architecture

## Runtime path

```text
GNOME hotkey → talkies CLI → Unix socket → resident daemon
                                          ├─ ffmpeg/Pulse capture
                                          ├─ faster-whisper/CTranslate2
                                          ├─ SQLite history + Markdown notes
                                          └─ xclip + xdotool injection
```

The CLI stays cheap enough for a global shortcut. The daemon owns recording state and begins model
warm-up when recording starts, overlapping startup with the user's speech. After a configurable
idle grace period, it replaces its own process to fully release model, CUDA, and ONNX/VAD memory;
ordinary Python garbage collection cannot return all of those native allocations. The Unix socket
is mode `0600` under `$XDG_RUNTIME_DIR`. Model resolution uses `local_files_only`, so normal daemon
startup never contacts the model host.

## Modes

- **dictation:** microphone → transcription → history → active window; audio is temporary.
- **note:** microphone → transcription → Markdown + history; audio is retained.
- **meeting:** microphone + default sink monitor → mixed WAV → Markdown + history.

## Failure boundaries

- Audio startup checks that `ffmpeg` remains alive before reporting recording state.
- The transcript is committed before disposable dictation audio is deleted.
- GPU model loading falls back to CPU, preserving function at lower speed.
- Clipboard restoration is delayed until the target application has consumed the paste.
- A single operation lock prevents overlapping stop/transcribe requests.

## Roadmap

1. Add VAD-driven partial streaming so long dictation appears incrementally.
2. Add an optional local text-rewrite model for contextual formatting, filler removal, and tone.
3. Add Wayland injection through a carefully permissioned `ydotoold` service.
4. Add speaker diarization and timestamped meeting sections.
5. Add a tray indicator and microphone/input chooser.
6. Add searchable history and retry/re-inject actions.
