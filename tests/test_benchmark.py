from __future__ import annotations

import json
from pathlib import Path

import pytest

from talkies import benchmark
from talkies.benchmark import BenchmarkOptions, BenchmarkPair, run_benchmark, word_error_rate


def test_word_error_rate_ignores_case_and_simple_punctuation() -> None:
    assert word_error_rate("Hello, Talkies world", "hello talkies world") == 0.0
    assert word_error_rate("hello brave world", "hello world") == 1 / 3
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "extra words") == 1.0


def test_run_benchmark_reports_model_metrics_as_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"not real wav data")

    def fake_model_run(model: str, pairs: list[BenchmarkPair], options: BenchmarkOptions):
        assert pairs == [BenchmarkPair(audio_path=audio_path, reference_text="hello brave world")]
        assert options.offline is True
        return {
            "status": "ok",
            "model": model,
            "wer": 1 / 3 if model == "fast" else 0.0,
            "word_count": 3,
            "word_errors": 1 if model == "fast" else 0,
            "elapsed_seconds": 0.1,
            "peak_rss_mb": 42.0,
            "peak_rss_scope": "model_process",
            "cases": [{"metadata": {"backend": "fake"}}],
        }

    monkeypatch.setattr(benchmark, "_benchmark_model_isolated", fake_model_run)
    monkeypatch.setattr(benchmark, "_iso_utc_now", lambda: "2026-09-24T00:00:00Z")

    report = run_benchmark(
        [BenchmarkPair(audio_path=audio_path, reference_text="hello brave world")],
        ["fast", "accurate"],
    )

    assert report["started_at"] == "2026-09-24T00:00:00Z"
    assert report["options"]["offline"] is True
    assert report["models"][0]["model"] == "fast"
    assert report["models"][0]["wer"] == 1 / 3
    assert report["models"][0]["peak_rss_mb"] == 42.0
    assert report["models"][1]["model"] == "accurate"
    assert report["models"][1]["wer"] == 0.0
    assert report["models"][1]["cases"][0]["metadata"]["backend"] == "fake"

    encoded = benchmark.benchmark_to_json(report)
    assert json.loads(encoded) == report


def test_load_pairs_resolves_audio_relative_to_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "bench.json"
    manifest.write_text(
        json.dumps([{"audio": "audio/one.wav", "reference": "hello"}]),
        encoding="utf-8",
    )

    pairs = benchmark.load_pairs(manifest)

    assert pairs == [BenchmarkPair(audio_path=tmp_path / "audio/one.wav", reference_text="hello")]


def test_run_benchmark_validates_audio_exists(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wav"

    with pytest.raises(ValueError, match="audio does not exist"):
        run_benchmark([BenchmarkPair(audio_path=missing, reference_text="hello")], ["small.en"])


def test_model_failure_is_reported_without_raising(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"not real wav data")

    class FailingTranscriber:
        def __init__(self, config):
            self.config = config

        def transcribe(self, audio: Path):
            raise RuntimeError(f"{self.config.model} cannot load")

    monkeypatch.setattr(benchmark, "Transcriber", FailingTranscriber)

    result = benchmark._benchmark_model_result(
        "broken",
        [BenchmarkPair(audio_path=audio_path, reference_text="hello")],
        BenchmarkOptions(),
    )

    assert result["status"] == "failed"
    assert result["model"] == "broken"
    assert result["error"] == "RuntimeError: broken cannot load"
    assert result["cases"] == []
