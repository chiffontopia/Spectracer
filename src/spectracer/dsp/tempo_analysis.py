from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from spectracer.audio.channel_modes import apply_channel_mode
from spectracer.audio.io import load_audio
from spectracer.core.analysis_results import BeatAnchor, TempoAnalysisCandidate, TempoAnalysisResult, TempoSegment
from spectracer.core.models import ChannelMode

DEFAULT_TEMPO_ANALYSIS_SAMPLE_RATE: int | None = None
DEFAULT_TEMPO_ANALYSIS_SEEDS = (60.0, 72.0, 80.0, 90.0, 100.0, 120.0, 140.0, 160.0, 180.0)
MIN_TEMPO_CANDIDATE_BPM = 40.0
MAX_TEMPO_CANDIDATE_BPM = 210.0
TEMPO_CANDIDATE_MERGE_TOLERANCE_BPM = 0.75


@dataclass(slots=True)
class _ScoredTempoCandidate:
    bpm: float
    first_beat_seconds: float
    precision: float
    recall: float
    raw_score: float
    adjusted_score: float
    beat_times: np.ndarray


def analyze_tempo_candidates(
    source_audio_path: str | Path,
    channel_mode: ChannelMode,
    *,
    target_sample_rate: int | None = DEFAULT_TEMPO_ANALYSIS_SAMPLE_RATE,
    candidate_limit: int = 5,
) -> TempoAnalysisResult:
    audio, sample_rate = load_audio(source_audio_path, target_sample_rate=target_sample_rate)
    signal = apply_channel_mode(audio, channel_mode)
    if signal.size == 0:
        raise ValueError("音频为空，无法进行节拍分析")

    duration_seconds = float(signal.shape[-1]) / float(sample_rate)
    onset_envelope = librosa.onset.onset_strength(y=signal, sr=sample_rate, aggregate=np.median)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        units="frames",
        backtrack=False,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sample_rate)
    onset_strengths = onset_envelope[onset_frames] if onset_frames.size else np.empty((0,), dtype=np.float64)

    scored_candidates = _collect_scored_candidates(
        onset_envelope=np.asarray(onset_envelope, dtype=np.float64),
        onset_times=np.asarray(onset_times, dtype=np.float64),
        onset_strengths=np.asarray(onset_strengths, dtype=np.float64),
        sample_rate=int(sample_rate),
        duration_seconds=duration_seconds,
    )
    if not scored_candidates:
        scored_candidates = [
            _build_fallback_candidate(
                onset_envelope=np.asarray(onset_envelope, dtype=np.float64),
                onset_times=np.asarray(onset_times, dtype=np.float64),
                onset_strengths=np.asarray(onset_strengths, dtype=np.float64),
                sample_rate=int(sample_rate),
                duration_seconds=duration_seconds,
            )
        ]

    _apply_harmonic_preference(scored_candidates)
    ordered_candidates = sorted(
        scored_candidates,
        key=lambda candidate: (candidate.adjusted_score, candidate.raw_score, -candidate.bpm),
        reverse=True,
    )[: max(1, int(candidate_limit))]

    primary = ordered_candidates[0]
    result_candidates: list[TempoAnalysisCandidate] = []
    for index, candidate in enumerate(ordered_candidates, start=1):
        label = _label_for_candidate(primary_bpm=primary.bpm, bpm=candidate.bpm, rank=index)
        result_candidates.append(
            TempoAnalysisCandidate(
                bpm=float(candidate.bpm),
                first_beat_seconds=float(candidate.first_beat_seconds),
                offset_ms=0.0,
                confidence=float(_clamp01(candidate.raw_score)),
                candidate_rank=index,
                label=label,
                applies_offset=False,
            )
        )

    beat_anchors = tuple(
        BeatAnchor(beat_index=float(index), time_seconds=float(beat_time), confidence=float(primary.raw_score))
        for index, beat_time in enumerate(primary.beat_times[:32])
    )
    tempo_segments = (
        TempoSegment(
            start_seconds=0.0,
            end_seconds=max(0.0, duration_seconds),
            bpm=float(primary.bpm),
            confidence=float(_clamp01(primary.raw_score)),
        ),
    )

    return TempoAnalysisResult(
        candidates=tuple(result_candidates),
        channel_mode=channel_mode,
        selected_candidate_rank=1,
        beat_anchors=beat_anchors,
        tempo_segments=tempo_segments,
        analysis_basis="multi_seed_beat_track",
        notes=(
            f"sample_rate={sample_rate}; onsets={int(onset_times.size)}; "
            f"candidate_count={len(result_candidates)}"
        ),
    )


