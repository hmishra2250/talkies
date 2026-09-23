# Talkies product roadmap

This is the durable agenda for a free, local voice tool that starts with a reliable Linux
dictation experience and can later reach macOS and mobile. The Linux release remains usable
without a network connection after a model is downloaded. Accuracy, speed, memory and UX claims
must be measured on real devices before becoming defaults.

## Linux release: acceptance checks

- The configured key holds to record and releases to paste. A quick double press locks recording;
  a later press finishes it. The dashboard captures and changes the key, including a Copilot key
  when X11 exposes it as a key or chord. The existing Ctrl+Super+Space toggle remains available
  as a fallback. Escape is never captured. Unsupported desktops use the toggle shortcut.
- Cancel prevents pasting but transcribes and records the item. Undo restores a cancelled item.
  Empty and failed recordings remain visible when there is recoverable audio.
- Local history can be searched and corrected. It shows status, duration and the audio when saved.
  Dictation audio defaults to a 14-day retention window; note and meeting retention is separate.
- The dashboard shows words today/week, average dictation pace, and a clear recording state.
  It binds only to localhost. No account, telemetry or subscription is involved.
- A benchmark command compares models on corrected audio/reference pairs and records word error
  rate, time and memory. No new engine is selected based on a public leaderboard alone.

## Next experiments

1. **Latency:** capture a representative set of short and long dictations, then measure time
   from key release to paste. Prototype pause-based partial transcription with overlap and
   deterministic stitching. Keep full-clip fallback for speech without pauses and failures.
2. **Accuracy:** collect corrected pairs only with explicit audio retention. Compare current
   `small.en` at beam sizes 1 and 5, current Whisper alternatives, and a portable ONNX engine.
   Assess names, punctuation and latency on the same recordings. Add vocabulary hints only when
   corrections show a recurring error. Fine-tuning requires enough consented corrected data and
   a measured advantage over the simpler fixes.
3. **Memory:** measure cold and warm resident memory, peak inference memory and idle recycle.
   Choose model/backend per device budget. Avoid forcing one model onto laptops and phones.
4. **Desktop portability:** isolate hotkey, capture and text injection behind platform adapters.
   Build and test macOS permissions/hotkeys/clipboard separately. Wayland needs a supported
   compositor integration; do not assume X11 hooks work there.
5. **Mobile:** design an on-device keyboard/share-sheet flow with a mobile-sized model and
   explicit local storage controls. Prototype Android and iOS independently; a web dashboard
   alone cannot provide system-wide mobile dictation.

Candidate runtimes to measure are the current
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) with a compatible
[Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) export,
[Moonshine](https://github.com/moonshine-ai/moonshine), and
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). Their model formats, licenses, device
support and true resident memory must be checked at integration time. The current benchmark
command evaluates faster-whisper models only; other engines need adapters before comparison.

## Release criteria

Run tests and lint, verify actual hold/cancel/undo in the target desktop session, check
microphone permissions and focus behavior, test audio retention expiry, and record measured
latency/word error/memory on the supported devices. Publish platform support only after those
platforms have been exercised. The MIT license remains in place.
