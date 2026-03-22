from __future__ import annotations

import argparse
import csv
import json
import sys
import tracemalloc
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from spectracer.core.config import AnalyzeCliConfig, load_runtime_analyze_config
from spectracer.core.models import AnalysisParams, ChannelMode

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"}


@dataclass(slots=True)
class BenchmarkRecord:
    audio_file: str
    cache_key: str
    sample_rate: int
    num_frames: int
    num_bins: int
    load_audio_ms: float
    mix_channel_ms: float
    compute_cqt_ms: float
    cache_write_ms: float
    preview_ms: float
    total_ms: float
    peak_memory_mb: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spectracer-benchmark",
        description="批量分析测试音频并生成性能报告",
    )
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["tests/*.wav"],
        help="音频文件匹配模式（glob），可传多个",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".benchmarks"),
        help="性能报告输出目录",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".spectracer_benchmark_cache"),
        help="分析缓存目录",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="分析配置 TOML 路径（默认自动读取 config/analysis.default.toml）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理文件数，0 为不限制")
    parser.add_argument("--preview", action="store_true", help="同时生成预览图（会增加耗时）")

    # 参数覆盖（可选）
    parser.add_argument("--channel-mode", choices=ChannelMode.choices(), default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--bins-per-semitone", type=float, default=None)
    parser.add_argument("--octave-min", type=int, default=None)
    parser.add_argument("--octave-max", type=int, default=None)
    parser.add_argument("--a4", type=float, default=None)
    parser.add_argument("--sample-rate", type=int, default=None)
    parser.add_argument("--processing-fingerprint", default=None)
    parser.add_argument("--sensitivity", type=float, default=None)
    parser.add_argument("--contrast", type=float, default=None)

    return parser


def _pick(override, fallback):
    return fallback if override is None else override


def _build_params(args: argparse.Namespace, config: AnalyzeCliConfig) -> AnalysisParams:
    sample_rate_raw = _pick(args.sample_rate, 0 if config.sample_rate is None else config.sample_rate)
    sample_rate = None if int(sample_rate_raw) == 0 else int(sample_rate_raw)

    params = AnalysisParams(
        channel_mode=ChannelMode.parse(_pick(args.channel_mode, config.channel_mode.value)),
        fps=int(_pick(args.fps, config.fps)),
        bins_per_semitone=float(_pick(args.bins_per_semitone, config.bins_per_semitone)),
        octave_min=int(_pick(args.octave_min, config.octave_min)),
        octave_max=int(_pick(args.octave_max, config.octave_max)),
        a4_hz=float(_pick(args.a4, config.a4_hz)),
        sample_rate=sample_rate,
    )
    params.validate()
    return params


def _collect_audio_files(patterns: list[str], limit: int) -> list[Path]:
    root = Path.cwd()
    files: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        for candidate in sorted(root.glob(pattern)):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)

    if limit > 0:
        return files[:limit]
    return files


def _record_to_dict(record: BenchmarkRecord) -> dict[str, float | int | str]:
    return {
        "audio_file": record.audio_file,
        "cache_key": record.cache_key,
        "sample_rate": record.sample_rate,
        "num_frames": record.num_frames,
        "num_bins": record.num_bins,
        "load_audio_ms": round(record.load_audio_ms, 3),
        "mix_channel_ms": round(record.mix_channel_ms, 3),
        "compute_cqt_ms": round(record.compute_cqt_ms, 3),
        "cache_write_ms": round(record.cache_write_ms, 3),
        "preview_ms": round(record.preview_ms, 3),
        "total_ms": round(record.total_ms, 3),
        "peak_memory_mb": round(record.peak_memory_mb, 3),
    }


def _write_reports(records: list[BenchmarkRecord], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"benchmark_{timestamp}.json"
    csv_path = output_dir / f"benchmark_{timestamp}.csv"

    json_path.write_text(
        json.dumps([_record_to_dict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    field_names = list(_record_to_dict(records[0]).keys()) if records else []
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=field_names)
        writer.writeheader()
        for record in records:
            writer.writerow(_record_to_dict(record))

    return json_path, csv_path


def run(args: argparse.Namespace) -> int:
    from spectracer.app.analysis_workflow import AnalyzeExecutionOptions, execute_analysis

    files = _collect_audio_files(args.patterns, args.limit)
    if not files:
        print("[Benchmark] 未匹配到任何音频文件", file=sys.stderr)
        return 2

    config, used_config_path = load_runtime_analyze_config(args.config)
    params = _build_params(args, config)

    options = AnalyzeExecutionOptions(
        processing_fingerprint=_pick(args.processing_fingerprint, config.processing_fingerprint),
        sensitivity=float(_pick(args.sensitivity, config.sensitivity)),
        contrast=float(_pick(args.contrast, config.contrast)),
        save_preview=bool(args.preview),
    )

    print(f"[Benchmark] 文件数量: {len(files)}")
    if used_config_path is not None:
        print(f"[Benchmark] 配置文件: {used_config_path}")
    else:
        print("[Benchmark] 配置文件: <内建默认>")

    records: list[BenchmarkRecord] = []

    for index, file_path in enumerate(files, start=1):
        print(f"[Benchmark] ({index}/{len(files)}) {file_path.name}")

        tracemalloc.start()
        try:
            result = execute_analysis(
                input_path=file_path,
                output_dir=args.cache_dir,
                params=params,
                options=options,
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        record = BenchmarkRecord(
            audio_file=str(file_path),
            cache_key=result.cache_key,
            sample_rate=result.sample_rate,
            num_frames=result.num_frames,
            num_bins=result.num_bins,
            load_audio_ms=result.timings_ms["load_audio_ms"],
            mix_channel_ms=result.timings_ms["mix_channel_ms"],
            compute_cqt_ms=result.timings_ms["compute_cqt_ms"],
            cache_write_ms=result.timings_ms["cache_write_ms"],
            preview_ms=result.timings_ms["preview_ms"],
            total_ms=result.timings_ms["total_ms"],
            peak_memory_mb=peak / (1024 * 1024),
        )
        records.append(record)

    json_report, csv_report = _write_reports(records, args.output_dir)

    avg_total = sum(record.total_ms for record in records) / len(records)
    avg_peak = sum(record.peak_memory_mb for record in records) / len(records)
    print(f"[Benchmark] 平均总耗时: {avg_total:.2f} ms")
    print(f"[Benchmark] 平均峰值内存: {avg_peak:.2f} MB")
    print(f"[Benchmark] JSON 报告: {json_report}")
    print(f"[Benchmark] CSV 报告: {csv_report}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[Benchmark] 执行失败: {exc}", file=sys.stderr)
        return 1
