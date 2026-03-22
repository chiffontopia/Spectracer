from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf

from spectracer.audio.channel_modes import apply_channel_mode
from spectracer.audio.io import load_audio
from spectracer.core.models import AnalysisParams, ChannelMode, CqtResult
from spectracer.dsp.cqt_engine import compute_cqt, compute_cqt_complex
from spectracer.dsp.visualization import save_cqt_heatmap
from spectracer.project.cache_store import CachePaths, CacheStore, file_fingerprint


@dataclass(slots=True)
class AnalyzeExecutionOptions:
    processing_fingerprint: str = "raw"
    sensitivity: float = 1.0
    contrast: float = 1.0
    save_preview: bool = True
    save_playback_audio: bool = False


@dataclass(slots=True)
class AnalysisProgress:
    completed_steps: int
    total_steps: int
    message: str
    channel_mode: ChannelMode | None = None


@dataclass(slots=True)
class AnalyzeExecutionResult:
    input_path: Path
    effective_params: AnalysisParams
    cache_key: str
    cache_paths: CachePaths
    cqt_result: CqtResult
    num_frames: int
    num_bins: int
    sample_rate: int
    preview_path: Path | None
    playback_audio_path: Path | None
    timings_ms: dict[str, float]


@dataclass(slots=True)
class MultiChannelAnalysisResult:
    input_path: Path
    results_by_mode: dict[ChannelMode, AnalyzeExecutionResult]
    load_audio_ms: float
    total_ms: float


ProgressCallback = Callable[[AnalysisProgress], None]
ModeResultCallback = Callable[[ChannelMode, AnalyzeExecutionResult], None]


def execute_analysis(
    input_path: str | Path,
    output_dir: str | Path,
    params: AnalysisParams,
    options: AnalyzeExecutionOptions | None = None,
    progress_callback: ProgressCallback | None = None,
) -> AnalyzeExecutionResult:
    """执行单个声道模式的完整分析流水线。"""

    resolved_input = Path(input_path).expanduser().resolve()
    if not resolved_input.exists():
        raise FileNotFoundError(f"输入文件不存在: {resolved_input}")

    opts = options or AnalyzeExecutionOptions()
    total_steps = _progress_total_steps(mode_count=1, options=opts)
    _report_progress(progress_callback, 0, total_steps, "准备加载音频")

    load_start = perf_counter()
    audio, loaded_sample_rate = load_audio(resolved_input, target_sample_rate=params.sample_rate)
    load_audio_ms = (perf_counter() - load_start) * 1000.0

    _report_progress(progress_callback, 1, total_steps, "音频加载完成", params.channel_mode)
    result, _ = _execute_analysis_from_loaded_audio(
        source_audio_path=resolved_input,
        audio=audio,
        loaded_sample_rate=loaded_sample_rate,
        output_dir=output_dir,
        params=params,
        options=opts,
        load_audio_ms=load_audio_ms,
        source_audio_fingerprint=None,
        completed_steps=1,
        total_steps=total_steps,
        progress_callback=progress_callback,
    )
    return result


