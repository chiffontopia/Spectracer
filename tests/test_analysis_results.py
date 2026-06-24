from __future__ import annotations

from spectracer.core.analysis_results import (
    BeatAnchor,
    CURRENT_TEMPO_ANALYSIS_SCHEMA,
    SCHEMA_TEMPO_ANALYSIS_V1,
    TempoAnalysisCandidate,
    TempoAnalysisResult,
    TempoSegment,
)
from spectracer.core.models import ChannelMode


def test_tempo_analysis_result_roundtrip_preserves_future_tempo_map_fields() -> None:
    result = TempoAnalysisResult(
        channel_mode=ChannelMode.RIGHT,
        analysis_basis="global_bpm",
        selected_candidate_rank=2,
        candidates=(
            TempoAnalysisCandidate(
                bpm=89.5,
                first_beat_seconds=0.12,
                offset_ms=120.0,
                confidence=0.64,
                candidate_rank=2,
                label="主候选",
            ),
            TempoAnalysisCandidate(
                bpm=179.0,
                first_beat_seconds=0.06,
                offset_ms=60.0,
                confidence=0.31,
                candidate_rank=1,
                label="倍速候选",
            ),
        ),
        beat_anchors=(BeatAnchor(beat_index=0, time_seconds=0.12, confidence=0.7),),
        tempo_segments=(
            TempoSegment(
                start_seconds=0.0,
                end_seconds=8.0,
                bpm=89.5,
                confidence=0.58,
                start_beat=0.0,
                end_beat=12.0,
            ),
        ),
        notes="为未来变速曲目支持预留字段",
    )

    restored = TempoAnalysisResult.from_dict(result.to_dict())

    assert restored == result
    assert restored.primary_candidate() == result.candidates[0]


def test_tempo_analysis_result_from_legacy_schema_upgrades_to_integer_bpm_without_offset() -> None:
    restored = TempoAnalysisResult.from_dict(
        {
            "schema_version": SCHEMA_TEMPO_ANALYSIS_V1,
            "analysis_basis": "global_bpm",
            "selected_candidate_rank": 1,
            "candidates": [
                {
                    "bpm": 119.6,
                    "first_beat_seconds": 0.18,
                    "offset_ms": 180.0,
                    "confidence": 0.72,
                    "candidate_rank": 1,
                    "label": "主候选",
                }
            ],
        }
    )

    candidate = restored.primary_candidate()
    assert candidate is not None
    assert candidate.bpm == 120.0
    assert candidate.offset_ms == 0.0
    assert candidate.applies_offset is False
    assert restored.schema_version == CURRENT_TEMPO_ANALYSIS_SCHEMA
