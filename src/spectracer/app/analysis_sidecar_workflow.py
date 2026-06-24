from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from spectracer.core.analysis_results import TempoAnalysisResult
from spectracer.core.models import ChannelMode
from spectracer.project.cache_store import CacheStore


class AnalysisSidecarKind(str, Enum):
    TEMPO = "tempo"


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
    payload: TempoAnalysisResult


ProgressCallback = Callable[[SidecarAnalysisProgress], None]
TempoAnalyzer = Callable[[Path, ChannelMode], TempoAnalysisResult]


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
    from_cache, payload = _execute_tempo_sidecar_analysis(
        source_audio_path=source_audio_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=channel_mode,
        analyzer=analyzer,
        options=options,
        progress_callback=progress_callback,
    )
    return SidecarAnalysisExecutionResult(
        kind=AnalysisSidecarKind.TEMPO,
        cache_key=cache_key,
        channel_mode=channel_mode,
        from_cache=from_cache,
        payload=payload,
    )


def _execute_tempo_sidecar_analysis(
    *,
    source_audio_path: str | Path,
    cache_store: CacheStore,
    cache_key: str,
    channel_mode: ChannelMode,
    analyzer: TempoAnalyzer,
    options: SidecarAnalysisExecutionOptions | None,
    progress_callback: ProgressCallback | None,
) -> tuple[bool, TempoAnalysisResult]:
    resolved_source = Path(source_audio_path).expanduser().resolve()
    opts = options if options is not None else SidecarAnalysisExecutionOptions()

    if not opts.force_recompute:
        _report_progress(progress_callback, cache_key, "cache_lookup", "检查 tempo 分析缓存")
        cached = cache_store.load_tempo_analysis(cache_key=cache_key)
        if cached is not None:
            _report_progress(progress_callback, cache_key, "cache_hit", "命中 tempo 分析缓存")
            return True, cached

    _report_progress(progress_callback, cache_key, "compute", "开始计算 tempo 分析结果")
    computed = analyzer(resolved_source, channel_mode)
    if not isinstance(computed, TempoAnalysisResult):
        raise TypeError(f"tempo analyzer 返回值类型错误: {type(computed)!r}")

    if computed.channel_mode is None:
        computed = computed.with_channel_mode(channel_mode)

    cache_store.save_tempo_analysis(cache_key=cache_key, result=computed)
    _report_progress(progress_callback, cache_key, "persist", "已写入 tempo 分析缓存")
    return False, computed


def _report_progress(
    progress_callback: ProgressCallback | None,
    cache_key: str,
    stage: str,
    message: str,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        SidecarAnalysisProgress(
            kind=AnalysisSidecarKind.TEMPO,
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
]
