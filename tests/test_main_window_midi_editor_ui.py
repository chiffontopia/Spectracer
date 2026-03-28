from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectracer.core.config import AnalyzeCliConfig
from spectracer.core.models import CqtResult
from spectracer.midi.editor_model import MidiChannelConfig, MidiEditorTool, MidiNote
from spectracer.ui import main_window as main_window_module
from spectracer.ui.main_window import SpectracerMainWindow
from spectracer.ui.overlays.midi_note_overlay import midi_note_to_frequency


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@dataclass(slots=True)
class _FakePlaybackState:
    is_playing: bool = False
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    sample_rate: int = 0
    source_path: str | None = None
    playback_rate: float = 1.0
    volume: float = 0.85


@dataclass(slots=True)
class _FakeMidiStatus:
    backend_name: str = "dummy"
    soundfont_path: str | None = None
    output_name: str | None = None
    message: str = "ready"


class _FakeSettings:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def value(self, key: str, default: object = None) -> object:
        return self._store.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self._store[key] = value


class _FakePlaybackEngine(QObject):
    position_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    playback_state_changed = pyqtSignal(bool)
    media_loaded = pyqtSignal(str)
    playback_rate_changed = pyqtSignal(float)
    volume_changed = pyqtSignal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = _FakePlaybackState()

    def set_playback_rate(self, rate: float) -> None:
        self.state.playback_rate = float(rate)
        self.playback_rate_changed.emit(self.state.playback_rate)

    def set_volume(self, volume: float) -> None:
        self.state.volume = max(0.0, min(1.0, float(volume)))
        self.volume_changed.emit(self.state.volume)

    def toggle(self) -> None:
        self.state.is_playing = not self.state.is_playing
        self.playback_state_changed.emit(self.state.is_playing)

    def play(self) -> None:
        self.state.is_playing = True
        self.playback_state_changed.emit(True)

    def pause(self) -> None:
        self.state.is_playing = False
        self.playback_state_changed.emit(False)

    def load(self, path, *, position_seconds: float = 0.0, autoplay: bool = False) -> None:
        self.state.source_path = str(path)
        self.state.position_seconds = float(position_seconds)
        self.media_loaded.emit(self.state.source_path)
        if autoplay:
            self.play()

    def seek(self, seconds: float) -> None:
        self.state.position_seconds = max(0.0, float(seconds))
        self.position_changed.emit(self.state.position_seconds)


class _FakeMidiSynth:
    def __init__(self) -> None:
        self.status = _FakeMidiStatus()
        self.is_available = True
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def panic(self) -> None:
        return None

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        _ = (note, velocity, channel)

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        _ = (note, channel)

    def apply_channel_config(self, config) -> None:
        _ = config


class _FakeMidiPlaybackController:
    def __init__(self, playback_engine, midi_synth, *, session=None, timeline=None, parent=None) -> None:
        self.playback_engine = playback_engine
        self.midi_synth = midi_synth
        self.session = session
        self.timeline = timeline
        self.parent = parent
        self.midi_gain = 0.6
        self.closed = False

    def set_timeline(self, timeline) -> None:
        self.timeline = timeline

    def set_synth(self, midi_synth) -> None:
        self.midi_synth = midi_synth

    def set_midi_gain(self, value: float) -> None:
        self.midi_gain = max(0.0, min(1.0, float(value)))

    def close(self) -> None:
        self.closed = True


def _patch_main_window_environment(
    monkeypatch: pytest.MonkeyPatch,
    settings_store: dict[str, object],
) -> None:
    monkeypatch.setattr(main_window_module, "QSettings", lambda *args, **kwargs: _FakeSettings(settings_store))
    monkeypatch.setattr(
        main_window_module,
        "load_runtime_analyze_config",
        lambda _explicit_path: (AnalyzeCliConfig(), None),
    )
    monkeypatch.setattr(main_window_module, "PlaybackEngine", _FakePlaybackEngine)
    monkeypatch.setattr(main_window_module, "create_default_midi_synth", lambda **kwargs: _FakeMidiSynth())
    monkeypatch.setattr(main_window_module, "MidiPlaybackController", _FakeMidiPlaybackController)


def _make_test_cqt_result() -> CqtResult:
    midi_pitches = np.arange(36, 85, dtype=np.float64)
    bin_frequencies = np.array([midi_note_to_frequency(pitch) for pitch in midi_pitches], dtype=np.float64)
    frame_times = np.linspace(0.0, 2.0, 9, dtype=np.float64)
    magnitude = np.zeros((frame_times.size, bin_frequencies.size), dtype=np.float32)
    return CqtResult(
        magnitude=magnitude,
        frame_times=frame_times,
        bin_frequencies=bin_frequencies,
        hop_length=512,
        sample_rate=22050,
    )


