from __future__ import annotations

import json
import multiprocessing as mp
import queue
import re
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import TranscriptionConfig
from .transcriber import Transcriber

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None

TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


@dataclass(frozen=True, slots=True)
class BenchmarkPair:
    audio_path: Path
    reference_text: str


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "en"
    beam_size: int = 1
    vad_filter: bool = True
    initial_prompt: str = ""
    offline: bool = True


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = _tokens(reference)
    hypothesis_words = _tokens(hypothesis)
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return _edit_distance(reference_words, hypothesis_words) / len(reference_words)


def run_benchmark(
    pairs: Iterable[BenchmarkPair],
    model_names: Iterable[str],
    options: BenchmarkOptions | None = None,
) -> dict[str, Any]:
    benchmark_pairs = list(pairs)
    models = list(model_names)
    if not benchmark_pairs:
        raise ValueError("At least one benchmark pair is required")
    if not models:
        raise ValueError("At least one model name is required")

    options = options or BenchmarkOptions()
    _validate_pairs(benchmark_pairs)
    started_at = _iso_utc_now()
    results = [_benchmark_model_isolated(model, benchmark_pairs, options) for model in models]
    return {
        "schema_version": 1,
        "started_at": started_at,
        "options": {
            "device": options.device,
            "compute_type": options.compute_type,
            "language": options.language,
            "beam_size": options.beam_size,
            "vad_filter": options.vad_filter,
            "initial_prompt": options.initial_prompt,
            "offline": options.offline,
        },
        "models": results,
    }


def benchmark_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def load_pairs(path: Path) -> list[BenchmarkPair]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Benchmark manifest must be a JSON array")
    pairs: list[BenchmarkPair] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Benchmark manifest item {index} must be an object")
        try:
            audio = item["audio"]
            reference = item["reference"]
        except KeyError as exc:
            raise ValueError(f"Benchmark manifest item {index} is missing {exc.args[0]!r}") from exc
        pairs.append(BenchmarkPair((path.parent / str(audio)).expanduser(), str(reference)))
    return pairs


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(benchmark_to_json(report), encoding="utf-8")


def _benchmark_model(
    model_name: str,
    pairs: list[BenchmarkPair],
    options: BenchmarkOptions,
) -> dict[str, Any]:
    transcriber = Transcriber(
        TranscriptionConfig(
            model=model_name,
            device=options.device,
            compute_type=options.compute_type,
            language=options.language,
            beam_size=options.beam_size,
            vad_filter=options.vad_filter,
            initial_prompt=options.initial_prompt,
            offline=options.offline,
        )
    )
    case_results = []
    total_words = 0
    total_errors = 0
    total_elapsed = 0.0
    peak_rss_mb: float | None = None
    for pair in pairs:
        started = time.perf_counter()
        text, metadata = transcriber.transcribe(pair.audio_path)
        elapsed = time.perf_counter() - started
        reference_words = _tokens(pair.reference_text)
        errors = _edit_distance(reference_words, _tokens(text))
        words = len(reference_words)
        total_words += words
        total_errors += errors
        total_elapsed += elapsed
        peak_rss_mb = _peak_rss_mb()
        case_results.append(
            {
                "audio": str(pair.audio_path),
                "reference": pair.reference_text,
                "hypothesis": text,
                "word_count": words,
                "word_errors": errors,
                "wer": _ratio(errors, words),
                "elapsed_seconds": elapsed,
                "peak_rss_mb": peak_rss_mb,
                "metadata": metadata,
            }
        )
    return {
        "status": "ok",
        "model": model_name,
        "wer": _ratio(total_errors, total_words),
        "word_count": total_words,
        "word_errors": total_errors,
        "elapsed_seconds": total_elapsed,
        "peak_rss_mb": peak_rss_mb,
        "peak_rss_scope": "model_process",
        "cases": case_results,
    }


def _benchmark_model_isolated(
    model_name: str,
    pairs: list[BenchmarkPair],
    options: BenchmarkOptions,
) -> dict[str, Any]:
    context = _process_context()
    if context is None:
        result = _benchmark_model_result(model_name, pairs, options)
        if result["status"] == "ok":
            result["peak_rss_scope"] = "process_lifetime_not_comparable"
        return result

    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_benchmark_model_worker,
        args=(model_name, pairs, options, result_queue),
    )
    process.start()
    deadline = time.monotonic() + 900
    result = None
    while time.monotonic() < deadline:
        try:
            result = result_queue.get(timeout=0.5)
            break
        except queue.Empty:
            if not process.is_alive():
                break
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    result_queue.close()
    if result is None:
        result = {
            "status": "failed",
            "model": model_name,
            "error": f"Benchmark worker exited or timed out with status {process.exitcode}",
            "cases": [],
        }
    if result.get("status") == "ok":
        result["peak_rss_scope"] = "model_process"
    return result


def _benchmark_model_worker(
    model_name: str,
    pairs: list[BenchmarkPair],
    options: BenchmarkOptions,
    result_queue: mp.Queue,
) -> None:
    result_queue.put(_benchmark_model_result(model_name, pairs, options))


def _benchmark_model_result(
    model_name: str,
    pairs: list[BenchmarkPair],
    options: BenchmarkOptions,
) -> dict[str, Any]:
    try:
        return _benchmark_model(model_name, pairs, options)
    except Exception as exc:
        return {
            "status": "failed",
            "model": model_name,
            "error": f"{type(exc).__name__}: {exc}",
            "cases": [],
        }


def _process_context() -> mp.context.BaseContext | None:
    methods = mp.get_all_start_methods()
    if "fork" in methods:
        return mp.get_context("fork")
    return None


def _validate_pairs(pairs: list[BenchmarkPair]) -> None:
    for index, pair in enumerate(pairs):
        if not pair.audio_path.exists():
            raise ValueError(f"Benchmark pair {index} audio does not exist: {pair.audio_path}")
        if not pair.audio_path.is_file():
            raise ValueError(f"Benchmark pair {index} audio is not a file: {pair.audio_path}")


def _tokens(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.casefold())


def _edit_distance(left: list[str], right: list[str]) -> int:
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for row_index, left_word in enumerate(left, start=1):
        current = [row_index]
        for column_index, right_word in enumerate(right, start=1):
            substitution = previous[column_index - 1] + (left_word != right_word)
            insertion = current[column_index - 1] + 1
            deletion = previous[column_index] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def _ratio(errors: int, words: int) -> float:
    return 0.0 if words == 0 and errors == 0 else errors / max(1, words)


def _peak_rss_mb() -> float | None:
    if resource is None:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 if sys.platform != "darwin" else 1024 * 1024
    return rss / divisor


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