def _collect_scored_candidates(
    *,
    onset_envelope: np.ndarray,
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
    sample_rate: int,
    duration_seconds: float,
) -> list[_ScoredTempoCandidate]:
    scored_candidates: list[_ScoredTempoCandidate] = []
    for seed_bpm in DEFAULT_TEMPO_ANALYSIS_SEEDS:
        tempo_estimate, beat_times = librosa.beat.beat_track(
            onset_envelope=onset_envelope,
            sr=sample_rate,
            start_bpm=float(seed_bpm),
            units="time",
        )
        bpm = _normalize_candidate_bpm(np.ravel(np.asarray(tempo_estimate, dtype=np.float64))[0])
        if not math.isfinite(bpm) or bpm < MIN_TEMPO_CANDIDATE_BPM or bpm > MAX_TEMPO_CANDIDATE_BPM:
            continue

        normalized_beats = np.asarray(beat_times, dtype=np.float64).ravel()
        first_beat_seconds = float(normalized_beats[0]) if normalized_beats.size else _fallback_first_beat(onset_times)
        precision, recall, raw_score = _score_candidate(
            onset_envelope=onset_envelope,
            onset_times=onset_times,
            onset_strengths=onset_strengths,
            sample_rate=sample_rate,
            bpm=bpm,
            first_beat_seconds=first_beat_seconds,
            duration_seconds=duration_seconds,
        )
        candidate = _ScoredTempoCandidate(
            bpm=bpm,
            first_beat_seconds=first_beat_seconds,
            precision=precision,
            recall=recall,
            raw_score=raw_score,
            adjusted_score=raw_score + _preferred_range_bonus(bpm),
            beat_times=np.asarray(normalized_beats, dtype=np.float64),
        )
        _merge_scored_candidate(scored_candidates, candidate)
    return scored_candidates


def _build_fallback_candidate(
    *,
    onset_envelope: np.ndarray,
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
    sample_rate: int,
    duration_seconds: float,
) -> _ScoredTempoCandidate:
    tempo_estimate = librosa.feature.tempo(onset_envelope=onset_envelope, sr=sample_rate, aggregate=np.median)
    bpm = _normalize_candidate_bpm(np.ravel(np.asarray(tempo_estimate, dtype=np.float64))[0]) if np.size(tempo_estimate) else 120.0
    if not math.isfinite(bpm) or bpm <= 0.0:
        bpm = 120.0
    bpm = _normalize_candidate_bpm(min(MAX_TEMPO_CANDIDATE_BPM, max(MIN_TEMPO_CANDIDATE_BPM, bpm)))
    first_beat_seconds = _fallback_first_beat(onset_times)
    precision, recall, raw_score = _score_candidate(
        onset_envelope=onset_envelope,
        onset_times=onset_times,
        onset_strengths=onset_strengths,
        sample_rate=sample_rate,
        bpm=bpm,
        first_beat_seconds=first_beat_seconds,
        duration_seconds=duration_seconds,
    )
    beat_times = np.arange(first_beat_seconds, duration_seconds + ((60.0 / bpm) * 0.5), 60.0 / bpm, dtype=np.float64)
    return _ScoredTempoCandidate(
        bpm=bpm,
        first_beat_seconds=first_beat_seconds,
        precision=precision,
        recall=recall,
        raw_score=raw_score,
        adjusted_score=raw_score + _preferred_range_bonus(bpm),
        beat_times=beat_times,
    )


def _score_candidate(
    *,
    onset_envelope: np.ndarray,
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
    sample_rate: int,
    bpm: float,
    first_beat_seconds: float,
    duration_seconds: float,
) -> tuple[float, float, float]:
    period_seconds = 60.0 / float(bpm)
    if period_seconds <= 0.0:
        return 0.0, 0.0, 0.0

    beat_times = np.arange(first_beat_seconds, duration_seconds + (period_seconds * 0.5), period_seconds, dtype=np.float64)
    precision = _beat_precision(onset_envelope, beat_times, sample_rate)
    recall = _onset_recall(onset_times, onset_strengths, period_seconds, first_beat_seconds)
    raw_score = (2.0 * precision * recall) / max(1e-9, precision + recall)
    return float(_clamp01(precision)), float(_clamp01(recall)), float(_clamp01(raw_score))