def execute_multi_channel_analysis(
    input_path: str | Path,
    output_dir: str | Path,
    params: AnalysisParams,
    channel_modes: Iterable[ChannelMode | str],
    options: AnalyzeExecutionOptions | None = None,
    progress_callback: ProgressCallback | None = None,
    mode_result_callback: ModeResultCallback | None = None,
) -> MultiChannelAnalysisResult:
    """一次加载音频，批量完成多个声道模式的分析。"""

    resolved_input = Path(input_path).expanduser().resolve()
    if not resolved_input.exists():
        raise FileNotFoundError(f"输入文件不存在: {resolved_input}")

    modes = _normalize_channel_modes(channel_modes)
    if not modes:
        raise ValueError("channel_modes 不能为空")

    opts = options or AnalyzeExecutionOptions()
    total_steps = _progress_total_steps(mode_count=len(modes), options=opts)
    total_start = perf_counter()

    _report_progress(progress_callback, 0, total_steps, "准备加载音频")
    load_start = perf_counter()
    audio, loaded_sample_rate = load_audio(resolved_input, target_sample_rate=params.sample_rate)
    load_audio_ms = (perf_counter() - load_start) * 1000.0
    source_audio_fingerprint = file_fingerprint(resolved_input)
    _report_progress(progress_callback, 1, total_steps, "音频加载完成")

    completed_steps = 1

    # 性能优化：当输入为 1~2 声道且同时需要多个模式时，CQT 可以在频域复用。
    # 计算一次 Left/Right 的 complex CQT，即可通过线性组合得到 mono/side，并复用 left/right/stereo。
    precomputed_cqt_results: dict[ChannelMode, CqtResult] = {}
    shared_cqt_ms = 0.0
    if audio.ndim == 2 and 1 <= audio.shape[0] <= 2 and len(modes) >= 2:
        channel_count = int(audio.shape[0])

        naive_cqt_count = 0
        for mode in modes:
            if mode == ChannelMode.STEREO and channel_count > 1:
                naive_cqt_count += 2
            else:
                naive_cqt_count += 1

        optimized_cqt_count = 2 if channel_count > 1 else 1
        if optimized_cqt_count < naive_cqt_count:
            _report_progress(progress_callback, completed_steps, total_steps, "计算共享 CQT（L/R）")
            shared_start = perf_counter()
            precomputed_cqt_results = _precompute_cqt_results_from_lr(
                audio=audio,
                sample_rate=loaded_sample_rate,
                params=params,
                modes=modes,
            )
            shared_cqt_ms = (perf_counter() - shared_start) * 1000.0
            _report_progress(progress_callback, completed_steps, total_steps, "共享 CQT 计算完成")
    results_by_mode: dict[ChannelMode, AnalyzeExecutionResult] = {}
    for mode in modes:
        mode_params = replace(params, channel_mode=mode)
        precomputed_cqt_result = precomputed_cqt_results.get(mode)
        precomputed_cqt_ms = shared_cqt_ms if mode == modes[0] and precomputed_cqt_result is not None else 0.0
        analysis_result, completed_steps = _execute_analysis_from_loaded_audio(
            source_audio_path=resolved_input,
            audio=audio,
            loaded_sample_rate=loaded_sample_rate,
            output_dir=output_dir,
            params=mode_params,
            options=opts,
            load_audio_ms=load_audio_ms,
            source_audio_fingerprint=source_audio_fingerprint,
            completed_steps=completed_steps,
            total_steps=total_steps,
            progress_callback=progress_callback,
            precomputed_cqt_result=precomputed_cqt_result,
            precomputed_cqt_ms=precomputed_cqt_ms,
        )
        results_by_mode[mode] = analysis_result
        if mode_result_callback is not None:
            mode_result_callback(mode, analysis_result)

    total_ms = (perf_counter() - total_start) * 1000.0
    return MultiChannelAnalysisResult(
        input_path=resolved_input,
        results_by_mode=results_by_mode,
        load_audio_ms=load_audio_ms,
        total_ms=total_ms,
    )


