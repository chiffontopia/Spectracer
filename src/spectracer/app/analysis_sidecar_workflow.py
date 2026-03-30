from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

from spectracer.core.analysis_results import ChordAnalysisResult, TempoAnalysisResult
from spectracer.core.models import ChannelMode
from spectracer.project.cache_store import CacheStore


class AnalysisSidecarKind(str, Enum):
    TEMPO = "tempo"
    CHORD = "chord"


@dataclass(slots=True)
class SidecarAnalysisExecutionOptions:
    force_recompute: bool = False


@dataclass(slots=True)
class SidecarAnalysisProgress:
    kind: AnalysisSidecarKind
    cache_key: str
    stage: str
    message: str


@dataclass(slots=True)
class SidecarAnalysisExecutionResult:
    kind: AnalysisSidecarKind
    cache_key: str
    channel_mode: ChannelMode
    from_cache: bool
    payload: TempoAnalysisResult | ChordAnalysisResult


ProgressCallback = Callable[[SidecarAnalysisProgress], None]
TempoAnalyzer = Callable[[Path, ChannelMode], TempoAnalysisResult]
ChordAnalyzer = Callable[[Path, ChannelMode], ChordAnalysisResult]
_SidecarT = TypeVar("_SidecarT", TempoAnalysisResult, ChordAnalysisResult)


def execute_tempo_sidecar_analysis(
    *,
    source_audio_path: str | Path,
    cache_store: CacheStore,
    cache_key: str,
    channel_mode: ChannelMode,
    analyzer: TempoAnalyzer,
    options: SidecarAnalysisExecutionOptions | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SidecarAnalysisExecutionResult:
    payload = _execute_sidecar_analysis(
        kind=AnalysisSidecarKind.TEMPO,
        source_audio_path=source_audio_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=channel_mode,
        options=options,
        progress_callback=progress_callback,
        analyzer=analyzer,
        load_cached=lambda: cache_store.load_tempo_analysis(cache_key=cache_key),
        save_cached=lambda result: cache_store.save_tempo_analysis(cache_key=cache_key, result=result),
        payload_type=TempoAnalysisResult,
    )
    return SidecarAnalysisExecutionResult(
        kind=AnalysisSidecarKind.TEMPO,
        cache_key=cache_key,
        channel_mode=channel_mode,
        from_cache=payload[0],
        payload=payload[1],
    )


def execute_chord_sidecar_analysis(
    *,
    source_audio_path: str | Path,
    cache_store: CacheStore,
    cache_key: str,
    channel_mode: ChannelMode,
    analyzer: ChordAnalyzer,
    options: SidecarAnalysisExecutionOptions | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SidecarAnalysisExecutionResult:
    payload = _execute_sidecar_analysis(
        kind=AnalysisSidecarKind.CHORD,
        source_audio_path=source_audio_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=channel_mode,
        options=options,
        progress_callback=progress_callback,
        analyzer=analyzer,
        load_cached=lambda: cache_store.load_chord_analysis(cache_key=cache_key),
        save_cached=lambda result: cache_store.save_chord_analysis(cache_key=cache_key, result=result),
        payload_type=ChordAnalysisResult,
    )
    return SidecarAnalysisExecutionResult(
        kind=AnalysisSidecarKind.CHORD,
        cache_key=cache_key,
        channel_mode=channel_mode,
        from_cache=payload[0],
        payload=payload[1],
    )


def _execute_sidecar_analysis(
    *,
    kind: AnalysisSidecarKind,
    source_audio_path: str | Path,
    cache_store: CacheStore,
    cache_key: str,
    channel_mode: ChannelMode,
    options: SidecarAnalysisExecutionOptions | None,
    progress_callback: ProgressCallback | None,
    analyzer: Callable[[Path, ChannelMode], _SidecarT],
    load_cached: Callable[[], _SidecarT | None],
    save_cached: Callable[[_SidecarT], Path],
    payload_type: type[_SidecarT],
) -> tuple[bool, _SidecarT]:
    resolved_source = Path(source_audio_path).expanduser().resolve()
    opts = options if options is not None else SidecarAnalysisExecutionOptions()

    if not opts.force_recompute:
        _report_progress(progress_callback, kind, cache_key, "cache_lookup", f"检查 {kind.value} 分析缓存")
        cached = load_cached()
        if cached is not None:
            _report_progress(progress_callback, kind, cache_key, "cache_hit", f"命中 {kind.value} 分析缓存")
            return True, cached

    _report_progress(progress_callback, kind, cache_key, "compute", f"开始计算 {kind.value} 分析结果")
    computed = analyzer(resolved_source, channel_mode)
    if not isinstance(computed, payload_type):
        raise TypeError(f"{kind.value} analyzer 返回值类型错误: {type(computed)!r}")

    if computed.channel_mode is None:
        computed = computed.with_channel_mode(channel_mode)

    save_cached(computed)
    _report_progress(progress_callback, kind, cache_key, "persist", f"已写入 {kind.value} 分析缓存")
    return False, computed


def _report_progress(
    progress_callback: ProgressCallback | None,
    kind: AnalysisSidecarKind,
    cache_key: str,
    stage: str,
    message: str,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        SidecarAnalysisProgress(
            kind=kind,
            cache_key=cache_key,
            stage=stage,
            message=message,
        )
    )


__all__ = [
    "AnalysisSidecarKind",
    "SidecarAnalysisExecutionOptions",
    "SidecarAnalysisProgress",
    "SidecarAnalysisExecutionResult",
    "execute_tempo_sidecar_analysis",
    "execute_chord_sidecar_analysis",
]
