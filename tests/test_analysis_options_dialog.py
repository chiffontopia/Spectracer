from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectracer.core.config import AnalyzeCliConfig
from spectracer.core.models import ChannelMode
from spectracer.ui.dialogs.analysis_options_dialog import AnalysisOptionsDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_analysis_options_dialog_exposes_common_sample_rates_including_48khz(qapp: QApplication) -> None:
    _ = qapp
    dialog = AnalysisOptionsDialog(
        audio_path=Path("example.wav"),
        initial_config=AnalyzeCliConfig(sample_rate=None),
        initial_channel_modes=[ChannelMode.STEREO],
        config_path=None,
    )

    sample_rate_values = [dialog.sample_rate_combo.itemData(index) for index in range(dialog.sample_rate_combo.count())]
    sample_rate_labels = [dialog.sample_rate_combo.itemText(index) for index in range(dialog.sample_rate_combo.count())]

    assert None in sample_rate_values
    assert 44100 in sample_rate_values
    assert 48000 in sample_rate_values
    assert "48000 Hz（推荐）" in sample_rate_labels
    assert "48kHz 通常有更好的解析性能" in dialog.sample_rate_combo.toolTip()

    dialog.sample_rate_combo.setCurrentIndex(dialog.sample_rate_combo.findData(48000))
    config = dialog.build_config()

    assert config.sample_rate == 48000


def test_analysis_options_dialog_preserves_nonstandard_initial_sample_rate(qapp: QApplication) -> None:
    _ = qapp
    dialog = AnalysisOptionsDialog(
        audio_path=Path("example.wav"),
        initial_config=AnalyzeCliConfig(sample_rate=11025),
        initial_channel_modes=[ChannelMode.STEREO],
        config_path=None,
    )

    index = dialog.sample_rate_combo.findData(11025)

    assert index >= 0
    assert dialog.sample_rate_combo.itemText(index) == "11025 Hz"
    assert dialog.build_config().sample_rate == 11025
