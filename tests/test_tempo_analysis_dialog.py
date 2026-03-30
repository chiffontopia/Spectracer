from __future__ import annotations

import os

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectracer.core.analysis_results import TempoAnalysisCandidate, TempoAnalysisResult
from spectracer.core.models import ChannelMode
from spectracer.ui.dialogs.tempo_analysis_dialog import TempoAnalysisDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_tempo_analysis_dialog_interval_and_tap_candidates(qapp: QApplication) -> None:
    _ = qapp
    dialog = TempoAnalysisDialog(
        current_bpm=120.0,
        current_offset_ms=0.0,
        current_channel_mode=ChannelMode.MONO,
        duration_seconds=12.0,
        default_tap_first_beat_seconds=0.25,
        default_interval_start_seconds=0.5,
        default_interval_end_seconds=2.5,
        default_interval_beats=4.0,
    )

    interval_candidate = dialog.interval_candidate()
    assert interval_candidate is not None
    assert interval_candidate.bpm == pytest.approx(120.0, abs=1e-6)
    assert interval_candidate.offset_ms == pytest.approx(500.0, abs=1e-6)

    dialog.record_tap(timestamp_seconds=0.0)
    dialog.record_tap(timestamp_seconds=0.5)
    dialog.record_tap(timestamp_seconds=1.0)
    tap_candidate = dialog.tap_candidate()

    assert tap_candidate is not None
    assert tap_candidate.bpm == pytest.approx(120.0, abs=1e-6)
    assert tap_candidate.first_beat_seconds == pytest.approx(0.25, abs=1e-6)
    assert dialog.tap_apply_button.isEnabled() is True


def test_tempo_analysis_dialog_populates_smart_candidate_table(qapp: QApplication) -> None:
    _ = qapp
    dialog = TempoAnalysisDialog(
        current_bpm=100.0,
        current_offset_ms=25.0,
        current_channel_mode=ChannelMode.LEFT,
        duration_seconds=8.0,
    )
    result = TempoAnalysisResult(
        selected_candidate_rank=2,
        candidates=(
            TempoAnalysisCandidate(
                bpm=100.0,
                first_beat_seconds=0.12,
                offset_ms=120.0,
                confidence=0.72,
                candidate_rank=1,
                label="主候选",
            ),
            TempoAnalysisCandidate(
                bpm=150.0,
                first_beat_seconds=0.06,
                offset_ms=60.0,
                confidence=0.44,
                candidate_rank=2,
                label="倍速候选",
            ),
        ),
    )

    dialog.set_smart_analysis_result(result, from_cache=True)
    selected = dialog.selected_smart_candidate()

    assert dialog.smart_candidates_table.rowCount() == 2
    assert selected is not None
    assert selected.candidate_rank == 2
    assert "缓存结果" in dialog.smart_status_label.text()