def _execute_analysis_from_loaded_audio(
    *,
    source_audio_path: Path,
    audio: np.ndarray,
    loaded_sample_rate: int,
    output_dir: str | Path,
    params: AnalysisParams,
    options: AnalyzeExecutionOptions,
    load_audio_ms: float,
    source_audio_fingerprint: str | None,
    completed_steps: int,
    total_steps: int,
    progress_callback: ProgressCallback | None,
    precomputed_cqt_result: CqtResult | None = None,
    precomputed_cqt_ms: float | None = None,
) -> tuple[AnalyzeExecutionResult, int]:
    params.validate()
    timings: dict[str, float] = {"load_audio_ms": float(load_audio_ms)}
    total_start = perf_counter()

    effective_params = replace(params, sample_rate=loaded_sample_rate)
    mode = effective_params.channel_mode

    stage_start = perf_counter()
    analysis_signal = _build_analysis_signal(audio, mode)
    timings["mix_channel_ms"] = (perf_counter() - stage_start) * 1000.0
    completed_steps += 1
    _report_progress(progress_callback, completed_steps, total_steps, f"{mode.display_name} 声道准备完成", mode)

    if precomputed_cqt_result is None:
        stage_start = perf_counter()
        cqt_result = _compute_cqt_for_mode(analysis_signal, loaded_sample_rate, effective_params)
        timings["compute_cqt_ms"] = (perf_counter() - stage_start) * 1000.0
    else:
        cqt_result = precomputed_cqt_result
        timings["compute_cqt_ms"] = float(0.0 if precomputed_cqt_ms is None else precomputed_cqt_ms)
    completed_steps += 1
    _report_progress(progress_callback, completed_steps, total_steps, f"{mode.display_name} 频谱计算完成", mode)

    cache_store = CacheStore(output_dir)
    stage_start = perf_counter()
    cache_key = cache_store.build_cache_key(
        audio_path=source_audio_path,
        params=effective_params,
        processing_fingerprint=options.processing_fingerprint,
        audio_fingerprint=source_audio_fingerprint,
    )
    cache_paths = cache_store.save_analysis(
        cache_key=cache_key,
        source_audio_path=source_audio_path,
        params=effective_params,
        result=cqt_result,
        processing_fingerprint=options.processing_fingerprint,
    )
    timings["cache_write_ms"] = (perf_counter() - stage_start) * 1000.0
    completed_steps += 1
    _report_progress(progress_callback, completed_steps, total_steps, f"{mode.display_name} 缓存写入完成", mode)

    preview_path: Path | None = None
    playback_audio_path: Path | None = None

    stage_start = perf_counter()
    if options.save_playback_audio:
        playback_audio_path = _save_playback_audio(
            analysis_signal,
            loaded_sample_rate,
            cache_paths.playback_audio,
        )
    timings["playback_audio_ms"] = (perf_counter() - stage_start) * 1000.0
    if options.save_playback_audio:
        completed_steps += 1
        _report_progress(progress_callback, completed_steps, total_steps, f"{mode.display_name} 回放音频生成完成", mode)

    stage_start = perf_counter()
    if options.save_preview:
        preview_path = save_cqt_heatmap(
            cqt_result,
            cache_paths.preview,
            sensitivity=options.sensitivity,
            contrast=options.contrast,
        )
    timings["preview_ms"] = (perf_counter() - stage_start) * 1000.0
    if options.save_preview:
        completed_steps += 1
        _report_progress(progress_callback, completed_steps, total_steps, f"{mode.display_name} 预览图生成完成", mode)

    timings["total_ms"] = load_audio_ms + (perf_counter() - total_start) * 1000.0

    return (
        AnalyzeExecutionResult(
            input_path=source_audio_path,
            effective_params=effective_params,
            cache_key=cache_key,
            cache_paths=cache_paths,
            cqt_result=cqt_result,
            num_frames=cqt_result.num_frames,
            num_bins=cqt_result.num_bins,
            sample_rate=loaded_sample_rate,
            preview_path=preview_path,
            playback_audio_path=playback_audio_path,
            timings_ms=timings,
        ),
        completed_steps,
    )


def _build_analysis_signal(audio: np.ndarray, channel_mode: ChannelMode) -> np.ndarray:
    if audio.ndim != 2:
        raise ValueError("audio 必须是二维数组 (channels, samples)")

    if channel_mode == ChannelMode.STEREO:
        return np.ascontiguousarray(audio[: min(2, audio.shape[0])], dtype=np.float32)

    mixed_signal = apply_channel_mode(audio, channel_mode)
    return np.ascontiguousarray(mixed_signal, dtype=np.float32)


def _compute_cqt_for_mode(signal: np.ndarray, sample_rate: int, params: AnalysisParams) -> CqtResult:
    if params.channel_mode == ChannelMode.STEREO and signal.ndim == 2 and signal.shape[0] > 1:
        left_result = compute_cqt(np.ascontiguousarray(signal[0], dtype=np.float32), sample_rate, params)
        right_result = compute_cqt(np.ascontiguousarray(signal[1], dtype=np.float32), sample_rate, params)
        magnitude = np.maximum(left_result.magnitude, right_result.magnitude).astype(np.float32)
        return CqtResult(
            magnitude=magnitude,
            frame_times=left_result.frame_times,
            bin_frequencies=left_result.bin_frequencies,
            hop_length=left_result.hop_length,
            sample_rate=left_result.sample_rate,
        )

    if signal.ndim == 2:
        signal = np.ascontiguousarray(signal[0], dtype=np.float32)

    return compute_cqt(signal, sample_rate, params)


