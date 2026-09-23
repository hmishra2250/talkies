# Measuring recognition on your own voice

The dashboard keeps raw audio for dictation when `behavior.keep_dictation_audio = true` and
records saved corrections. Export those pairs with:

```bash
talkies export-benchmark ~/.local/share/talkies/benchmark/manifest.json
```

The manifest is a JSON array. Each item contains an `audio` path and the corrected `reference`
text. Audio remains in its original location, subject to the configured retention window. Export
and run the benchmark before the audio expires, or keep consented test clips separately.

```bash
talkies benchmark ~/.local/share/talkies/benchmark/manifest.json \
  --model small.en --model distil-large-v3 \
  --output ~/.local/share/talkies/benchmark/report.json
```

Models must already be downloaded unless `--online` is passed. A failed model appears as a
failed row and makes the command exit nonzero. Each successful row includes case transcripts,
word error rate, elapsed inference time, and peak process resident memory. On platforms where
process isolation is available, each model runs in a separate process for comparable peak RSS.
The peak RSS does not include GPU memory, and elapsed inference time is not the same as full
key-release-to-paste latency. Measure the latter on the actual desktop separately. A silent
smoke clip can verify that a model loads, but cannot compare recognition quality.

The current harness supports `faster-whisper` models. Other engines need adapters and the same
normalization and input clips before their results can be compared fairly.
