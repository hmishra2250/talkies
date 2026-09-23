# Benchmark CLI Proposal

Add a `talkies benchmark` subcommand after the core harness lands.

Suggested shape:

```bash
talkies benchmark manifest.json \
  --model small.en \
  --model distil-large-v3 \
  --output benchmark-results.json
```

The manifest is a JSON array of paired audio and reference text:

```json
[
  {"audio": "samples/dictation-001.wav", "reference": "the corrected transcript"}
]
```

Implementation sketch for `cli.py`:

1. Import `BenchmarkOptions`, `load_pairs`, `run_benchmark`, `benchmark_to_json`, and `write_report`.
2. Add a `benchmark` parser with `manifest`, repeatable `--model`, optional `--output`, and transcription flags mirroring the config fields: `--device`, `--compute-type`, `--language`, `--beam-size`, `--vad-filter/--no-vad-filter`, `--initial-prompt`, and `--online`.
3. Default to the current configured transcription model when `--model` is omitted.
4. Keep offline mode on by default. `--online` should be the explicit escape hatch for downloading a missing model.
5. Print JSON to stdout when `--output` is absent; write the same JSON to the requested path when it is present.

The CLI should call the same `Transcriber` path as the daemon through `talkies.benchmark.run_benchmark`, so benchmark results match the real dictation pipeline.
