from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from spectracer import __version__
from spectracer.core.config import AnalyzeCliConfig, load_runtime_analyze_config
from spectracer.core.models import AnalysisParams, ChannelMode
from spectracer.project.project_service import ProjectService

DEFAULT_ANALYZE_OUTPUT = Path(".spectracer_cache")

T = TypeVar("T")


def _pick(override: T | None, fallback: T) -> T:
    return fallback if override is None else override


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spectracer",
        description="Spectracer CLI - CQT 频谱分析与缓存工具",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="分析音频并输出缓存与预览图")
    analyze_parser.add_argument("input", help="输入音频文件路径")
    analyze_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ANALYZE_OUTPUT,
        help="分析缓存输出目录（默认: ./.spectracer_cache）",
    )
    analyze_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="分析配置 TOML 路径（不传时自动尝试 config/analysis.default.toml）",
    )
    analyze_parser.add_argument(
        "--channel-mode",
        default=None,
        choices=ChannelMode.choices(),
        help="声道模式（覆盖配置）",
    )
    analyze_parser.add_argument("--fps", type=int, default=None, help="每秒块数（1~100，覆盖配置）")
    analyze_parser.add_argument(
        "--bins-per-semitone",
        type=float,
        default=None,
        help="每半音分块数（最小 0.1，覆盖配置）",
    )
    analyze_parser.add_argument("--octave-min", type=int, default=None, help="最低八度（覆盖配置）")
    analyze_parser.add_argument("--octave-max", type=int, default=None, help="最高八度（覆盖配置）")
    analyze_parser.add_argument("--a4", type=float, default=None, help="标准频率 A4（Hz，覆盖配置）")
    analyze_parser.add_argument(
        "--sample-rate",
        type=int,
        default=None,
        help="重采样率（0 = 保持原采样率，覆盖配置）",
    )
    analyze_parser.add_argument(
        "--processing-fingerprint",
        default=None,
        help="后处理参数指纹（覆盖配置）",
    )
    analyze_parser.add_argument("--sensitivity", type=float, default=None, help="热图灵敏度（覆盖配置）")
    analyze_parser.add_argument("--contrast", type=float, default=None, help="热图对比度（覆盖配置）")
    analyze_parser.add_argument(
        "--no-preview",
        action="store_true",
        help="不输出 preview.png（覆盖配置）",
    )
    analyze_parser.set_defaults(handler=run_analyze)

    gui_parser = subparsers.add_parser("gui", help="启动 PyQt6 图形界面")
    gui_parser.add_argument("input", nargs="?", default=None, help="可选：启动后立即打开的音频文件")
    gui_parser.set_defaults(handler=run_gui)

    init_parser = subparsers.add_parser("init-project", help="初始化 .srproj 项目目录")
    init_parser.add_argument("path", help="项目目录（可省略 .srproj 后缀）")
    init_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 project.json")
    init_parser.set_defaults(handler=run_init_project)

    return parser


def _build_params(args: argparse.Namespace, config: AnalyzeCliConfig) -> AnalysisParams:
    sample_rate_raw = _pick(args.sample_rate, 0 if config.sample_rate is None else config.sample_rate)
    sample_rate = None if int(sample_rate_raw) == 0 else int(sample_rate_raw)

    params = AnalysisParams(
        fps=int(_pick(args.fps, config.fps)),
        bins_per_semitone=float(_pick(args.bins_per_semitone, config.bins_per_semitone)),
        octave_min=int(_pick(args.octave_min, config.octave_min)),
        octave_max=int(_pick(args.octave_max, config.octave_max)),
        a4_hz=float(_pick(args.a4, config.a4_hz)),
        sample_rate=sample_rate,
        channel_mode=ChannelMode.parse(_pick(args.channel_mode, config.channel_mode.value)),
    )
    params.validate()
    return params


def run_analyze(args: argparse.Namespace) -> int:
    # 重型依赖延迟导入，保证 `--help` / `init-project` 无需 DSP 依赖即可运行。
    from spectracer.app.analysis_workflow import AnalyzeExecutionOptions, execute_analysis

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"[Spectracer] 输入文件不存在: {input_path}", file=sys.stderr)
        return 2

    try:
        config, used_config_path = load_runtime_analyze_config(args.config)
        params = _build_params(args, config)

        processing_fingerprint = _pick(args.processing_fingerprint, config.processing_fingerprint)
        sensitivity = float(_pick(args.sensitivity, config.sensitivity))
        contrast = float(_pick(args.contrast, config.contrast))
        save_preview = (not args.no_preview) and config.preview_enabled

        options = AnalyzeExecutionOptions(
            processing_fingerprint=processing_fingerprint,
            sensitivity=sensitivity,
            contrast=contrast,
            save_preview=save_preview,
        )

        result = execute_analysis(
            input_path=input_path,
            output_dir=args.output,
            params=params,
            options=options,
        )

        print("[Spectracer] 分析完成")
        if used_config_path is not None:
            print(f"  配置文件: {used_config_path}")
        else:
            print("  配置文件: <内建默认>")
        print(f"  输入文件: {result.input_path}")
        print(f"  缓存键: {result.cache_key}")
        print(f"  帧数: {result.num_frames} | 频率分箱: {result.num_bins}")
        print(f"  缓存目录: {result.cache_paths.root}")
        if result.preview_path is not None:
            print(f"  预览图: {result.preview_path}")
        print(
            "  耗时(ms): "
            f"load={result.timings_ms['load_audio_ms']:.1f}, "
            f"mix={result.timings_ms['mix_channel_ms']:.1f}, "
            f"cqt={result.timings_ms['compute_cqt_ms']:.1f}, "
            f"cache={result.timings_ms['cache_write_ms']:.1f}, "
            f"preview={result.timings_ms['preview_ms']:.1f}, "
            f"total={result.timings_ms['total_ms']:.1f}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[Spectracer] 分析失败: {exc}", file=sys.stderr)
        return 1


def run_gui(args: argparse.Namespace) -> int:
    try:
        from spectracer.ui.main_window import launch_ui

        return int(launch_ui(args.input))
    except Exception as exc:  # noqa: BLE001
        print(f"[Spectracer] GUI 启动失败: {exc}", file=sys.stderr)
        return 1


def run_init_project(args: argparse.Namespace) -> int:
    try:
        project = ProjectService().create_empty_project(args.path, overwrite=args.overwrite)
        print("[Spectracer] 项目初始化完成")
        print(f"  项目目录: {project.root}")
        print(f"  配置文件: {project.project_file}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[Spectracer] 初始化失败: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
