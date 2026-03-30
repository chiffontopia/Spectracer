from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from spectracer.app.analysis_sidecar_workflow import (
    AnalysisSidecarKind,
    SidecarAnalysisExecutionOptions,
    execute_chord_sidecar_analysis,
    execute_tempo_sidecar_analysis,
)
from spectracer.core.analysis_results import (
    ChordAnalysisResult,
    ChordSegment,
    TempoAnalysisCandidate,
    TempoAnalysisResult,
)
from spectracer.core.models import AnalysisParams, ChannelMode, CqtResult
from spectracer.project.cache_store import CacheStore


def _make_cqt_result() -> CqtResult:
    return CqtResult(
        magnitude=np.ones((4, 3), dtype=np.float32),
        frame_times=np.array([0.0, 0.1, 0.2, 0.3], dtype=np.float32),
        bin_frequencies=np.array([110.0, 220.0, 440.0], dtype=np.float32),
        hop_length=512,
        sample_rate=22050,
    )


def _create_analysis_cache(tmp_path: Path, sample_wav_path: Path) -> tuple[CacheStore, str]:
    cache_store = CacheStore(tmp_path / "analysis-cache")
    params = AnalysisParams(
        fps=24,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )
    cache_key = cache_store.build_cache_key(
        audio_path=sample_wav_path,
        params=params,
        processing_fingerprint="sidecar-test",
        audio_fingerprint="sidecar-fingerprint",
    )
    cache_store.save_analysis(
        cache_key=cache_key,
        source_audio_path=sample_wav_path,
        params=params,
        result=_make_cqt_result(),
        processing_fingerprint="sidecar-test",
    )
    return cache_store, cache_key


def test_cache_store_persists_analysis_sidecars_and_registers_default_paths(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    cache_store, cache_key = _create_analysis_cache(tmp_path, sample_wav_path)

    tempo_result = TempoAnalysisResult(
        channel_mode=ChannelMode.MONO,
        candidates=(
            TempoAnalysisCandidate(
                bpm=120.0,
                first_beat_seconds=0.0,
                offset_ms=0.0,
                confidence=0.95,
                candidate_rank=1,
            ),
        ),
    )
    chord_result = ChordAnalysisResult(
        channel_mode=ChannelMode.MONO,
        segments=(ChordSegment(start_seconds=0.0, end_seconds=1.0, label="C", confidence=0.88),),
    )

    tempo_path = cache_store.save_tempo_analysis(cache_key=cache_key, result=tempo_result)
    chord_path = cache_store.save_chord_analysis(cache_key=cache_key, result=chord_result)
    loaded_entry = cache_store.load_analysis(cache_key=cache_key)

    assert loaded_entry is not None
    assert loaded_entry.tempo_analysis_path == tempo_path
    assert loaded_entry.chord_analysis_path == chord_path
    assert cache_store.load_tempo_analysis(cache_key=cache_key) == tempo_result
    assert cache_store.load_chord_analysis(cache_key=cache_key) == chord_result

    metadata = json.loads(loaded_entry.paths.metadata.read_text(encoding="utf-8"))
    assert metadata["files"]["tempo_analysis"] == "tempo_analysis.json"
    assert metadata["files"]["chord_analysis"] == "chord_analysis.json"


def test_execute_tempo_sidecar_analysis_reuses_cached_sidecar(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    cache_store, cache_key = _create_analysis_cache(tmp_path, sample_wav_path)
    calls: list[tuple[Path, ChannelMode]] = []
    progress_stages: list[tuple[AnalysisSidecarKind, str]] = []

    def _tempo_analyzer(audio_path: Path, channel_mode: ChannelMode) -> TempoAnalysisResult:
        calls.append((audio_path, channel_mode))
        return TempoAnalysisResult(
            candidates=(
                TempoAnalysisCandidate(
                    bpm=120.0,
                    first_beat_seconds=0.0,
                    offset_ms=0.0,
                    confidence=0.9,
                    candidate_rank=1,
                ),
            ),
        )

    first = execute_tempo_sidecar_analysis(
        source_audio_path=sample_wav_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=ChannelMode.LEFT,
        analyzer=_tempo_analyzer,
        progress_callback=lambda progress: progress_stages.append((progress.kind, progress.stage)),
    )
    second = execute_tempo_sidecar_analysis(
        source_audio_path=sample_wav_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=ChannelMode.LEFT,
        analyzer=_tempo_analyzer,
    )

    assert len(calls) == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.payload == first.payload
    assert second.payload.channel_mode == ChannelMode.LEFT
    assert (AnalysisSidecarKind.TEMPO, "compute") in progress_stages
    assert (AnalysisSidecarKind.TEMPO, "persist") in progress_stages


def test_execute_chord_sidecar_analysis_force_recompute_bypasses_existing_cache(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    cache_store, cache_key = _create_analysis_cache(tmp_path, sample_wav_path)
    call_count = 0

    def _chord_analyzer(_audio_path: Path, channel_mode: ChannelMode) -> ChordAnalysisResult:
        nonlocal call_count
        call_count += 1
        return ChordAnalysisResult(
            channel_mode=channel_mode,
            segments=(ChordSegment(start_seconds=0.0, end_seconds=1.0, label=f"C#{call_count}", confidence=0.6),),
        )

    first = execute_chord_sidecar_analysis(
        source_audio_path=sample_wav_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=ChannelMode.RIGHT,
        analyzer=_chord_analyzer,
    )
    second = execute_chord_sidecar_analysis(
        source_audio_path=sample_wav_path,
        cache_store=cache_store,
        cache_key=cache_key,
        channel_mode=ChannelMode.RIGHT,
        analyzer=_chord_analyzer,
        options=SidecarAnalysisExecutionOptions(force_recompute=True),
    )

    assert call_count == 2
    assert first.from_cache is False
    assert second.from_cache is False
    assert first.payload.segments[0].label == "C#1"
    assert second.payload.segments[0].label == "C#2"
