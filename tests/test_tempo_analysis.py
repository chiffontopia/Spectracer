from __future__ import annotations

from pathlib import Path

import pytest

from spectracer.core.models import ChannelMode
from spectracer.dsp.tempo_analysis import analyze_tempo_candidates

ROOT = Path(__file__).resolve().parents[1]


def test_analyze_tempo_candidates_detects_primary_120bpm_candidate(sample_wav_path: Path) -> None:
    result = analyze_tempo_candidates(sample_wav_path, ChannelMode.MONO)

    primary = result.primary_candidate()

    assert primary is not None
    assert result.channel_mode == ChannelMode.MONO
    assert primary.bpm > 0.0
    assert primary.bpm.is_integer()
    assert primary.bpm == pytest.approx(120.0, abs=2.0)
    assert primary.first_beat_seconds == pytest.approx(0.5, abs=0.2)
    assert primary.offset_ms == pytest.approx(0.0, abs=1e-6)
    assert primary.applies_offset is False
    assert any(candidate.bpm == pytest.approx(60.0, abs=2.0) for candidate in result.candidates)
    assert result.beat_anchors
    assert result.tempo_segments[0].bpm == pytest.approx(primary.bpm, abs=0.01)


def test_analyze_tempo_candidates_surfaces_half_double_ambiguity_for_twinklestar() -> None:
    sample_path = ROOT / "tests" / "twinklestar_80bpm_2bars_piano.wav"
    if not sample_path.exists():
        pytest.skip(f"缺少测试音频: {sample_path}")

    result = analyze_tempo_candidates(sample_path, ChannelMode.MONO)

    primary = result.primary_candidate()
    assert primary is not None
    assert primary.bpm > 0.0
    assert primary.bpm.is_integer()
    assert primary.bpm == pytest.approx(80.0, abs=2.0)
    assert any(candidate.bpm == pytest.approx(160.0, abs=3.0) for candidate in result.candidates)
    assert primary.label in {"主候选", None}
    assert result.selected_candidate_rank == 1