def test_main_window_editor_controls_drive_overlay_state_and_mix(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.resize(1280, 820)
    window.show()
    window._midi_session.add_note(MidiNote(id="note-1", pitch=60, start_beat=1.0, duration_beats=1.0, channel=2), record_undo=False)
    window.spectrogram_view.set_cqt_result(_make_test_cqt_result())
    qapp.processEvents()

    assert window.edit_mode_checkbox.isChecked() is False
    assert window.spectrogram_view.is_midi_overlay_visible() is False
    assert window.spectrogram_view.is_dim_overlay_visible() is False
    assert window.playback_engine.state.volume == pytest.approx(0.85)
    assert window._midi_playback_controller.midi_gain == pytest.approx(0.6)

    window.edit_mode_checkbox.setChecked(True)
    qapp.processEvents()

    assert window._midi_session.editor_state.enabled is True
    assert window.spectrogram_view.is_midi_overlay_visible() is True
    assert window.spectrogram_view.is_dim_overlay_visible() is True

    window.place_tool_button.click()
    qapp.processEvents()
    assert window._midi_session.editor_state.tool is MidiEditorTool.PLACE
    assert settings_store["midi_editor/tool"] == MidiEditorTool.PLACE.value

    window.editor_snap_checkbox.setChecked(False)
    qapp.processEvents()
    assert window._midi_session.editor_state.snap_enabled is False
    assert window.editor_snap_resolution_combo.isEnabled() is False

    snap_index = window.editor_snap_resolution_combo.findData("1/32")
    window.editor_snap_checkbox.setChecked(True)
    window.editor_snap_resolution_combo.setCurrentIndex(snap_index)
    qapp.processEvents()
    assert window._midi_session.editor_state.snap_enabled is True
    assert window._midi_session.editor_state.snap_resolution.value == "1/32"

    channel_index = window.editor_active_channel_combo.findData(5)
    window.editor_active_channel_combo.setCurrentIndex(channel_index)
    qapp.processEvents()
    assert window._midi_session.editor_state.active_channel == 5
    assert settings_store["midi_editor/active_channel"] == 5

    window.editor_darken_slider.setValue(70)
    qapp.processEvents()
    assert window._midi_session.editor_state.darken_amount == pytest.approx(0.7)
    assert window.editor_darken_value_label.text() == "70%"
    assert window.spectrogram_view.dim_overlay_alpha() == 178

    window.background_mix_slider.setValue(35)
    window.midi_mix_slider.setValue(80)
    qapp.processEvents()
    assert window.playback_engine.state.volume == pytest.approx(0.35)
    assert window._midi_playback_controller.midi_gain == pytest.approx(0.8)
    assert window.background_mix_value_label.text() == "35%"
    assert window.midi_mix_value_label.text() == "80%"
    assert settings_store["mixer/background_gain"] == pytest.approx(0.35)
    assert settings_store["mixer/midi_gain"] == pytest.approx(0.8)

    window.close()


def test_main_window_restores_editor_and_mix_settings_from_qsettings(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    settings_store: dict[str, object] = {
        "midi_editor/enabled": True,
        "midi_editor/tool": MidiEditorTool.ERASE.value,
        "midi_editor/active_channel": 9,
        "midi_editor/snap_enabled": False,
        "midi_editor/snap_resolution": "1/32",
        "midi_editor/darken_amount": 0.55,
        "mixer/background_gain": 0.25,
        "mixer/midi_gain": 0.9,
    }
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.show()
    qapp.processEvents()

    assert window.edit_mode_checkbox.isChecked() is True
    assert window.erase_tool_button.isChecked() is True
    assert window.editor_active_channel_combo.currentData() == 9
    assert window.editor_snap_checkbox.isChecked() is False
    assert window.editor_snap_resolution_combo.currentData() == "1/32"
    assert window.editor_snap_resolution_combo.isEnabled() is False
    assert window.editor_darken_slider.value() == 55
    assert window.editor_darken_value_label.text() == "55%"
    assert window._midi_session.editor_state.tool is MidiEditorTool.ERASE
    assert window._midi_session.editor_state.active_channel == 9
    assert window._midi_session.editor_state.darken_amount == pytest.approx(0.55)
    assert window.spectrogram_view.midi_overlay_darken_amount() == pytest.approx(0.55)
    assert window.background_mix_slider.value() == 25
    assert window.midi_mix_slider.value() == 90
    assert window.playback_engine.state.volume == pytest.approx(0.25)
    assert window._midi_playback_controller.midi_gain == pytest.approx(0.9)

    window.close()


def test_main_window_toolbar_groups_project_and_history_view_actions(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.show()
    qapp.processEvents()

    assert window.project_toolbar_button.text() == "项目"
    assert window.history_view_toolbar_button.text() == "历史/视图"

    assert window.main_toolbar.widgetForAction(window.open_action) is None
    assert window.main_toolbar.widgetForAction(window.export_midi_action) is None
    assert window.main_toolbar.widgetForAction(window.undo_action) is None
    assert window.main_toolbar.widgetForAction(window.redo_action) is None
    assert window.main_toolbar.widgetForAction(window.zoom_reset_action) is None
    assert window.main_toolbar.widgetForAction(window.zoom_x_in_action) is None
    assert window.main_toolbar.widgetForAction(window.zoom_x_out_action) is None
    assert window.main_toolbar.widgetForAction(window.zoom_y_in_action) is None
    assert window.main_toolbar.widgetForAction(window.zoom_y_out_action) is None

    project_menu = window.project_toolbar_button.menu()
    assert project_menu is not None
    assert [action.text() for action in project_menu.actions() if not action.isSeparator()] == [
        "打开音频",
        "导出 MIDI...",
        "无缓存模式",
        "清理未使用缓存…",
    ]

    history_view_menu = window.history_view_toolbar_button.menu()
    assert history_view_menu is not None
    assert [action.text() for action in history_view_menu.actions() if not action.isSeparator()] == ["撤销", "重做", "重置视图", "横向放大", "横向缩小", "纵向放大", "纵向缩小"]

    window.close()


def test_main_window_no_cache_mode_uses_temporary_analysis_directory(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)
    monkeypatch.setattr(main_window_module, "DEFAULT_CACHE_DIR", tmp_path / ".spectracer_cache")

    window = SpectracerMainWindow()
    window.show()
    window.cache_disabled_action.setChecked(True)
    qapp.processEvents()

    output_dir = window._prepare_analysis_output_dir()
    assert settings_store["cache/disabled"] is True
    assert output_dir.exists()
    assert output_dir != main_window_module.DEFAULT_CACHE_DIR

    window.close()
    assert output_dir.exists() is False


def test_main_window_editor_shortcuts_and_session_signals_keep_controls_in_sync(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.show()
    window.activateWindow()
    window.setFocus()
    qapp.processEvents()

    QTest.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.edit_mode_checkbox.isChecked() is True
    assert settings_store["midi_editor/enabled"] is True

    QTest.keyClick(window, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.place_tool_button.isChecked() is True
    assert window._midi_session.editor_state.tool is MidiEditorTool.PLACE
    assert settings_store["midi_editor/tool"] == MidiEditorTool.PLACE.value

    QTest.keyClick(window, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.erase_tool_button.isChecked() is True
    assert window._midi_session.editor_state.tool is MidiEditorTool.ERASE
    assert settings_store["midi_editor/tool"] == MidiEditorTool.ERASE.value

    synced_state = window._midi_session.editor_state.with_updates(
        enabled=True,
        tool=MidiEditorTool.SELECT,
        active_channel=3,
        snap_enabled=False,
        snap_resolution="1/32",
        darken_amount=0.42,
    )
    window._midi_session.set_editor_state(synced_state)
    window._midi_session.update_channel_config(3, name="Lead Synth")
    qapp.processEvents()

    assert window.edit_mode_checkbox.isChecked() is True
    assert window.select_tool_button.isChecked() is True
    assert window.editor_active_channel_combo.currentData() == 3
    assert window.editor_snap_checkbox.isChecked() is False
    assert window.editor_snap_resolution_combo.currentData() == "1/32"
    assert window.editor_darken_slider.value() == 42
    assert "Lead Synth" in window.editor_active_channel_combo.currentText()

    window.close()


def test_main_window_midi_clipboard_shortcuts_and_delete_key(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.show()
    window.activateWindow()
    window.setFocus()
    window._midi_session.add_note(MidiNote(id="copy-src", pitch=60, start_beat=1.0, duration_beats=0.5), record_undo=False)
    window._midi_session.select_note("copy-src")
    qapp.processEvents()

    QTest.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window._midi_session.editor_state.enabled is True

    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()

    assert len(window._midi_session.notes) == 2
    pasted_note = next(note for note in window._midi_session.notes if note.id != "copy-src")
    assert pasted_note.start_beat == pytest.approx(1.5)
    assert pasted_note.duration_beats == pytest.approx(0.5)
    assert window._midi_session.selected_note_ids == {pasted_note.id}
    assert window.status_message.text() == "已粘贴 1 个音符"

    window.event_track_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.event_track_view, Qt.Key.Key_Delete)
    qapp.processEvents()
    assert {note.id for note in window._midi_session.notes} == {"copy-src", pasted_note.id}

    window.setFocus()
    qapp.processEvents()
    QTest.keyClick(window, Qt.Key.Key_Delete)
    qapp.processEvents()
    assert {note.id for note in window._midi_session.notes} == {"copy-src"}
    assert window.status_message.text() == "已删除 1 个音符"

    window.close()



def test_main_window_channel_config_dialog_updates_session_combo_and_overlay(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    monkeypatch.setattr(
        main_window_module.ChannelConfigDialog,
        "get_config",
        lambda *, initial_config, parent=None: MidiChannelConfig(
            channel=initial_config.channel,
            name="Lead Synth",
            program=81,
            bank=0,
            pan=32,
            color="#FF4081",
            muted=True,
            solo=True,
        ),
    )

    window = SpectracerMainWindow()
    window.resize(1280, 820)
    window.show()
    window.edit_mode_checkbox.setChecked(True)
    window._midi_session.add_note(MidiNote(id="note-2", pitch=60, start_beat=1.0, duration_beats=1.0, channel=2), record_undo=False)
    window.spectrogram_view.set_cqt_result(_make_test_cqt_result())
    channel_index = window.editor_active_channel_combo.findData(2)
    window.editor_active_channel_combo.setCurrentIndex(channel_index)
    qapp.processEvents()

    window.channel_config_button.click()
    qapp.processEvents()

    updated_config = window._midi_session.get_channel_config(2)
    assert updated_config.display_name == "Lead Synth"
    assert updated_config.program == 81
    assert updated_config.pan == 32
    assert updated_config.color.lower() == "#ff4081"
    assert updated_config.muted is True
    assert updated_config.solo is True
    assert "Lead Synth" in window.editor_active_channel_combo.currentText()
    assert "独奏" in window.editor_active_channel_combo.currentText()
    assert "静音" in window.editor_active_channel_combo.currentText()

    geometry = window.spectrogram_view._midi_note_overlay.note_geometry("note-2")
    assert geometry is not None
    assert geometry.channel_color.name().lower() == "#ff4081"

    window.close()


def test_main_window_undo_redo_actions_follow_session_history(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    window = SpectracerMainWindow()
    window.show()
    window.activateWindow()
    window.setFocus()
    qapp.processEvents()

    created_note = MidiNote(id="undo-note", pitch=67, start_beat=0.5, duration_beats=0.75, channel=1)
    window._midi_session.add_note(created_note, select=True)
    qapp.processEvents()

    assert window.undo_action.isEnabled() is True
    assert window.redo_action.isEnabled() is False

    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()

    assert window._midi_session.notes == ()
    assert window.undo_action.isEnabled() is False
    assert window.redo_action.isEnabled() is True
    assert window.status_message.text().startswith("已撤销：")

    window.redo_action.trigger()
    qapp.processEvents()

    assert window._midi_session.get_note(created_note.id) == created_note
    assert window.undo_action.isEnabled() is True
    assert window.status_message.text().startswith("已重做：")

    window.close()


def test_main_window_export_action_passes_session_state_to_exporter(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path,
) -> None:
    settings_store: dict[str, object] = {}
    _patch_main_window_environment(monkeypatch, settings_store)

    captured: dict[str, object] = {}
    export_path = tmp_path / "session.mid"

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(export_path), "MIDI Files (*.mid)"),
    )

    def _fake_export(output_path, *, notes, channel_configs, timeline, ticks_per_beat=480, linear_tempo_strategy=None):
        captured["output_path"] = output_path
        captured["notes"] = tuple(notes)
        captured["channel_configs"] = tuple(channel_configs)
        captured["timeline"] = timeline
        captured["ticks_per_beat"] = ticks_per_beat
        captured["linear_tempo_strategy"] = linear_tempo_strategy
        return export_path

    monkeypatch.setattr(main_window_module, "export_notes_to_midi", _fake_export)

    window = SpectracerMainWindow()
    window._midi_session.add_note(MidiNote(id="export-note", pitch=64, start_beat=1.0, duration_beats=1.0, channel=4), record_undo=False)
    window.export_midi_action.trigger()
    qapp.processEvents()

    assert str(captured["output_path"]) == str(export_path)
    assert [note.id for note in captured["notes"]] == ["export-note"]
    assert len(captured["channel_configs"]) == 16
    assert captured["timeline"] == window._grid_timeline
    assert window.status_message.text() == f"已导出 MIDI：{export_path}"

    window.close()