def _precompute_cqt_results_from_lr(
    *,
    audio: np.ndarray,
    sample_rate: int,
    params: AnalysisParams,
    modes: Iterable[ChannelMode],
) -> dict[ChannelMode, CqtResult]:
    """对 1~2 声道音频复用 CQT：只计算 L/R 的 complex CQT，再派生各模式的 magnitude。"""

    if audio.ndim != 2:
        raise ValueError("audio 必须是二维数组 (channels, samples)")
    if audio.shape[0] < 1:
        raise ValueError("audio 至少需要 1 个声道")
    if audio.shape[0] > 2:
        raise ValueError("仅支持对 1~2 声道音频进行共享 CQT 优化")

    requested = set(modes)

    left = np.ascontiguousarray(audio[0], dtype=np.float32)
    right_source = audio[1] if audio.shape[0] > 1 else audio[0]
    right = np.ascontiguousarray(right_source, dtype=np.float32)

    left_complex, frame_times, bin_frequencies, hop_length = compute_cqt_complex(
        left,
        sample_rate,
        params,
    )
    right_complex, _, _, _ = compute_cqt_complex(
        right,
        sample_rate,
        params,
    )

    left_magnitude = np.abs(left_complex).T.astype(np.float32)
    right_magnitude = np.abs(right_complex).T.astype(np.float32)

    results: dict[ChannelMode, CqtResult] = {}

    def build_result(magnitude: np.ndarray) -> CqtResult:
        return CqtResult(
            magnitude=magnitude,
            frame_times=frame_times,
            bin_frequencies=bin_frequencies,
            hop_length=hop_length,
            sample_rate=int(sample_rate),
        )

    if ChannelMode.LEFT in requested:
        results[ChannelMode.LEFT] = build_result(left_magnitude)
    if ChannelMode.RIGHT in requested:
        results[ChannelMode.RIGHT] = build_result(right_magnitude)
    if ChannelMode.STEREO in requested:
        results[ChannelMode.STEREO] = build_result(np.maximum(left_magnitude, right_magnitude).astype(np.float32))
    if ChannelMode.MONO in requested:
        mono_complex = (left_complex + right_complex) * 0.5
        results[ChannelMode.MONO] = build_result(np.abs(mono_complex).T.astype(np.float32))
    if ChannelMode.SIDE in requested:
        side_complex = left_complex - right_complex
        results[ChannelMode.SIDE] = build_result(np.abs(side_complex).T.astype(np.float32))

    return results


def _save_playback_audio(signal: np.ndarray, sample_rate: int, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if signal.ndim == 2:
        audio_buffer = signal.T
    else:
        audio_buffer = signal

    audio_buffer = np.clip(np.asarray(audio_buffer, dtype=np.float32), -1.0, 1.0)
    sf.write(output, audio_buffer, sample_rate, subtype="PCM_16")
    return output


def _normalize_channel_modes(channel_modes: Iterable[ChannelMode | str]) -> list[ChannelMode]:
    modes: list[ChannelMode] = []
    seen: set[ChannelMode] = set()

    for raw_mode in channel_modes:
        mode = raw_mode if isinstance(raw_mode, ChannelMode) else ChannelMode.parse(str(raw_mode))
        if mode in seen:
            continue
        seen.add(mode)
        modes.append(mode)

    return modes


def _progress_total_steps(mode_count: int, options: AnalyzeExecutionOptions) -> int:
    steps_per_mode = 3 + int(options.save_playback_audio) + int(options.save_preview)
    return 1 + (mode_count * steps_per_mode)


def _report_progress(
    progress_callback: ProgressCallback | None,
    completed_steps: int,
    total_steps: int,
    message: str,
    channel_mode: ChannelMode | None = None,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        AnalysisProgress(
            completed_steps=completed_steps,
            total_steps=total_steps,
            message=message,
            channel_mode=channel_mode,
        )
    )
