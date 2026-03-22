from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectracer.core.config import AnalyzeCliConfig
from spectracer.core.models import ChannelMode
from spectracer.ui import main_window as main_window_module
from spectracer.ui.main_window import AnalysisWorker, SpectracerMainWindow


def test_load_initial_runtime_config_unpacks_runtime_helper_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_config = AnalyzeCliConfig(sensitivity=1.5, contrast=2.0)
    expected_path = Path("config/test-analysis.toml")
    captured_explicit_paths: list[str | Path | None] = []

    def _fake_load_runtime_analyze_config(explicit_path: str | Path | None) -> tuple[AnalyzeCliConfig, Path | None]:
        captured_explicit_paths.append(explicit_path)
        return expected_config, expected_path

    monkeypatch.setattr(main_window_module, "load_runtime_analyze_config", _fake_load_runtime_analyze_config)

    config, config_path, error = SpectracerMainWindow._load_initial_runtime_config(object())

    assert captured_explicit_paths == [None]
    assert config is expected_config
    assert config_path == expected_path
    assert error is None


def test_load_initial_runtime_config_falls_back_to_defaults_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_load_error(explicit_path: str | Path | None) -> tuple[AnalyzeCliConfig, Path | None]:
        _ = explicit_path
        raise ValueError("bad config")

    monkeypatch.setattr(main_window_module, "load_runtime_analyze_config", _raise_load_error)

    config, config_path, error = SpectracerMainWindow._load_initial_runtime_config(object())

    assert isinstance(config, AnalyzeCliConfig)
    assert config_path is None
    assert error == "bad config"


def test_set_display_controls_updates_view_with_keyword_arguments() -> None:
    class _Label:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class _Slider:
        def __init__(self) -> None:
            self.blocked: list[bool] = []
            self.value = 0

        def blockSignals(self, blocked: bool) -> None:
            self.blocked.append(bool(blocked))

        def setValue(self, value: int) -> None:
            self.value = int(value)

    class _View:
        def __init__(self) -> None:
            self.calls: list[tuple[float, float]] = []

        def update_display_settings(self, *, sensitivity: float, contrast: float) -> None:
            self.calls.append((float(sensitivity), float(contrast)))

    class _Window:
        def __init__(self) -> None:
            self.sensitivity_value_label = _Label()
            self.contrast_value_label = _Label()
            self.sensitivity_slider = _Slider()
            self.contrast_slider = _Slider()
            self.spectrogram_view = _View()

        @staticmethod
        def _display_value_to_slider(value: float) -> int:
            return int(round(float(value) * 100.0))

    window = _Window()

    SpectracerMainWindow._set_display_controls(window, 1.25, 2.5)

    assert window.spectrogram_view.calls == [(1.25, 2.5)]
    assert window.sensitivity_value_label.text == "1.25"
    assert window.contrast_value_label.text == "2.50"


def test_open_colormap_editor_uses_current_dialog_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_constructor_args: list[tuple[list[tuple[float, str]], object]] = []

    class _FakeDialog:
        class DialogCode:
            Accepted = 1

        def __init__(self, *, initial_stops=None, parent=None) -> None:
            captured_constructor_args.append((list(initial_stops), parent))

        def exec(self) -> int:
            return self.DialogCode.Accepted

        def stops(self) -> list[tuple[float, str]]:
            return [(0.0, "#000000"), (1.0, "#ffffff")]

    class _StatusMessage:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class _View:
        def __init__(self) -> None:
            self.applied_stops = None

        def set_colormap_stops(self, stops) -> None:
            self.applied_stops = list(stops)

    class _Window:
        def __init__(self) -> None:
            self._colormap_stops = [(0.0, "#112233"), (1.0, "#445566")]
            self.spectrogram_view = _View()
            self.status_message = _StatusMessage()
            self.saved_stops = None

        def _save_colormap_stops(self, stops) -> None:
            self.saved_stops = list(stops)

    monkeypatch.setattr(main_window_module, "ColormapEditorDialog", _FakeDialog)
    window = _Window()

    SpectracerMainWindow._open_colormap_editor(window)

    assert captured_constructor_args == [([(0.0, "#112233"), (1.0, "#445566")], window)]
    assert window._colormap_stops == [(0.0, "#000000"), (1.0, "#ffffff")]
    assert window.spectrogram_view.applied_stops == [(0.0, "#000000"), (1.0, "#ffffff")]
    assert window.saved_stops == [(0.0, "#000000"), (1.0, "#ffffff")]
    assert window.status_message.text == "已更新热图色盘"


@pytest.mark.parametrize(
    ("channel_mode", "channel_modes", "expected_save_playback_audio"),
    [
        (ChannelMode.STEREO, [ChannelMode.STEREO], False),
        (ChannelMode.STEREO, [ChannelMode.STEREO, ChannelMode.LEFT], True),
        (ChannelMode.LEFT, [ChannelMode.LEFT], True),
    ],
)
def test_analysis_worker_sets_playback_audio_generation_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    channel_mode: ChannelMode,
    channel_modes: list[ChannelMode],
    expected_save_playback_audio: bool,
) -> None:
    captured_flags: list[bool] = []

    def _fake_execute_multi_channel_analysis(*args, **kwargs):
        captured_flags.append(bool(kwargs["options"].save_playback_audio))
        return object()

    monkeypatch.setattr(main_window_module, "execute_multi_channel_analysis", _fake_execute_multi_channel_analysis)

    worker = AnalysisWorker(
        audio_path=Path("input.wav"),
        output_dir=Path(".spectracer_cache"),
        config=AnalyzeCliConfig(channel_mode=channel_mode),
        channel_modes=channel_modes,
    )

    worker.run()

    assert captured_flags == [expected_save_playback_audio]