def _beat_precision(onset_envelope: np.ndarray, beat_times: np.ndarray, sample_rate: int) -> float:
    if beat_times.size == 0 or onset_envelope.size == 0:
        return 0.0
    reference_peak = float(np.max(onset_envelope))
    if reference_peak <= 1e-9:
        return 0.0

    beat_frames = librosa.time_to_frames(beat_times, sr=sample_rate)
    local_peaks: list[float] = []
    for frame in np.asarray(beat_frames, dtype=np.int64):
        start = max(0, int(frame) - 1)
        end = min(int(onset_envelope.size), int(frame) + 2)
        if end <= start:
            continue
        local_peaks.append(float(np.max(onset_envelope[start:end])))
    if not local_peaks:
        return 0.0
    return float(np.mean(local_peaks) / reference_peak)


def _onset_recall(
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
    period_seconds: float,
    first_beat_seconds: float,
) -> float:
    if onset_times.size == 0:
        return 0.0
    weights = onset_strengths if onset_strengths.size == onset_times.size else np.ones_like(onset_times)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 1e-9:
        return 0.0

    sigma_seconds = min(0.08, period_seconds * 0.18)
    phase_distance = np.abs(((onset_times - first_beat_seconds + (0.5 * period_seconds)) % period_seconds) - (0.5 * period_seconds))
    alignment = np.exp(-(phase_distance**2) / max(1e-12, 2.0 * sigma_seconds * sigma_seconds))
    return float(np.sum(weights * alignment) / weight_sum)


def _merge_scored_candidate(candidates: list[_ScoredTempoCandidate], candidate: _ScoredTempoCandidate) -> None:
    for index, existing in enumerate(candidates):
        if abs(existing.bpm - candidate.bpm) > TEMPO_CANDIDATE_MERGE_TOLERANCE_BPM:
            continue
        if candidate.raw_score > existing.raw_score:
            candidates[index] = candidate
        return
    candidates.append(candidate)


def _apply_harmonic_preference(candidates: list[_ScoredTempoCandidate]) -> None:
    for candidate in candidates:
        candidate.adjusted_score = candidate.raw_score + _preferred_range_bonus(candidate.bpm)

    for candidate in candidates:
        half_time = _find_related_candidate(candidates, candidate.bpm / 2.0)
        double_time = _find_related_candidate(candidates, candidate.bpm * 2.0)
        if candidate.bpm >= 150.0 and half_time is not None and half_time.raw_score >= candidate.raw_score * 0.8:
            candidate.adjusted_score -= 0.07
        if candidate.bpm <= 75.0 and double_time is not None and double_time.raw_score >= candidate.raw_score * 0.8:
            candidate.adjusted_score -= 0.07


def _find_related_candidate(candidates: list[_ScoredTempoCandidate], target_bpm: float) -> _ScoredTempoCandidate | None:
    for candidate in candidates:
        if abs(candidate.bpm - target_bpm) <= TEMPO_CANDIDATE_MERGE_TOLERANCE_BPM:
            return candidate
    return None


def _preferred_range_bonus(bpm: float) -> float:
    if 70.0 <= bpm <= 150.0:
        return 0.03
    if bpm < 55.0 or bpm > 185.0:
        return -0.03
    return 0.0


def _fallback_first_beat(onset_times: np.ndarray) -> float:
    if onset_times.size == 0:
        return 0.0
    return float(onset_times[0])


def _normalize_candidate_bpm(value: float) -> float:
    bpm = float(value)
    if not math.isfinite(bpm):
        return bpm
    bpm = min(MAX_TEMPO_CANDIDATE_BPM, max(MIN_TEMPO_CANDIDATE_BPM, bpm))
    return float(max(1, int(round(bpm))))


def _label_for_candidate(*, primary_bpm: float, bpm: float, rank: int) -> str:
    if rank == 1:
        return "主候选"
    ratio = float(bpm) / max(1e-9, float(primary_bpm))
    if 1.9 <= ratio <= 2.1:
        return "倍速候选"
    if 0.45 <= ratio <= 0.55:
        return "半速候选"
    return f"候选 {rank}"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "DEFAULT_TEMPO_ANALYSIS_SAMPLE_RATE",
    "analyze_tempo_candidates",
]
