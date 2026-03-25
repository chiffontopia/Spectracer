from __future__ import annotations

import sys
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import QObject, QSettings, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollBar,
    QSlider,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from spectracer.app.analysis_workflow import (
    AnalysisProgress,
    AnalyzeExecutionOptions,
    AnalyzeExecutionResult,
    MultiChannelAnalysisResult,
    execute_multi_channel_analysis,
)
from spectracer.app.controllers.midi_editor_controller import MidiEditorController
from spectracer.audio.playback import PlaybackEngine
from spectracer.core.config import AnalyzeCliConfig, load_runtime_analyze_config
from spectracer.core.models import AnalysisParams, ChannelMode
from spectracer.core.pitch import frequency_to_midi
from spectracer.midi.editor_model import EventTrackLane, EventTrackSelection, MidiEditorState, MidiEditorTool, MidiSnapResolution
from spectracer.midi.exporter import export_notes_to_midi
from spectracer.midi.gm import gm_program_name, is_drum_channel
from spectracer.midi.grid import (
    GridDivision,
    MidiGridTimeline,
    TempoEvent,
    TempoTransition,
    TimeSignature,
    TimeSignatureEvent,
)
from spectracer.midi.playback_controller import MidiPlaybackController
from spectracer.midi.session import MidiSession
from spectracer.midi.synth import DEFAULT_GAIN, MidiSynth, create_default_midi_synth
from spectracer.dsp.colormap import ColorStop, default_spectracer_colormap_stops, normalize_colormap_stops
from spectracer.dsp.visualization import NormalizationMode
from spectracer.ui.dialogs.analysis_options_dialog import AnalysisOptionsDialog
from spectracer.ui.dialogs.channel_config_dialog import ChannelConfigDialog
from spectracer.ui.dialogs.grid_event_dialog import TempoEventDialog, TimeSignatureEventDialog
from spectracer.ui.dialogs.grid_settings_dialog import GridSettingsDialog
from spectracer.ui.overlays.event_track_widget import EventTrackLaneLabels, GridEventTrackWidget
from spectracer.ui.dialogs.midi_settings_dialog import MidiSettingsDialog
from spectracer.ui.dialogs.colormap_editor_dialog import ColormapEditorDialog
from spectracer.ui.views.spectrogram_view import HoverInfo, SpectrogramView, ViewState
from spectracer.ui.widgets.piano_keyboard import PianoKeyboardWidget

DEFAULT_CACHE_DIR = Path(".spectracer_cache")
SCROLLBAR_RESOLUTION = 1000
DISPLAY_SLIDER_MIN = 10
DISPLAY_SLIDER_MAX = 400
PLAYBACK_RATE_SLIDER_MIN = 50   # 0.50x
PLAYBACK_RATE_SLIDER_MAX = 200  # 2.00x
PLAYBACK_RATE_SLIDER_DEFAULT = 100  # 1.00x
MIDI_AUDITION_DURATION_MS = 360
MIDI_AUDITION_RETRIGGER_GAP_MS = 0
MIDI_AUDITION_VELOCITY = 108
FRACTION_SLIDER_RANGE = 100


def _default_grid_tempo_events() -> tuple[TempoEvent, ...]:
    return (TempoEvent(0.0, 120.0),)


def _default_grid_time_signature_events() -> tuple[TimeSignatureEvent, ...]:
    return (TimeSignatureEvent(0.0, TimeSignature(4, 4)),)


def _reset_tempo_events(events: Sequence[TempoEvent]) -> tuple[TempoEvent, ...]:
    if not events:
        return _default_grid_tempo_events()
    root = events[0]
    return (TempoEvent(0.0, root.bpm, root.transition),)


def _reset_time_signature_events(events: Sequence[TimeSignatureEvent]) -> tuple[TimeSignatureEvent, ...]:
    if not events:
        return _default_grid_time_signature_events()
    root = events[0]
    return (TimeSignatureEvent(0.0, root.time_signature),)


def _serialize_tempo_events(events: Sequence[TempoEvent]) -> str:
    payload = [
        {
            "beat_position": float(event.beat_position),
            "bpm": float(event.bpm),
            "transition": event.transition.value,
        }
        for event in events
    ]
    return json.dumps(payload, ensure_ascii=False)


def _serialize_time_signature_events(events: Sequence[TimeSignatureEvent]) -> str:
    payload = [
        {
            "beat_position": float(event.beat_position),
            "numerator": int(event.time_signature.numerator),
            "denominator": int(event.time_signature.denominator),
        }
        for event in events
    ]
    return json.dumps(payload, ensure_ascii=False)


def _parse_tempo_events_payload(raw: object) -> tuple[TempoEvent, ...]:
    if raw is None:
        return _default_grid_tempo_events()

    try:
        payload = json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return _default_grid_tempo_events()

    events: list[TempoEvent] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            try:
                events.append(
                    TempoEvent(
                        float(entry["beat_position"]),
                        float(entry["bpm"]),
                        TempoTransition.parse(entry.get("transition", TempoTransition.STEP.value)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

    return tuple(events) if events else _default_grid_tempo_events()


def _parse_time_signature_events_payload(raw: object) -> tuple[TimeSignatureEvent, ...]:
    if raw is None:
        return _default_grid_time_signature_events()

    try:
        payload = json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return _default_grid_time_signature_events()

    events: list[TimeSignatureEvent] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            try:
                events.append(
                    TimeSignatureEvent(
                        float(entry["beat_position"]),
                        TimeSignature(int(entry["numerator"]), int(entry["denominator"])),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

    return tuple(events) if events else _default_grid_time_signature_events()


@dataclass(slots=True)
class MidiUserSettings:
    output_name: str | None = None
    soundfont_path: str | None = None
    program: int = 0
    channel: int = 0


@dataclass(slots=True)
class MidiGridUserSettings:
    bpm: float = 120.0
    numerator: int = 4
    denominator: int = 4
    offset_ms: float = 0.0
    subdivisions_per_beat: int = 4
    visible: bool = True
    event_snap_enabled: bool = True
    event_track_visible: bool = True
    tempo_events: tuple[TempoEvent, ...] = field(default_factory=_default_grid_tempo_events)
    time_signature_events: tuple[TimeSignatureEvent, ...] = field(default_factory=_default_grid_time_signature_events)


class AnalysisWorker(QObject):
    progress_changed = pyqtSignal(object)
    mode_ready = pyqtSignal(object, object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(
        self,
        *,
        audio_path: Path,
        output_dir: Path,
        config: AnalyzeCliConfig,
        channel_modes: list[ChannelMode],
    ) -> None:
        super().__init__()
        self._audio_path = audio_path
        self._output_dir = output_dir
        self._config = config
        self._channel_modes = channel_modes

    def run(self) -> None:
        try:
            params = AnalysisParams(
                fps=self._config.fps,
                bins_per_semitone=self._config.bins_per_semitone,
                octave_min=self._config.octave_min,
                octave_max=self._config.octave_max,
                a4_hz=self._config.a4_hz,
                sample_rate=self._config.sample_rate,
                channel_mode=self._config.channel_mode,
            )
            options = AnalyzeExecutionOptions(
                processing_fingerprint=self._config.processing_fingerprint,
                sensitivity=self._config.sensitivity,
                contrast=self._config.contrast,
                save_preview=self._config.preview_enabled,
                save_playback_audio=(len(self._channel_modes) > 1 or self._config.channel_mode != ChannelMode.STEREO),
            )
            batch_result = execute_multi_channel_analysis(
                self._audio_path,
                output_dir=self._output_dir,
                params=params,
                channel_modes=self._channel_modes,
                options=options,
                progress_callback=self._handle_progress,
                mode_result_callback=self._handle_mode_ready,
            )
            self.finished.emit(batch_result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()

    def _handle_progress(self, progress: AnalysisProgress) -> None:
        self.progress_changed.emit(progress)

    def _handle_mode_ready(self, mode: ChannelMode, result: AnalyzeExecutionResult) -> None:
        self.mode_ready.emit(mode, result)


class SpectracerMainWindow(QMainWindow):
    def __init__(self, initial_audio_path: str | Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Spectracer - Week 2 Prototype (PyQt6)")
        self.resize(1480, 960)

        self._runtime_config, self._runtime_config_path, self._runtime_config_error = self._load_initial_runtime_config()
        self._session_config = self._runtime_config
        self._session_channel_modes = ChannelMode.ordered_modes()

        self._current_result: AnalyzeExecutionResult | None = None
        self._current_audio_path: Path | None = None
        self._current_channel_mode: ChannelMode | None = None
        self._channel_results: dict[ChannelMode, AnalyzeExecutionResult] = {}
        self._scrubbing = False
        self._analysis_busy = False
        self._analysis_primary_ready = False
        self._analysis_primary_mode: ChannelMode | None = None
        self._analysis_requested_modes: list[ChannelMode] = []
        self._updating_scrollbars = False
        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._display_sensitivity = self._runtime_config.sensitivity
        self._display_contrast = self._runtime_config.contrast
        self._last_view_state: ViewState | None = None
        self._settings = QSettings("Spectracer", "Spectracer")
        raw_follow_cursor = self._settings.value("playback/follow_cursor", True)
        if isinstance(raw_follow_cursor, bool):
            self._follow_cursor_enabled = raw_follow_cursor
        else:
            self._follow_cursor_enabled = str(raw_follow_cursor).strip().lower() in {"1", "true", "yes", "on"}

        self._normalization_mode = NormalizationMode.parse(
            self._settings.value(
                "render/normalization_mode",
                NormalizationMode.DB_PERCENTILE.value,
            )
        )
        raw_ref_percentile = self._settings.value("render/normalization_ref_percentile", 99.5)
        try:
            self._normalization_ref_percentile = float(raw_ref_percentile)
        except (TypeError, ValueError):
            self._normalization_ref_percentile = 99.5
        self._colormap_stops: list[ColorStop] = self._load_colormap_stops()
        self._midi_settings = self._load_midi_user_settings()
        self._grid_settings = self._load_grid_user_settings()
        self._midi_editor_state = self._load_midi_editor_state()
        self._background_mix_gain = self._read_float_setting("mixer/background_gain", 0.85, minimum=0.0, maximum=1.0)
        self._midi_mix_gain = self._read_float_setting("mixer/midi_gain", DEFAULT_GAIN, minimum=0.0, maximum=1.0)
        self._grid_timeline = self._build_grid_timeline(self._grid_settings)

        self._suppress_view_state_persist = False
        self._playback_rate = 1.0

        self.playback_engine = PlaybackEngine(self)
        self.midi_synth: MidiSynth = create_default_midi_synth(
            output_name=self._midi_settings.output_name,
            soundfont_path=self._midi_settings.soundfont_path,
            channel=self._midi_settings.channel,
            program=self._midi_settings.program,
        )
        self._midi_session = MidiSession()
        self._midi_playback_controller = MidiPlaybackController(
            self.playback_engine,
            self.midi_synth,
            session=self._midi_session,
            timeline=self._grid_timeline,
            parent=self,
        )
        self._midi_preview_release_timers: dict[int, QTimer] = {}
        self._midi_preview_restart_timers: dict[int, QTimer] = {}
        self._midi_preview_active_notes: set[int] = set()
        self._midi_warning_shown = False
        self._sync_midi_backend_status()

        self._build_ui()
        self.spectrogram_view.set_midi_session(self._midi_session)
        self._midi_editor_controller = MidiEditorController(
            self.spectrogram_view,
            session=self._midi_session,
            timeline=self._grid_timeline,
            menu_host=self,
            parent=self,
        )
        self._apply_midi_editor_state(self._midi_editor_state, persist=False)
        self._set_background_mix_gain(self._background_mix_gain, persist=False)
        self._set_midi_mix_gain(self._midi_mix_gain, persist=False)
        self._update_midi_status_display()
        self._apply_event_track_visibility()
        self._connect_signals()

        self.spectrogram_view.set_colormap_stops(self._colormap_stops)
        self.spectrogram_view.set_normalization_settings(
            mode=self._normalization_mode,
            ref_percentile=self._normalization_ref_percentile,
        )

        self._apply_grid_settings_to_view()
        self._set_display_controls(self._display_sensitivity, self._display_contrast, apply_to_view=False)
        self._set_analysis_busy(False)

        if self._runtime_config_error is not None:
            self.status_message.setText(f"配置加载失败，已回退默认值：{self._runtime_config_error}")

        if initial_audio_path is not None:
            path = Path(initial_audio_path).expanduser().resolve()
            QTimer.singleShot(0, lambda: self.open_audio(path))

    def _build_ui(self) -> None:
        self.main_toolbar = QToolBar("Main")
        self.main_toolbar.setMovable(False)
        self.addToolBar(self.main_toolbar)

        self.open_action = QAction("打开音频", self)
        self.main_toolbar.addAction(self.open_action)
        self.main_toolbar.addSeparator()

        self.undo_action = QAction("撤销", self)
        self.undo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Undo))
        self.redo_action = QAction("重做", self)
        redo_shortcuts = list(QKeySequence.keyBindings(QKeySequence.StandardKey.Redo))
        shift_redo_shortcut = QKeySequence("Ctrl+Shift+Z")
        if shift_redo_shortcut not in redo_shortcuts:
            redo_shortcuts.append(shift_redo_shortcut)
        self.redo_action.setShortcuts(redo_shortcuts)
        self.main_toolbar.addAction(self.undo_action)
        self.main_toolbar.addAction(self.redo_action)
        self.main_toolbar.addSeparator()

        self.zoom_reset_action = QAction("重置视图", self)
        self.zoom_x_in_action = QAction("横向放大", self)
        self.zoom_x_out_action = QAction("横向缩小", self)
        self.zoom_y_in_action = QAction("纵向放大", self)
        self.zoom_y_out_action = QAction("纵向缩小", self)
        self.follow_cursor_action = QAction("跟随游标", self)
        self.follow_cursor_action.setCheckable(True)
        self.follow_cursor_action.setChecked(self._follow_cursor_enabled)
        self.locate_cursor_action = QAction("定位游标", self)
        self.main_toolbar.addAction(self.zoom_reset_action)
        self.main_toolbar.addAction(self.zoom_x_in_action)
        self.main_toolbar.addAction(self.zoom_x_out_action)
        self.main_toolbar.addAction(self.zoom_y_in_action)
        self.main_toolbar.addAction(self.zoom_y_out_action)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.follow_cursor_action)
        self.main_toolbar.addAction(self.locate_cursor_action)
        self.main_toolbar.addSeparator()

        self.normalization_toolbar_label = QLabel("归一化")
        self.normalization_combo = QComboBox(self)
        self.normalization_combo.addItem("最大值 (dB)", NormalizationMode.DB_MAX.value)
        self.normalization_combo.addItem("百分位 (dB, 推荐)", NormalizationMode.DB_PERCENTILE.value)
        self.normalization_combo.setMinimumWidth(150)
        self.normalization_combo.setCurrentIndex(
            max(0, self.normalization_combo.findData(self._normalization_mode.value))
        )

        self.colormap_action = QAction("色盘...", self)
        self.export_midi_action = QAction("导出 MIDI...", self)
        self.midi_settings_action = QAction("MIDI...", self)
        self.grid_settings_action = QAction("网格...", self)
        self.grid_toggle_action = QAction("显示网格", self)
        self.grid_toggle_action.setCheckable(True)
        self.grid_toggle_action.setChecked(self._grid_settings.visible)
        self.event_track_toggle_action = QAction("事件轨道", self)
        self.event_track_toggle_action.setCheckable(True)
        self.event_track_toggle_action.setChecked(self._grid_settings.event_track_visible)

        self.main_toolbar.addWidget(self.normalization_toolbar_label)
        self.main_toolbar.addWidget(self.normalization_combo)
        self.main_toolbar.addAction(self.colormap_action)
        self.main_toolbar.addAction(self.export_midi_action)
        self.main_toolbar.addAction(self.midi_settings_action)
        self.main_toolbar.addAction(self.grid_settings_action)
        self.main_toolbar.addAction(self.grid_toggle_action)
        self.main_toolbar.addAction(self.event_track_toggle_action)
        self.main_toolbar.addSeparator()

        self.sensitivity_toolbar_label = QLabel("灵敏度")
        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(DISPLAY_SLIDER_MIN, DISPLAY_SLIDER_MAX)
        self.sensitivity_slider.setFixedWidth(150)
        self.sensitivity_value_label = QLabel("1.00")

        self.contrast_toolbar_label = QLabel("对比度")
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(DISPLAY_SLIDER_MIN, DISPLAY_SLIDER_MAX)
        self.contrast_slider.setFixedWidth(150)
        self.contrast_value_label = QLabel("1.00")

        self.main_toolbar.addWidget(self.sensitivity_toolbar_label)
        self.main_toolbar.addWidget(self.sensitivity_slider)
        self.main_toolbar.addWidget(self.sensitivity_value_label)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addWidget(self.contrast_toolbar_label)
        self.main_toolbar.addWidget(self.contrast_slider)
        self.main_toolbar.addWidget(self.contrast_value_label)

        self.space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.space_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)

        self.edit_mode_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        self.edit_mode_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.place_tool_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self.place_tool_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.select_tool_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.select_tool_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.erase_tool_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self.erase_tool_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.copy_notes_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        self.copy_notes_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.paste_notes_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        self.paste_notes_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)

        self.central = QWidget(self)
        self.setCentralWidget(self.central)

        root_layout = QVBoxLayout(self.central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        top_info_layout = QHBoxLayout()
        self.file_label = QLabel("未加载音频")
        self.info_label = QLabel("悬停热图查看音高 / 频率")
        self.channel_mode_label = QLabel("声道模式:")
        self.channel_mode_combo = QComboBox()
        self.channel_mode_combo.setMinimumWidth(110)
        self.channel_mode_combo.setEnabled(False)
        self.harmonics_checkbox = QCheckBox("高亮倍音")
        self.harmonics_checkbox.setChecked(True)
        self.harmonics_count_spinbox = QSpinBox()
        self.harmonics_count_spinbox.setRange(2, 12)
        self.harmonics_count_spinbox.setValue(6)
        self.harmonics_count_spinbox.setSuffix(" 个")

        top_info_layout.addWidget(self.file_label, stretch=2)
        top_info_layout.addWidget(self.info_label, stretch=3)
        top_info_layout.addWidget(self.channel_mode_label)
        top_info_layout.addWidget(self.channel_mode_combo)
        top_info_layout.addWidget(self.harmonics_checkbox)
        top_info_layout.addWidget(self.harmonics_count_spinbox)
        root_layout.addLayout(top_info_layout)

        self.editor_controls_row = QWidget(self)
        editor_controls_layout = QHBoxLayout(self.editor_controls_row)
        editor_controls_layout.setContentsMargins(0, 0, 0, 0)
        editor_controls_layout.setSpacing(8)
        self.edit_mode_checkbox = QCheckBox("编辑模式")
        self.edit_mode_checkbox.setToolTip("开启后显示 MIDI 覆盖层，并按设定暗化背景热图。快捷键：Ctrl+E。")
        self.editor_tool_label = QLabel("工具")
        self.editor_tool_group = QButtonGroup(self)
        self.editor_tool_group.setExclusive(True)

        self.place_tool_button = QPushButton("放置")
        self.select_tool_button = QPushButton("选择")
        self.erase_tool_button = QPushButton("擦除")
        self.place_tool_button.setToolTip("放置工具 (Ctrl+W)")
        self.select_tool_button.setToolTip("选择工具 (Ctrl+S)")
        self.erase_tool_button.setToolTip("擦除工具 (Ctrl+D)")
        editor_controls_layout.addWidget(self.edit_mode_checkbox)
        editor_controls_layout.addWidget(self.editor_tool_label)
        for button, tool in (
            (self.place_tool_button, MidiEditorTool.PLACE),
            (self.select_tool_button, MidiEditorTool.SELECT),
            (self.erase_tool_button, MidiEditorTool.ERASE),
        ):
            button.setCheckable(True)
            button.setProperty("midiTool", tool.value)
            self.editor_tool_group.addButton(button)
            editor_controls_layout.addWidget(button)
        self.editor_snap_checkbox = QCheckBox("吸附")
        self.editor_snap_resolution_combo = QComboBox(self)
        self.editor_snap_resolution_combo.setMinimumWidth(84)
        for resolution in MidiSnapResolution.ordered():
            self.editor_snap_resolution_combo.addItem(resolution.display_name, resolution.value)
        self.editor_active_channel_label = QLabel("通道")
        self.editor_active_channel_combo = QComboBox(self)
        self.editor_active_channel_combo.setMinimumWidth(180)
        self.channel_config_button = QPushButton("配置…")
        self.channel_config_button.setToolTip("编辑当前通道的名称、音色、声像、颜色与静音/独奏状态。")
        self.editor_darken_label = QLabel("暗化")
        self.editor_darken_slider = QSlider(Qt.Orientation.Horizontal)
        self.editor_darken_slider.setRange(0, FRACTION_SLIDER_RANGE)
        self.editor_darken_slider.setFixedWidth(110)
        self.editor_darken_value_label = QLabel("35%")
        self.background_mix_label = QLabel("BG")
        self.background_mix_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_mix_slider.setRange(0, FRACTION_SLIDER_RANGE)
        self.background_mix_slider.setFixedWidth(96)
        self.background_mix_value_label = QLabel("85%")
        self.midi_mix_label = QLabel("MIDI")
        self.midi_mix_slider = QSlider(Qt.Orientation.Horizontal)
        self.midi_mix_slider.setRange(0, FRACTION_SLIDER_RANGE)
        self.midi_mix_slider.setFixedWidth(96)
        self.midi_mix_value_label = QLabel("60%")
        editor_controls_layout.addWidget(self.editor_snap_checkbox)
        editor_controls_layout.addWidget(self.editor_snap_resolution_combo)
        editor_controls_layout.addWidget(self.editor_active_channel_label)
        editor_controls_layout.addWidget(self.editor_active_channel_combo)
        editor_controls_layout.addWidget(self.channel_config_button)
        editor_controls_layout.addWidget(self.editor_darken_label)
        editor_controls_layout.addWidget(self.editor_darken_slider)
        editor_controls_layout.addWidget(self.editor_darken_value_label)
        editor_controls_layout.addStretch(1)
        editor_controls_layout.addWidget(self.background_mix_label)
        editor_controls_layout.addWidget(self.background_mix_slider)
        editor_controls_layout.addWidget(self.background_mix_value_label)
        editor_controls_layout.addWidget(self.midi_mix_label)
        editor_controls_layout.addWidget(self.midi_mix_slider)
        editor_controls_layout.addWidget(self.midi_mix_value_label)
        root_layout.addWidget(self.editor_controls_row)
        self._populate_editor_active_channel_combo()

        self.view_grid = QGridLayout()
        self.view_grid.setContentsMargins(0, 0, 0, 0)
        self.view_grid.setHorizontalSpacing(6)
        self.view_grid.setVerticalSpacing(6)

        self.piano_widget = PianoKeyboardWidget(self)
        self.event_track_labels = EventTrackLaneLabels(self)
        lane_label_width = self.piano_widget.minimumWidth()
        self.event_track_labels.setMinimumWidth(lane_label_width)
        self.event_track_labels.setMaximumWidth(lane_label_width)
        self.event_track_view = GridEventTrackWidget(self)
        self.event_track_labels.setVisible(self._grid_settings.event_track_visible)
        self.event_track_controls_row = QWidget(self)
        event_track_controls_layout = QHBoxLayout(self.event_track_controls_row)
        event_track_controls_layout.setContentsMargins(lane_label_width + self.view_grid.horizontalSpacing(), 0, 0, 0)
        event_track_controls_layout.setSpacing(8)
        event_track_controls_layout.addStretch(1)
        self.event_snap_checkbox = QCheckBox("事件吸附")
        self.event_snap_checkbox.setChecked(self._grid_settings.event_snap_enabled)
        self.event_snap_checkbox.setToolTip("开启后，新增与拖动 Tempo / Meter 事件会吸附到当前网格细分。")
        self.reset_events_button = QPushButton("重置事件…")
        self.reset_events_button.setToolTip("移除所有非起始 Tempo / Meter 事件，保留当前起始 BPM / 拍号。")
        event_track_controls_layout.addWidget(self.event_snap_checkbox)
        event_track_controls_layout.addWidget(self.reset_events_button)
        self.event_track_controls_row.setVisible(self._grid_settings.event_track_visible)
        self.event_track_view.setVisible(self._grid_settings.event_track_visible)
        self.spectrogram_view = SpectrogramView(self)
        self.horizontal_scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.vertical_scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self.horizontal_scrollbar.setRange(0, 0)
        self.vertical_scrollbar.setRange(0, 0)

        placeholder = QWidget(self)
        placeholder.setFixedWidth(lane_label_width)

        root_layout.addWidget(self.event_track_controls_row)
        self.view_grid.addWidget(self.event_track_labels, 0, 0)
        self.view_grid.addWidget(self.event_track_view, 0, 1)
        self.view_grid.addWidget(self.piano_widget, 1, 0)
        self.view_grid.addWidget(self.spectrogram_view, 1, 1)
        self.view_grid.addWidget(self.vertical_scrollbar, 1, 2)
        self.view_grid.addWidget(placeholder, 2, 0)
        self.view_grid.addWidget(self.horizontal_scrollbar, 2, 1)
        self.view_grid.setColumnStretch(1, 1)
        self.view_grid.setRowStretch(1, 1)
        self.view_grid.setRowMinimumHeight(0, self.event_track_view.minimumHeight() if self._grid_settings.event_track_visible else 0)
        root_layout.addLayout(self.view_grid, stretch=1)

        controls_layout = QHBoxLayout()
        self.open_button = QPushButton("打开")
        self.play_button = QPushButton("播放")

        self.playback_rate_label = QLabel("速度")
        self.playback_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_rate_slider.setRange(PLAYBACK_RATE_SLIDER_MIN, PLAYBACK_RATE_SLIDER_MAX)
        self.playback_rate_slider.setValue(PLAYBACK_RATE_SLIDER_DEFAULT)
        self.playback_rate_slider.setFixedWidth(120)
        self.playback_rate_value_label = QLabel("1.00x")
        self.playback_rate_reset_button = QPushButton("1x")
        self.playback_rate_reset_button.setFixedWidth(44)

        self.position_label = QLabel("00:00.000 / 00:00.000")
        self.transport_slider = QSlider(Qt.Orientation.Horizontal)
        self.transport_slider.setRange(0, 0)
        controls_layout.addWidget(self.open_button)
        controls_layout.addWidget(self.play_button)

        controls_layout.addWidget(self.playback_rate_label)
        controls_layout.addWidget(self.playback_rate_slider)
        controls_layout.addWidget(self.playback_rate_value_label)
        controls_layout.addWidget(self.playback_rate_reset_button)

        controls_layout.addWidget(self.position_label)
        controls_layout.addWidget(self.transport_slider, stretch=1)
        root_layout.addLayout(controls_layout)

        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_message = QLabel("就绪")
        self.midi_status_label = QLabel("")
        self.midi_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.midi_status_label.setToolTip("")
        self.statusBar().addPermanentWidget(self.status_message, 1)
        self.statusBar().addPermanentWidget(self.midi_status_label)

        self._sync_undo_redo_actions()

    def _connect_signals(self) -> None:
        self.open_action.triggered.connect(self.open_audio_dialog)
        self.undo_action.triggered.connect(self._undo_last_midi_edit)
        self.redo_action.triggered.connect(self._redo_last_midi_edit)
        self.open_button.clicked.connect(self.open_audio_dialog)
        self.play_button.clicked.connect(self.toggle_playback)
        self.space_shortcut.activated.connect(self.toggle_playback)
        self.edit_mode_shortcut.activated.connect(self._toggle_edit_mode_shortcut)
        self.place_tool_shortcut.activated.connect(lambda: self._set_editor_tool(MidiEditorTool.PLACE, persist=True))
        self.select_tool_shortcut.activated.connect(lambda: self._set_editor_tool(MidiEditorTool.SELECT, persist=True))
        self.erase_tool_shortcut.activated.connect(lambda: self._set_editor_tool(MidiEditorTool.ERASE, persist=True))
        self.copy_notes_shortcut.activated.connect(self._copy_selected_midi_notes)
        self.paste_notes_shortcut.activated.connect(self._paste_copied_midi_notes)

        self.playback_rate_slider.valueChanged.connect(self._on_playback_rate_slider_changed)
        self.playback_rate_reset_button.clicked.connect(self._reset_playback_rate)

        self.zoom_reset_action.triggered.connect(self.spectrogram_view.reset_view)
        self.zoom_x_in_action.triggered.connect(lambda: self.spectrogram_view.zoom_horizontal(0.8))
        self.zoom_x_out_action.triggered.connect(lambda: self.spectrogram_view.zoom_horizontal(1.25))
        self.zoom_y_in_action.triggered.connect(lambda: self.spectrogram_view.zoom_vertical(0.8))
        self.zoom_y_out_action.triggered.connect(lambda: self.spectrogram_view.zoom_vertical(1.25))

        self.follow_cursor_action.toggled.connect(self._on_follow_cursor_toggled)
        self.locate_cursor_action.triggered.connect(self._locate_cursor)

        self.normalization_combo.currentIndexChanged.connect(self._on_normalization_mode_changed)
        self.colormap_action.triggered.connect(self._open_colormap_editor)
        self.export_midi_action.triggered.connect(self._open_export_midi_dialog)
        self.midi_settings_action.triggered.connect(self._open_midi_settings_dialog)
        self.grid_settings_action.triggered.connect(self._open_grid_settings_dialog)
        self.grid_toggle_action.toggled.connect(self._on_grid_visibility_toggled)
        self.event_track_toggle_action.toggled.connect(self._on_event_track_visibility_toggled)
        self.event_snap_checkbox.toggled.connect(self._on_event_snap_toggled)
        self.edit_mode_checkbox.toggled.connect(self._on_edit_mode_toggled)
        self.editor_tool_group.buttonClicked.connect(self._on_editor_tool_button_clicked)
        self.editor_snap_checkbox.toggled.connect(self._on_editor_snap_toggled)
        self.editor_snap_resolution_combo.currentIndexChanged.connect(self._on_editor_snap_resolution_changed)
        self.editor_active_channel_combo.currentIndexChanged.connect(self._on_editor_active_channel_changed)
        self.channel_config_button.clicked.connect(self._open_active_channel_config_dialog)
        self.editor_darken_slider.valueChanged.connect(self._on_editor_darken_slider_changed)
        self.background_mix_slider.valueChanged.connect(self._on_background_mix_slider_changed)
        self.midi_mix_slider.valueChanged.connect(self._on_midi_mix_slider_changed)
        self.reset_events_button.clicked.connect(self._on_reset_events_requested)
        self.event_track_view.create_requested.connect(self._on_event_track_create_requested)
        self.event_track_view.move_requested.connect(self._on_event_track_move_requested)
        self.event_track_view.edit_requested.connect(self._on_event_track_edit_requested)
        self.event_track_view.delete_requested.connect(self._on_event_track_delete_requested)

        self.sensitivity_slider.valueChanged.connect(self._on_display_slider_changed)
        self.contrast_slider.valueChanged.connect(self._on_display_slider_changed)

        self.transport_slider.sliderPressed.connect(self._on_slider_pressed)
        self.transport_slider.sliderReleased.connect(self._on_slider_released)
        self.transport_slider.sliderMoved.connect(self._on_slider_moved)

        self.channel_mode_combo.currentIndexChanged.connect(self._on_channel_mode_changed)
        self.harmonics_checkbox.toggled.connect(self._on_harmonics_setting_changed)
        self.harmonics_count_spinbox.valueChanged.connect(self._on_harmonics_setting_changed)

        self.playback_engine.position_changed.connect(self._on_playback_position_changed)
        self.playback_engine.duration_changed.connect(self._on_playback_duration_changed)
        self.playback_engine.playback_state_changed.connect(self._on_playback_state_changed)

        self.spectrogram_view.hover_changed.connect(self._on_hover_changed)
        self.spectrogram_view.seek_requested.connect(self._seek_to_seconds)
        self.spectrogram_view.note_audition_requested.connect(self._on_spectrogram_note_audition_requested)
        self.spectrogram_view.view_state_changed.connect(self._on_view_state_changed)
        self.piano_widget.note_triggered.connect(self._on_piano_note_triggered)

        self.horizontal_scrollbar.valueChanged.connect(self._on_horizontal_scrollbar_changed)
        self.vertical_scrollbar.valueChanged.connect(self._on_vertical_scrollbar_changed)

        self._midi_session.editor_state_changed.connect(self._on_midi_session_editor_state_changed)
        self._midi_session.channel_configs_changed.connect(self._on_midi_session_channel_configs_changed)
        self._midi_session.command_stack_changed.connect(self._on_midi_session_command_stack_changed)

    def open_audio_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            str(Path.cwd()),
            "Audio Files (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma);;All Files (*)",
        )
        if file_path:
            self.open_audio(file_path)

    def open_audio(self, path: str | Path) -> None:
        audio_path = Path(path).expanduser().resolve()
        if not audio_path.exists():
            self._show_error(f"音频文件不存在：{audio_path}")
            return

        selected_options = AnalysisOptionsDialog(
            audio_path=audio_path,
            initial_config=self._session_config,
            initial_channel_modes=self._session_channel_modes,
            config_path=self._runtime_config_path,
            parent=self,
        )
        if selected_options.exec() != selected_options.DialogCode.Accepted:
            return

        selected_config = selected_options.selected_config()
        selected_channel_modes = selected_options.selected_channel_modes()
        self._session_config = selected_config
        self._session_channel_modes = selected_channel_modes
        self._start_analysis(audio_path, selected_config, selected_channel_modes)

    def toggle_playback(self) -> None:
        if self._analysis_busy and not self._analysis_primary_ready:
            return
        self.playback_engine.toggle()

    def _set_playback_rate(self, rate: float, *, update_slider: bool = True) -> None:
        clamped = max(0.5, min(2.0, float(rate)))
        self._playback_rate = clamped
        self.playback_engine.set_playback_rate(clamped)
        self.playback_rate_value_label.setText(f"{clamped:.2f}x")
        if update_slider:
            self.playback_rate_slider.blockSignals(True)
            self.playback_rate_slider.setValue(int(round(clamped * 100.0)))
            self.playback_rate_slider.blockSignals(False)

    def _on_playback_rate_slider_changed(self, value: int) -> None:
        self._set_playback_rate(value / 100.0, update_slider=False)

    def _reset_playback_rate(self) -> None:
        self._set_playback_rate(1.0)

    def _on_follow_cursor_toggled(self, enabled: bool) -> None:
        self._follow_cursor_enabled = bool(enabled)
        self._settings.setValue("playback/follow_cursor", self._follow_cursor_enabled)
        if self._follow_cursor_enabled:
            self._locate_cursor()

    def _locate_cursor(self) -> None:
        if self._current_result is None:
            return
        self.spectrogram_view.center_on_time(self.playback_engine.state.position_seconds)

    def _follow_cursor_to(self, seconds: float) -> None:
        if not self._follow_cursor_enabled:
            return
        self.spectrogram_view.ensure_time_visible(seconds, anchor_ratio=0.25, margin_ratio=0.08)

    def _on_normalization_mode_changed(self, index: int) -> None:
        data = self.normalization_combo.itemData(index)
        if data is None:
            return
        self._normalization_mode = NormalizationMode.parse(data)
        self._settings.setValue("render/normalization_mode", self._normalization_mode.value)
        self.spectrogram_view.set_normalization_settings(
            mode=self._normalization_mode,
            ref_percentile=self._normalization_ref_percentile,
        )

    def _open_colormap_editor(self) -> None:
        stops = list(self._colormap_stops)
        dialog = ColormapEditorDialog(initial_stops=stops, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._colormap_stops = dialog.stops()
        self.spectrogram_view.set_colormap_stops(self._colormap_stops)
        self._save_colormap_stops(self._colormap_stops)
        self.status_message.setText("已更新热图色盘")

    def _open_export_midi_dialog(self) -> None:
        suggested_path = self._suggest_midi_export_path()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 MIDI",
            str(suggested_path),
            "MIDI Files (*.mid);;All Files (*)",
        )
        if not file_path:
            return

        try:
            exported_path = export_notes_to_midi(
                file_path,
                notes=self._midi_session.notes,
                channel_configs=self._midi_session.channel_configs,
                timeline=self._grid_timeline,
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"MIDI 导出失败：{exc}")
            return

        self.status_message.setText(f"已导出 MIDI：{exported_path}")

    def _suggest_midi_export_path(self) -> Path:
        if self._current_audio_path is not None:
            return Path(self._current_audio_path).with_suffix(".mid")
        return Path.cwd() / "spectracer_export.mid"

    def _open_midi_settings_dialog(self) -> None:
        dialog = MidiSettingsDialog(
            initial_output_name=self._midi_settings.output_name,
            initial_soundfont_path=self._midi_settings.soundfont_path,
            initial_program=self._midi_settings.program,
            initial_channel=self._midi_settings.channel,
            current_status_message=self._build_midi_status_summary(),
            parent=self,
        )
        dialog_result = dialog.exec()
        if dialog_result != dialog.DialogCode.Accepted:
            return
        self._midi_settings = dialog.selected_settings()
        self._save_midi_user_settings(self._midi_settings)
        self._recreate_midi_synth(show_feedback=True)

    def _load_midi_user_settings(self) -> MidiUserSettings:
        raw_output_name = self._settings.value("midi/output_name", None)
        output_name = str(raw_output_name).strip() if raw_output_name is not None else None
        raw_soundfont_path = self._settings.value("midi/soundfont_path", None)
        soundfont_path = str(raw_soundfont_path).strip() if raw_soundfont_path is not None else None
        raw_program = self._settings.value("midi/program", 0)
        raw_channel = self._settings.value("midi/channel", 0)
        try:
            program = max(0, min(127, int(raw_program)))
        except (TypeError, ValueError):
            program = 0
        try:
            channel = max(0, min(15, int(raw_channel)))
        except (TypeError, ValueError):
            channel = 0
        return MidiUserSettings(
            output_name=output_name or None,
            soundfont_path=soundfont_path or None,
            program=program,
            channel=channel,
        )

    def _save_midi_user_settings(self, settings: MidiUserSettings) -> None:
        self._settings.setValue("midi/output_name", settings.output_name or "")
        self._settings.setValue("midi/soundfont_path", settings.soundfont_path or "")
        self._settings.setValue("midi/program", int(settings.program))
        self._settings.setValue("midi/channel", int(settings.channel))

    def _load_midi_editor_state(self) -> MidiEditorState:
        return MidiEditorState(
            enabled=self._read_bool_setting("midi_editor/enabled", False),
            tool=self._settings.value("midi_editor/tool", MidiEditorTool.SELECT.value),
            active_channel=self._read_int_setting("midi_editor/active_channel", 0, minimum=0, maximum=15),
            snap_enabled=self._read_bool_setting("midi_editor/snap_enabled", True),
            snap_resolution=self._settings.value("midi_editor/snap_resolution", MidiSnapResolution.SIXTEENTH.value),
            darken_amount=self._read_float_setting("midi_editor/darken_amount", 0.35, minimum=0.0, maximum=1.0),
        )

    def _save_midi_editor_state(self, editor_state: MidiEditorState) -> None:
        self._settings.setValue("midi_editor/enabled", bool(editor_state.enabled))
        self._settings.setValue("midi_editor/tool", editor_state.tool.value)
        self._settings.setValue("midi_editor/active_channel", int(editor_state.active_channel))
        self._settings.setValue("midi_editor/snap_enabled", bool(editor_state.snap_enabled))
        self._settings.setValue("midi_editor/snap_resolution", editor_state.snap_resolution.value)
        self._settings.setValue("midi_editor/darken_amount", float(editor_state.darken_amount))

    def _populate_editor_active_channel_combo(self) -> None:
        if not hasattr(self, "editor_active_channel_combo"):
            return
        current_channel = self._midi_session.editor_state.active_channel
        self.editor_active_channel_combo.blockSignals(True)
        self.editor_active_channel_combo.clear()
        for config in self._midi_session.channel_configs:
            tags: list[str] = []
            if config.is_drum:
                tags.append("打击乐")
            if config.solo:
                tags.append("独奏")
            if config.muted:
                tags.append("静音")
            suffix = f"（{' / '.join(tags)}）" if tags else ""
            label = f"{config.channel + 1:02d} · {config.display_name}{suffix}"
            self.editor_active_channel_combo.addItem(label, config.channel)
        index = max(0, self.editor_active_channel_combo.findData(current_channel))
        self.editor_active_channel_combo.setCurrentIndex(index)
        self.editor_active_channel_combo.blockSignals(False)

    def _open_active_channel_config_dialog(self) -> None:
        combo_data = self.editor_active_channel_combo.currentData()
        active_channel = self._midi_session.editor_state.active_channel if combo_data is None else int(combo_data)
        dialog_result = ChannelConfigDialog.get_config(
            initial_config=self._midi_session.get_channel_config(active_channel),
            parent=self,
        )
        if dialog_result is None:
            return
        updated_config = self._midi_session.set_channel_config(dialog_result)
        program_text = self._format_midi_program_text(updated_config.program, updated_config.channel)
        state_tags = []
        if updated_config.solo:
            state_tags.append("独奏")
        if updated_config.muted:
            state_tags.append("静音")
        state_text = " | " + " / ".join(state_tags) if state_tags else ""
        self.status_message.setText(
            f"已更新通道 {updated_config.channel + 1:02d}：{updated_config.display_name} | {program_text} | Pan {updated_config.pan}{state_text}"
        )

    def _apply_midi_editor_state(self, editor_state: MidiEditorState, *, persist: bool) -> None:
        self._midi_editor_state = self._midi_session.set_editor_state(editor_state)
        self.spectrogram_view.set_midi_editor_state(self._midi_editor_state)
        self._sync_midi_editor_controls()
        if persist:
            self._save_midi_editor_state(self._midi_editor_state)

    def _on_midi_session_editor_state_changed(self, editor_state: object) -> None:
        if not isinstance(editor_state, MidiEditorState):
            return
        self._midi_editor_state = editor_state
        self._sync_midi_editor_controls()

    def _on_midi_session_channel_configs_changed(self, _channel_configs: object) -> None:
        self._populate_editor_active_channel_combo()

    def _on_midi_session_command_stack_changed(self, _state: object) -> None:
        self._sync_undo_redo_actions()

    def _undo_last_midi_edit(self) -> None:
        command = self._midi_session.undo()
        if command is None:
            return
        self.status_message.setText(f"已撤销：{command.summary}")

    def _redo_last_midi_edit(self) -> None:
        command = self._midi_session.redo()
        if command is None:
            return
        self.status_message.setText(f"已重做：{command.summary}")

    def _sync_undo_redo_actions(self) -> None:
        state = self._midi_session.command_stack_state
        self.undo_action.setEnabled(state.can_undo)
        self.undo_action.setToolTip(f"撤销：{state.undo_text}" if state.undo_text else "撤销")
        self.redo_action.setEnabled(state.can_redo)
        self.redo_action.setToolTip(f"重做：{state.redo_text}" if state.redo_text else "重做")

    def _sync_midi_editor_controls(self) -> None:
        state = self._midi_session.editor_state
        self._populate_editor_active_channel_combo()
        self.edit_mode_checkbox.blockSignals(True)
        self.edit_mode_checkbox.setChecked(state.enabled)
        self.edit_mode_checkbox.blockSignals(False)
        for button, tool in (
            (self.place_tool_button, MidiEditorTool.PLACE),
            (self.select_tool_button, MidiEditorTool.SELECT),
            (self.erase_tool_button, MidiEditorTool.ERASE),
        ):
            button.blockSignals(True)
            button.setChecked(state.tool is tool)
            button.blockSignals(False)
        self.editor_snap_checkbox.blockSignals(True)
        self.editor_snap_checkbox.setChecked(state.snap_enabled)
        self.editor_snap_checkbox.blockSignals(False)
        snap_index = max(0, self.editor_snap_resolution_combo.findData(state.snap_resolution.value))
        self.editor_snap_resolution_combo.blockSignals(True)
        self.editor_snap_resolution_combo.setCurrentIndex(snap_index)
        self.editor_snap_resolution_combo.blockSignals(False)
        self.editor_snap_resolution_combo.setEnabled(state.snap_enabled)
        channel_index = max(0, self.editor_active_channel_combo.findData(state.active_channel))
        self.editor_active_channel_combo.blockSignals(True)
        self.editor_active_channel_combo.setCurrentIndex(channel_index)
        self.editor_active_channel_combo.blockSignals(False)
        self.editor_darken_slider.blockSignals(True)
        self.editor_darken_slider.setValue(self._fraction_to_slider_value(state.darken_amount))
        self.editor_darken_slider.blockSignals(False)
        self.editor_darken_value_label.setText(self._format_percent_label(state.darken_amount))

    def _set_background_mix_gain(self, value: float, *, persist: bool) -> None:
        self.playback_engine.set_volume(max(0.0, min(1.0, float(value))))
        self._sync_mix_controls()
        if persist:
            self._settings.setValue("mixer/background_gain", float(self.playback_engine.state.volume))

    def _set_midi_mix_gain(self, value: float, *, persist: bool) -> None:
        self._midi_playback_controller.set_midi_gain(max(0.0, min(1.0, float(value))))
        self._sync_mix_controls()
        if persist:
            self._settings.setValue("mixer/midi_gain", float(self._midi_playback_controller.midi_gain))

    def _sync_mix_controls(self) -> None:
        self.background_mix_slider.blockSignals(True)
        self.background_mix_slider.setValue(self._fraction_to_slider_value(self.playback_engine.state.volume))
        self.background_mix_slider.blockSignals(False)
        self.background_mix_value_label.setText(self._format_percent_label(self.playback_engine.state.volume))
        self.midi_mix_slider.blockSignals(True)
        self.midi_mix_slider.setValue(self._fraction_to_slider_value(self._midi_playback_controller.midi_gain))
        self.midi_mix_slider.blockSignals(False)
        self.midi_mix_value_label.setText(self._format_percent_label(self._midi_playback_controller.midi_gain))

    def _open_grid_settings_dialog(self) -> None:
        dialog = GridSettingsDialog(
            initial_bpm=self._grid_settings.bpm,
            initial_numerator=self._grid_settings.numerator,
            initial_denominator=self._grid_settings.denominator,
            initial_offset_ms=self._grid_settings.offset_ms,
            initial_subdivisions_per_beat=self._grid_settings.subdivisions_per_beat,
            parent=self,
        )
        dialog_result = dialog.exec()
        if dialog_result != dialog.DialogCode.Accepted:
            return

        selected = dialog.selected_settings()
        self._grid_settings.bpm = float(selected.bpm)
        self._grid_settings.numerator = int(selected.numerator)
        self._grid_settings.denominator = int(selected.denominator)
        self._grid_settings.offset_ms = float(selected.offset_ms)
        self._grid_settings.subdivisions_per_beat = int(selected.subdivisions_per_beat)

        first_tempo_transition = self._grid_settings.tempo_events[0].transition if self._grid_settings.tempo_events else TempoTransition.STEP
        remaining_tempo_events = self._grid_settings.tempo_events[1:] if len(self._grid_settings.tempo_events) > 1 else ()
        self._grid_settings.tempo_events = (TempoEvent(0.0, self._grid_settings.bpm, first_tempo_transition), *remaining_tempo_events)

        remaining_signature_events = self._grid_settings.time_signature_events[1:] if len(self._grid_settings.time_signature_events) > 1 else ()
        root_signature = TimeSignature(self._grid_settings.numerator, self._grid_settings.denominator)
        self._grid_settings.time_signature_events = (TimeSignatureEvent(0.0, root_signature), *remaining_signature_events)
        self._persist_grid_settings()

    def _on_grid_visibility_toggled(self, enabled: bool) -> None:
        self._grid_settings.visible = bool(enabled)
        self._persist_grid_settings()

    def _on_event_snap_toggled(self, enabled: bool) -> None:
        self._grid_settings.event_snap_enabled = bool(enabled)
        self._persist_grid_settings(selection=self.event_track_view.selected_event())

    def _on_event_track_visibility_toggled(self, enabled: bool) -> None:
        self._grid_settings.event_track_visible = bool(enabled)
        self._persist_grid_settings(selection=self.event_track_view.selected_event())

    def _apply_event_track_visibility(self) -> None:
        visible = self._grid_settings.event_track_visible
        self.event_track_labels.setVisible(visible)
        self.event_track_controls_row.setVisible(visible)
        self.event_track_view.setVisible(visible)
        self.view_grid.setRowMinimumHeight(0, self.event_track_view.minimumHeight() if visible else 0)

    def _has_resettable_grid_events(self) -> bool:
        return len(self._grid_settings.tempo_events) > 1 or len(self._grid_settings.time_signature_events) > 1

    def _sync_event_track_controls(self) -> None:
        custom_tempo_events = len(self._grid_settings.tempo_events) > 1
        custom_meter_events = len(self._grid_settings.time_signature_events) > 1
        has_resettable_events = custom_tempo_events or custom_meter_events
        self.reset_events_button.setEnabled(has_resettable_events)
        if has_resettable_events:
            self.reset_events_button.setText("重置事件…")
        else:
            self.reset_events_button.setText("无可重置事件")

    def _on_reset_events_requested(self) -> None:
        custom_tempo_events = len(self._grid_settings.tempo_events) > 1
        custom_meter_events = len(self._grid_settings.time_signature_events) > 1
        if not (custom_tempo_events or custom_meter_events):
            return

        dialog = QMessageBox(self)
        dialog.setWindowTitle("重置事件")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("将移除所有非起始 Tempo / Meter 事件，保留当前起始 BPM 与拍号。")
        dialog.setInformativeText("此操作不会更改当前偏移量和显示设置。")
        dialog.setStandardButtons(QMessageBox.StandardButton.Cancel)
        reset_button = dialog.addButton("重置", QMessageBox.ButtonRole.AcceptRole)
        reset_button.setDefault(True)
        dialog.exec()
        if dialog.clickedButton() is not reset_button:
            return

        self._update_grid_event_sequences(
            tempo_events=_reset_tempo_events(self._grid_settings.tempo_events),
            time_signature_events=_reset_time_signature_events(self._grid_settings.time_signature_events),
            selection=EventTrackSelection(EventTrackLane.TEMPO, 0),
        )

    def _on_event_track_create_requested(self, lane_raw: object, beat_position: object) -> None:
        try:
            lane = EventTrackLane.parse(str(lane_raw))
            beat = float(beat_position)
        except (TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return

        try:
            active_event = self.event_track_view.active_event_at_beat(lane, beat)
            if lane is EventTrackLane.TEMPO:
                dialog_result = TempoEventDialog.get_event(
                    self,
                    beat_position=beat,
                    existing_event=active_event,
                )
                if dialog_result is None:
                    return
                new_event = TempoEvent(
                    beat_position=float(dialog_result.beat_position),
                    bpm=float(dialog_result.bpm),
                    transition=TempoTransition.parse(dialog_result.transition),
                )
                tempo_events = self._grid_timeline.with_added_tempo_event(new_event).tempo_events
                self._update_grid_event_sequences(
                    tempo_events=tuple(tempo_events),
                    selection=self._selection_for_tempo_event(new_event),
                )
                return

            active_signature = active_event.time_signature if active_event is not None else None
            dialog_result = TimeSignatureEventDialog.get_event(
                self,
                beat_position=beat,
                existing_signature=active_signature,
            )
            if dialog_result is None:
                return
            new_event = TimeSignatureEvent(
                beat_position=float(dialog_result.beat_position),
                time_signature=TimeSignature(int(dialog_result.numerator), int(dialog_result.denominator)),
            )
            time_signature_events = self._grid_timeline.with_added_time_signature_event(new_event).time_signature_events
            self._update_grid_event_sequences(
                time_signature_events=tuple(time_signature_events),
                selection=self._selection_for_time_signature_event(new_event),
            )
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_event_track_move_requested(self, payload: object, beat_position: object) -> None:
        selection = self._coerce_event_track_selection(payload)
        if selection is None:
            return
        try:
            target_beat = float(beat_position)
            if selection.lane == EventTrackLane.TEMPO:
                event = self._tempo_event_for_selection(selection)
                if event is None:
                    return
                updated_event = TempoEvent(target_beat, event.bpm, event.transition)
                tempo_events = self._grid_timeline.with_moved_tempo_event(selection.event_index, updated_event).tempo_events
                self._update_grid_event_sequences(
                    tempo_events=tuple(tempo_events),
                    selection=EventTrackSelection(EventTrackLane.TEMPO, selection.event_index),
                )
                return
            event = self._time_signature_event_for_selection(selection)
            if event is None:
                return
            updated_event = TimeSignatureEvent(target_beat, event.time_signature)
            time_signature_events = self._grid_timeline.with_moved_time_signature_event(
                selection.event_index,
                updated_event,
            ).time_signature_events
            self._update_grid_event_sequences(
                time_signature_events=tuple(time_signature_events),
                selection=EventTrackSelection(EventTrackLane.METER, selection.event_index),
            )
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_event_track_edit_requested(self, payload: object) -> None:
        selection = self._coerce_event_track_selection(payload)
        if selection is None:
            return
        try:
            if selection.lane == EventTrackLane.TEMPO:
                event = self._tempo_event_for_selection(selection)
                if event is None:
                    return
                dialog_result = TempoEventDialog.get_event(
                    self,
                    beat_position=event.beat_position,
                    existing_event=event,
                )
                if dialog_result is None:
                    return
                updated_event = TempoEvent(
                    beat_position=float(dialog_result.beat_position),
                    bpm=float(dialog_result.bpm),
                    transition=TempoTransition.parse(dialog_result.transition),
                )
                tempo_events = self._grid_timeline.with_replaced_tempo_event(selection.event_index, updated_event).tempo_events
                self._update_grid_event_sequences(
                    tempo_events=tuple(tempo_events),
                    selection=self._selection_for_tempo_event(updated_event),
                )
                return

            event = self._time_signature_event_for_selection(selection)
            if event is None:
                return
            dialog_result = TimeSignatureEventDialog.get_event(
                self,
                beat_position=event.beat_position,
                existing_signature=event.time_signature,
            )
            if dialog_result is None:
                return
            updated_event = TimeSignatureEvent(
                beat_position=float(dialog_result.beat_position),
                time_signature=TimeSignature(int(dialog_result.numerator), int(dialog_result.denominator)),
            )
            time_signature_events = self._grid_timeline.with_replaced_time_signature_event(
                selection.event_index,
                updated_event,
            ).time_signature_events
            self._update_grid_event_sequences(
                time_signature_events=tuple(time_signature_events),
                selection=self._selection_for_time_signature_event(updated_event),
            )
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_event_track_delete_requested(self, payload: object) -> None:
        selection = self._coerce_event_track_selection(payload)
        if selection is None or selection.is_root_event:
            return
        try:
            if selection.lane == EventTrackLane.TEMPO:
                tempo_events = self._grid_timeline.with_deleted_tempo_event(selection.event_index).tempo_events
                self._update_grid_event_sequences(
                    tempo_events=tuple(tempo_events),
                    selection=EventTrackSelection(EventTrackLane.TEMPO, max(0, selection.event_index - 1)),
                )
                return
            time_signature_events = self._grid_timeline.with_deleted_time_signature_event(
                selection.event_index,
            ).time_signature_events
            self._update_grid_event_sequences(
                time_signature_events=tuple(time_signature_events),
                selection=EventTrackSelection(EventTrackLane.METER, max(0, selection.event_index - 1)),
            )
        except ValueError as exc:
            self._show_error(str(exc))

    def _coerce_event_track_selection(self, payload: object) -> EventTrackSelection | None:
        return payload if isinstance(payload, EventTrackSelection) else None

    def _tempo_event_for_selection(self, selection: EventTrackSelection) -> TempoEvent | None:
        if selection.lane != EventTrackLane.TEMPO:
            return None
        if selection.event_index >= len(self._grid_settings.tempo_events):
            return None
        return self._grid_settings.tempo_events[selection.event_index]

    def _time_signature_event_for_selection(self, selection: EventTrackSelection) -> TimeSignatureEvent | None:
        if selection.lane != EventTrackLane.METER:
            return None
        if selection.event_index >= len(self._grid_settings.time_signature_events):
            return None
        return self._grid_settings.time_signature_events[selection.event_index]

    def _selection_for_tempo_event(self, event: TempoEvent) -> EventTrackSelection | None:
        for index, candidate in enumerate(self._grid_settings.tempo_events):
            if candidate == event:
                return EventTrackSelection(EventTrackLane.TEMPO, index)
        return None

    def _selection_for_time_signature_event(self, event: TimeSignatureEvent) -> EventTrackSelection | None:
        for index, candidate in enumerate(self._grid_settings.time_signature_events):
            if candidate == event:
                return EventTrackSelection(EventTrackLane.METER, index)
        return None

    def _update_grid_base_settings_from_events(self) -> None:
        base_tempo = self._grid_timeline.tempo_events[0]
        base_signature = self._grid_timeline.time_signature_events[0].time_signature
        self._grid_settings.bpm = float(base_tempo.bpm)
        self._grid_settings.numerator = int(base_signature.numerator)
        self._grid_settings.denominator = int(base_signature.denominator)

    def _load_grid_user_settings(self) -> MidiGridUserSettings:
        def _read_bool(key: str, default: bool) -> bool:
            raw_value = self._settings.value(key, default)
            if isinstance(raw_value, bool):
                return raw_value
            return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}

        def _read_float(key: str, default: float) -> float:
            try:
                return float(self._settings.value(key, default))
            except (TypeError, ValueError):
                return default

        def _read_int(key: str, default: int) -> int:
            try:
                return int(self._settings.value(key, default))
            except (TypeError, ValueError):
                return default

        numerator = max(1, _read_int("grid/numerator", 4))
        denominator = max(1, _read_int("grid/denominator", 4))
        bpm = max(1.0, _read_float("grid/bpm", 120.0))
        offset_ms = _read_float("grid/offset_ms", 0.0)
        subdivisions_per_beat = max(1, _read_int("grid/subdivisions_per_beat", 4))
        tempo_events = _parse_tempo_events_payload(self._settings.value("grid/tempo_events", None))
        time_signature_events = _parse_time_signature_events_payload(
            self._settings.value("grid/time_signature_events", None)
        )

        fallback_tempo_events = _default_grid_tempo_events()
        fallback_time_signature_events = _default_grid_time_signature_events()
        timeline = MidiGridTimeline(
            tempo_events=tempo_events or fallback_tempo_events,
            time_signature_events=time_signature_events or fallback_time_signature_events,
            offset_ms=offset_ms,
            default_division=GridDivision(subdivisions_per_beat),
        )
        base_tempo = timeline.tempo_events[0]
        base_signature = timeline.time_signature_events[0].time_signature
        return MidiGridUserSettings(
            bpm=float(base_tempo.bpm),
            numerator=int(base_signature.numerator),
            denominator=int(base_signature.denominator),
            offset_ms=float(offset_ms),
            subdivisions_per_beat=max(1, int(subdivisions_per_beat)),
            visible=_read_bool("grid/visible", True),
            event_snap_enabled=_read_bool("grid/event_snap_enabled", True),
            event_track_visible=_read_bool("grid/event_track_visible", True),
            tempo_events=tuple(timeline.tempo_events),
            time_signature_events=tuple(timeline.time_signature_events),
        )

    def _save_grid_user_settings(self, settings: MidiGridUserSettings) -> None:
        self._settings.setValue("grid/bpm", float(settings.bpm))
        self._settings.setValue("grid/numerator", int(settings.numerator))
        self._settings.setValue("grid/denominator", int(settings.denominator))
        self._settings.setValue("grid/offset_ms", float(settings.offset_ms))
        self._settings.setValue("grid/subdivisions_per_beat", int(settings.subdivisions_per_beat))
        self._settings.setValue("grid/visible", bool(settings.visible))
        self._settings.setValue("grid/event_snap_enabled", bool(settings.event_snap_enabled))
        self._settings.setValue("grid/event_track_visible", bool(settings.event_track_visible))
        self._settings.setValue("grid/tempo_events", _serialize_tempo_events(settings.tempo_events))
        self._settings.setValue(
            "grid/time_signature_events",
            _serialize_time_signature_events(settings.time_signature_events),
        )

    def _build_grid_timeline(self, settings: MidiGridUserSettings) -> MidiGridTimeline:
        return MidiGridTimeline(
            tempo_events=settings.tempo_events,
            time_signature_events=settings.time_signature_events,
            offset_ms=float(settings.offset_ms),
            default_division=GridDivision(max(1, int(settings.subdivisions_per_beat))),
        )

    def _apply_grid_settings_to_view(self) -> None:
        self._grid_timeline = self._build_grid_timeline(self._grid_settings)
        self._grid_settings.tempo_events = tuple(self._grid_timeline.tempo_events)
        self._grid_settings.time_signature_events = tuple(self._grid_timeline.time_signature_events)
        self._update_grid_base_settings_from_events()
        self.event_track_view.set_grid_timeline(self._grid_timeline)
        self.event_track_view.set_snap_enabled(self._grid_settings.event_snap_enabled)
        self.spectrogram_view.set_grid_timeline(self._grid_timeline)
        self.spectrogram_view.set_grid_division(GridDivision(self._grid_settings.subdivisions_per_beat))
        self.spectrogram_view.set_grid_visible(self._grid_settings.visible)
        self._midi_playback_controller.set_timeline(self._grid_timeline)
        self._midi_editor_controller.set_timeline(self._grid_timeline)
        self.event_snap_checkbox.blockSignals(True)
        self.event_snap_checkbox.setChecked(self._grid_settings.event_snap_enabled)
        self.event_snap_checkbox.blockSignals(False)
        self.grid_toggle_action.setChecked(self._grid_settings.visible)
        self.event_track_toggle_action.setChecked(self._grid_settings.event_track_visible)
        self._apply_event_track_visibility()
        self._sync_event_track_controls()

    def _persist_grid_settings(self, *, selection: EventTrackSelection | None = None) -> None:
        self._apply_grid_settings_to_view()
        self._save_grid_user_settings(self._grid_settings)
        if selection is not None:
            self.event_track_view.set_selected_event(selection)
        self.status_message.setText(self._build_grid_status_summary())

    def _update_grid_event_sequences(
        self,
        *,
        tempo_events: Sequence[TempoEvent] | None = None,
        time_signature_events: Sequence[TimeSignatureEvent] | None = None,
        selection: EventTrackSelection | None = None,
    ) -> None:
        previous_tempo_events = self._grid_settings.tempo_events
        previous_time_signature_events = self._grid_settings.time_signature_events
        if tempo_events is not None:
            self._grid_settings.tempo_events = tuple(tempo_events)
        if time_signature_events is not None:
            self._grid_settings.time_signature_events = tuple(time_signature_events)

        try:
            self._persist_grid_settings(selection=selection)
        except ValueError:
            self._grid_settings.tempo_events = previous_tempo_events
            self._grid_settings.time_signature_events = previous_time_signature_events
            raise

    def _build_grid_status_summary(self) -> str:
        visibility = "显示" if self._grid_settings.visible else "隐藏"
        snap_state = "吸附开" if self._grid_settings.event_snap_enabled else "吸附关"
        event_track_state = "事件轨道开" if self._grid_settings.event_track_visible else "事件轨道关"
        return f"网格 {visibility} | {snap_state} | {event_track_state}"

    def _sync_midi_backend_status(self) -> None:
        backend_status = self.midi_synth.status
        self._midi_backend_name = backend_status.backend_name
        self._midi_soundfont_path = backend_status.soundfont_path
        self._midi_output_name = backend_status.output_name

    def _recreate_midi_synth(self, *, show_feedback: bool) -> None:
        self._panic_preview_notes()
        try:
            self.midi_synth.close()
        except Exception:  # noqa: BLE001
            pass
        self.midi_synth = create_default_midi_synth(
            output_name=self._midi_settings.output_name,
            soundfont_path=self._midi_settings.soundfont_path,
            channel=self._midi_settings.channel,
            program=self._midi_settings.program,
        )
        self._midi_playback_controller.set_synth(self.midi_synth)
        self._midi_warning_shown = False
        self._sync_midi_backend_status()
        self._update_midi_status_display()
        if show_feedback:
            self.status_message.setText(self._build_midi_status_summary())

    def _load_colormap_stops(self) -> list[ColorStop]:
        raw = self._settings.value("render/colormap_stops", None)
        if raw is None:
            return default_spectracer_colormap_stops()

        try:
            payload = json.loads(str(raw))
        except Exception:  # noqa: BLE001
            return default_spectracer_colormap_stops()

        stops: list[ColorStop] = []
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict):
                    if "pos" not in entry or "color" not in entry:
                        continue
                    try:
                        stops.append((float(entry["pos"]), str(entry["color"])))
                    except (TypeError, ValueError):
                        continue
                elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                    try:
                        stops.append((float(entry[0]), str(entry[1])))
                    except (TypeError, ValueError):
                        continue

        if len(stops) < 2:
            return default_spectracer_colormap_stops()
        return normalize_colormap_stops(stops)

    def _save_colormap_stops(self, stops: Sequence[ColorStop]) -> None:
        normalized = normalize_colormap_stops(stops)
        payload = [{"pos": float(pos), "color": str(color)} for pos, color in normalized]
        self._settings.setValue("render/colormap_stops", json.dumps(payload, ensure_ascii=False))

    def _start_analysis(self, audio_path: Path, config: AnalyzeCliConfig, channel_modes: list[ChannelMode]) -> None:
        self.playback_engine.pause()

        # 防御性处理：如果上一次分析的进度窗口异常遗留，先关闭，避免阻塞主窗口交互。
        self._close_progress_dialog()

        # 重置 UI / 状态，避免在新分析期间仍显示上一份结果。
        self._analysis_primary_ready = False
        self._analysis_primary_mode = None
        requested_modes = [mode for mode in ChannelMode.ordered_modes() if mode in set(channel_modes)]
        if config.channel_mode in requested_modes:
            requested_modes.remove(config.channel_mode)
            requested_modes.insert(0, config.channel_mode)
        self._analysis_requested_modes = requested_modes

        self._current_result = None
        self._current_audio_path = None
        self._current_channel_mode = None
        self._channel_results = {}

        self.file_label.setText(f"正在分析: {audio_path.name}")
        self.info_label.setText("悬停热图查看音高 / 频率")
        self.channel_mode_combo.blockSignals(True)
        self.channel_mode_combo.clear()
        self.channel_mode_combo.blockSignals(False)
        self.channel_mode_combo.setEnabled(False)

        self.spectrogram_view.clear_result()
        self.event_track_view.clear()
        self.piano_widget.set_bin_frequencies([])
        self.transport_slider.setRange(0, 0)
        self.transport_slider.setValue(0)
        self.position_label.setText("00:00.000 / 00:00.000")

        self._set_analysis_busy(True)
        self.status_message.setText("正在分析，请稍候...")
        progress_dialog = self._ensure_progress_dialog("正在分析，请稍候...")
        progress_dialog.setRange(0, 0)
        progress_dialog.setValue(0)
        progress_dialog.setLabelText("正在分析，请稍候...")
        QApplication.processEvents()

        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(
            audio_path=audio_path,
            output_dir=DEFAULT_CACHE_DIR,
            config=config,
            channel_modes=self._analysis_requested_modes,
        )
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.progress_changed.connect(self._on_analysis_progress_changed)
        self._analysis_worker.mode_ready.connect(self._on_analysis_mode_ready)
        self._analysis_worker.finished.connect(self._on_analysis_finished)
        self._analysis_worker.failed.connect(self._on_analysis_failed)
        self._analysis_worker.completed.connect(self._on_analysis_completed)
        self._analysis_worker.completed.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._analysis_worker.deleteLater)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)
        self._analysis_thread.start()

    def _populate_channel_mode_combo(self, ordered_modes: list[ChannelMode]) -> None:
        current_value = self._current_channel_mode.value if self._current_channel_mode is not None else None
        self.channel_mode_combo.blockSignals(True)
        self.channel_mode_combo.clear()
        for mode in ordered_modes:
            if mode not in self._channel_results:
                continue
            self.channel_mode_combo.addItem(mode.display_name, mode.value)
        if current_value is not None:
            current_index = self.channel_mode_combo.findData(current_value)
            if current_index >= 0:
                self.channel_mode_combo.setCurrentIndex(current_index)
        self.channel_mode_combo.blockSignals(False)

    def _set_channel_mode(
        self,
        mode: ChannelMode,
        *,
        position_seconds: float | None = None,
        autoplay: bool | None = None,
        force_audio_reload: bool = False,
    ) -> None:
        result = self._channel_results.get(mode)
        if result is None:
            return

        previous_view_state: ViewState | None = None
        if self._current_result is not None:
            candidate_state = self.spectrogram_view.current_view_state()
            if candidate_state.total_x > 0.0 and candidate_state.total_y > 0.0:
                previous_view_state = candidate_state

        current_seconds = self.playback_engine.state.position_seconds if position_seconds is None else max(0.0, float(position_seconds))
        should_autoplay = self.playback_engine.state.is_playing if autoplay is None else bool(autoplay)

        self._panic_preview_notes()

        self._current_channel_mode = mode
        self._current_result = result

        self.piano_widget.set_bin_frequencies(result.cqt_result.bin_frequencies)

        initial_view_state = previous_view_state
        if initial_view_state is None:
            initial_view_state = self._load_persisted_view_state(result)

        self.event_track_view.set_duration_seconds(result.cqt_result.duration_seconds)
        self.event_track_view.set_cursor_seconds(current_seconds)
        self.spectrogram_view.set_cqt_result(
            result.cqt_result,
            sensitivity=self._display_sensitivity,
            contrast=self._display_contrast,
            initial_view_state=initial_view_state,
            cursor_seconds=current_seconds,
        )
        self.spectrogram_view.set_harmonics_enabled(self.harmonics_checkbox.isChecked())
        self.spectrogram_view.set_harmonic_count(self.harmonics_count_spinbox.value())
        self.spectrogram_view.set_seek_on_click_enabled(not should_autoplay)
        self._on_view_state_changed(self.spectrogram_view.current_view_state())

        self.piano_widget.set_highlight_bins(None, [])
        self.info_label.setText("悬停热图查看音高 / 频率")

        duration_seconds = result.cqt_result.duration_seconds
        self.transport_slider.setRange(0, max(1, int(duration_seconds * 1000.0)))
        self.transport_slider.setValue(int(min(current_seconds, duration_seconds) * 1000.0))
        self.position_label.setText(
            f"{self._format_seconds(current_seconds)} / {self._format_seconds(duration_seconds)}"
        )

        playback_path = result.playback_audio_path or result.input_path
        if playback_path is not None:
            resolved_playback = str(Path(playback_path).expanduser().resolve())
            if force_audio_reload or self.playback_engine.state.source_path != resolved_playback:
                self.playback_engine.load(
                    resolved_playback,
                    position_seconds=min(current_seconds, duration_seconds),
                    autoplay=should_autoplay,
                )

        combo_index = self.channel_mode_combo.findData(mode.value)
        if combo_index >= 0 and combo_index != self.channel_mode_combo.currentIndex():
            self.channel_mode_combo.blockSignals(True)
            self.channel_mode_combo.setCurrentIndex(combo_index)
            self.channel_mode_combo.blockSignals(False)

        self.status_message.setText(
            f"当前显示: {mode.display_name} | 帧数={result.num_frames} | 频率分箱={result.num_bins}"
        )

    def _seek_to_seconds(self, seconds: float) -> None:
        if self._analysis_busy and not self._analysis_primary_ready:
            return
        self.playback_engine.seek(seconds)
        self.spectrogram_view.set_cursor_seconds(seconds)
        self.event_track_view.set_cursor_seconds(seconds)
        self._follow_cursor_to(seconds)
        if not self.playback_engine.state.is_playing and self._current_result is not None:
            self._update_position_label(seconds, self._current_result.cqt_result.duration_seconds)

    def _on_slider_pressed(self) -> None:
        self._scrubbing = True

    def _on_slider_released(self) -> None:
        self._scrubbing = False
        self._seek_to_seconds(self.transport_slider.value() / 1000.0)

    def _on_slider_moved(self, position_ms: int) -> None:
        seconds = position_ms / 1000.0
        self.spectrogram_view.set_cursor_seconds(seconds)
        self.event_track_view.set_cursor_seconds(seconds)
        self._follow_cursor_to(seconds)
        if self._current_result is not None:
            self._update_position_label(seconds, self._current_result.cqt_result.duration_seconds)

    def _on_playback_position_changed(self, seconds: float) -> None:
        if not self._scrubbing:
            self.transport_slider.setValue(int(seconds * 1000.0))
            self.spectrogram_view.set_cursor_seconds(seconds)
            self.event_track_view.set_cursor_seconds(seconds)
            self._follow_cursor_to(seconds)
        duration_seconds = self._current_result.cqt_result.duration_seconds if self._current_result is not None else 0.0
        self._update_position_label(seconds, duration_seconds)

    def _on_playback_duration_changed(self, duration_seconds: float) -> None:
        effective_duration = duration_seconds
        if self._current_result is not None:
            effective_duration = self._current_result.cqt_result.duration_seconds
        self.transport_slider.setRange(0, max(1, int(effective_duration * 1000.0)))
        self._update_position_label(self.playback_engine.state.position_seconds, effective_duration)

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        self.play_button.setText("暂停" if is_playing else "播放")
        self.spectrogram_view.set_seek_on_click_enabled(not is_playing)
        if self._current_channel_mode is None:
            self.status_message.setText("播放中" if is_playing else "已暂停")
            return
        suffix = "播放中" if is_playing else "已暂停"
        self.status_message.setText(f"{self._current_channel_mode.display_name} | {suffix}")

    def _on_hover_changed(self, hover: HoverInfo | None) -> None:
        if hover is None:
            self.piano_widget.set_highlight_bins(None, [])
            self.info_label.setText("悬停热图查看音高 / 频率")
            return

        self.piano_widget.set_highlight_bins(hover.bin_index, hover.harmonic_bins)
        harmonic_suffix = ""
        if hover.harmonic_bins:
            harmonic_suffix = f" | 倍音 {len(hover.harmonic_bins)} 个"
        self.info_label.setText(
            f"Time {hover.time_seconds:.3f}s | {hover.note_name} | {hover.frequency_hz:.2f} Hz{harmonic_suffix}"
        )

    def _on_piano_note_triggered(self, midi_note: int, frequency_hz: float, note_name: str) -> None:
        self._preview_midi_note(
            midi_note,
            frequency_hz=frequency_hz,
            note_name=note_name,
            source_label="钢琴键",
        )

    def _on_spectrogram_note_audition_requested(self, hover: object) -> None:
        if not isinstance(hover, HoverInfo):
            return

        midi_note = int(round(frequency_to_midi(hover.frequency_hz)))
        self._preview_midi_note(
            midi_note,
            frequency_hz=hover.frequency_hz,
            note_name=hover.note_name,
            source_label="热图",
        )

    def _preview_midi_note(
        self,
        midi_note: int,
        *,
        frequency_hz: float,
        note_name: str,
        source_label: str,
    ) -> None:
        midi_note = max(0, min(127, int(midi_note)))
        if not self.midi_synth.is_available:
            if not self._midi_warning_shown:
                self.status_message.setText(f"MIDI 试听不可用：{self.midi_synth.status.message}")
                self._midi_warning_shown = True
            return

        self._midi_warning_shown = False
        retrigger_pending = (
            midi_note in self._midi_preview_active_notes or midi_note in self._midi_preview_restart_timers
        )
        self._cancel_preview_release_timer(midi_note, send_note_off=False)
        self._cancel_preview_restart_timer(midi_note)

        if retrigger_pending:
            self._send_preview_note_off(midi_note)
            self._schedule_preview_note_restart(midi_note)
        else:
            if not self._start_preview_note(midi_note):
                return

        self.status_message.setText(
            f"{source_label}试听：{note_name} | MIDI {midi_note} | {frequency_hz:.2f} Hz"
            f" | {self._format_midi_channel_text()} | {self._format_midi_program_text()}"
            f" | {self._format_active_midi_target_text()}"
        )

    def _start_preview_note(self, midi_note: int) -> bool:
        try:
            self.midi_synth.note_on(midi_note, velocity=MIDI_AUDITION_VELOCITY)
        except Exception as exc:  # noqa: BLE001
            self.status_message.setText(f"MIDI 试听失败：{exc}")
            return False

        self._midi_preview_active_notes.add(int(midi_note))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda note=midi_note: self._finish_preview_note(note))
        timer.start(MIDI_AUDITION_DURATION_MS)
        self._midi_preview_release_timers[int(midi_note)] = timer
        return True

    def _schedule_preview_note_restart(self, midi_note: int) -> None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda note=midi_note: self._restart_preview_note(note))
        timer.start(MIDI_AUDITION_RETRIGGER_GAP_MS)
        self._midi_preview_restart_timers[int(midi_note)] = timer

    def _restart_preview_note(self, midi_note: int) -> None:
        self._cancel_preview_restart_timer(midi_note)
        self._start_preview_note(int(midi_note))

    def _finish_preview_note(self, midi_note: int) -> None:
        self._cancel_preview_release_timer(midi_note, send_note_off=True)

    def _cancel_preview_release_timer(self, midi_note: int, *, send_note_off: bool) -> None:
        timer = self._midi_preview_release_timers.pop(int(midi_note), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

        if send_note_off:
            self._send_preview_note_off(int(midi_note))

    def _cancel_preview_restart_timer(self, midi_note: int) -> None:
        timer = self._midi_preview_restart_timers.pop(int(midi_note), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _send_preview_note_off(self, midi_note: int) -> None:
        if int(midi_note) not in self._midi_preview_active_notes:
            return
        self._midi_preview_active_notes.discard(int(midi_note))
        if self.midi_synth.is_available:
            try:
                self.midi_synth.note_off(int(midi_note))
            except Exception:  # noqa: BLE001
                pass

    def _panic_preview_notes(self) -> None:
        for midi_note in list(self._midi_preview_restart_timers.keys()):
            self._cancel_preview_restart_timer(midi_note)
        for midi_note in list(self._midi_preview_release_timers.keys()):
            self._cancel_preview_release_timer(midi_note, send_note_off=False)

        self._midi_preview_active_notes.clear()

        try:
            self.midi_synth.panic()
        except Exception:  # noqa: BLE001
            pass

    def _update_midi_status_display(self) -> None:
        if not hasattr(self, "midi_status_label"):
            return
        self.midi_status_label.setText(self._build_midi_status_bar_text())
        self.midi_status_label.setToolTip(self._build_midi_status_tooltip())

    def _build_midi_status_summary(self) -> str:
        mode_text = self._format_midi_mode_text()
        target_text = self._format_active_midi_target_text()
        if self.midi_synth.is_available:
            return (
                f"MIDI 就绪 | {mode_text}"
                f" | {self._format_midi_channel_text()}"
                f" | {self._format_midi_program_text()}"
                f" | {target_text}"
            )
        return (
            f"MIDI 不可用 | {mode_text}"
            f" | {self._format_midi_channel_text()}"
            f" | {self._format_midi_program_text()}"
            f" | {self.midi_synth.status.message}"
        )

    def _build_midi_status_bar_text(self) -> str:
        availability_text = "MIDI 已连接" if self.midi_synth.is_available else "MIDI 不可用"
        return (
            f"{availability_text}"
            f" | {self._format_midi_channel_text()}"
            f" | {self._format_midi_program_text()}"
        )

    def _build_midi_status_tooltip(self) -> str:
        tooltip_lines = [
            self._build_midi_status_summary(),
            f"当前后端状态：{self.midi_synth.status.message}",
        ]
        if self._midi_soundfont_path is not None:
            tooltip_lines.append(f"SoundFont：{self._midi_soundfont_path}")
        if self._midi_output_name is not None:
            tooltip_lines.append(f"MIDI 输出：{self._midi_output_name}")
        return "\n".join(tooltip_lines)

    def _format_midi_channel_text(self) -> str:
        channel_number = int(self._midi_settings.channel) + 1
        if is_drum_channel(self._midi_settings.channel):
            return f"通道 {channel_number} (Drum)"
        return f"通道 {channel_number}"

    def _format_midi_program_text(self) -> str:
        label = int(self._midi_settings.program) + 1
        program_name = gm_program_name(self._midi_settings.program, channel=self._midi_settings.channel)
        return f"音色 {label:03d} - {program_name}"

    def _format_midi_mode_text(self) -> str:
        if self._midi_settings.output_name:
            return "模式=系统 MIDI 输出"
        if self._midi_settings.soundfont_path:
            return "模式=指定 SoundFont"
        return "模式=自动检测"

    def _format_active_midi_target_text(self) -> str:
        if self._midi_backend_name == "midi_output" and self._midi_output_name:
            return f"实际=系统 MIDI {self._midi_output_name}"
        if self._midi_backend_name == "fluidsynth" and self._midi_soundfont_path is not None:
            soundfont_name = Path(self._midi_soundfont_path).name
            return f"实际=FluidSynth {soundfont_name}"
        return f"实际=不可用 ({self.midi_synth.status.message})"

    def _on_channel_mode_changed(self, index: int) -> None:
        data = self.channel_mode_combo.itemData(index)
        if data is None:
            return
        self._set_channel_mode(ChannelMode.parse(data))

    def _on_harmonics_setting_changed(self) -> None:
        self.spectrogram_view.set_harmonics_enabled(self.harmonics_checkbox.isChecked())
        self.spectrogram_view.set_harmonic_count(self.harmonics_count_spinbox.value())

    def _on_display_slider_changed(self) -> None:
        sensitivity = self._slider_to_display_value(self.sensitivity_slider.value())
        contrast = self._slider_to_display_value(self.contrast_slider.value())
        self._set_display_controls(sensitivity, contrast)

    def _on_view_state_changed(self, view_state: ViewState) -> None:
        self._last_view_state = view_state
        self._persist_view_state(view_state)
        self.event_track_view.set_view_window(view_state.x_min, view_state.x_max, view_state.total_x)
        self.piano_widget.set_visible_bin_range(view_state.y_min, view_state.y_max)
        self._update_scrollbars_from_view_state(view_state)

    def _on_horizontal_scrollbar_changed(self, value: int) -> None:
        if self._updating_scrollbars:
            return
        self.spectrogram_view.set_horizontal_scroll_ratio(value / SCROLLBAR_RESOLUTION)

    def _on_vertical_scrollbar_changed(self, value: int) -> None:
        if self._updating_scrollbars:
            return
        self.spectrogram_view.set_vertical_scroll_ratio(value / SCROLLBAR_RESOLUTION)

    def _on_analysis_mode_ready(self, mode: ChannelMode, result: AnalyzeExecutionResult) -> None:
        self._channel_results[mode] = result
        ordered_modes = [candidate for candidate in ChannelMode.ordered_modes() if candidate in self._channel_results]
        self._populate_channel_mode_combo(ordered_modes)

        if not self._analysis_primary_ready:
            self._analysis_primary_ready = True
            self._analysis_primary_mode = mode
            self._current_audio_path = result.input_path
            self._set_channel_mode(mode, force_audio_reload=True)

    def _on_analysis_progress_changed(self, progress: AnalysisProgress) -> None:
        progress_dialog = self._progress_dialog
        if progress_dialog is None:
            return
        total_steps = max(1, int(progress.total_steps))
        completed_steps = max(0, min(int(progress.completed_steps), total_steps))
        progress_dialog.setRange(0, total_steps)
        progress_dialog.setValue(completed_steps)
        progress_dialog.setLabelText(str(progress.message))
        self.status_message.setText(str(progress.message))

    def _on_analysis_finished(self, batch_result: MultiChannelAnalysisResult) -> None:
        ordered_modes = [mode for mode in ChannelMode.ordered_modes() if mode in batch_result.results_by_mode]
        if not ordered_modes:
            self._show_error("未生成任何分析结果")
            self._set_analysis_busy(False)
            return

        for mode in ordered_modes:
            self._channel_results[mode] = batch_result.results_by_mode[mode]
        self._populate_channel_mode_combo(ordered_modes)
        preferred_mode = self._analysis_primary_mode or ordered_modes[0]
        self._set_channel_mode(preferred_mode, force_audio_reload=True)
        self.file_label.setText(str(batch_result.input_path.name))
        self._current_audio_path = batch_result.input_path
        self._set_analysis_busy(False)

    def _on_analysis_failed(self, message: str) -> None:
        self._show_error(message)
        self._set_analysis_busy(False)

    def _on_analysis_completed(self) -> None:
        self._close_progress_dialog()
        if self._analysis_thread is not None:
            self._analysis_thread = None
        self._analysis_worker = None

    def _ensure_progress_dialog(self, label_text: str) -> QProgressDialog:
        dialog = self._progress_dialog
        if dialog is None:
            dialog = QProgressDialog(self)
            dialog.setWindowTitle("分析中")
            dialog.setCancelButton(None)
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
            dialog.setMinimumDuration(0)
            self._progress_dialog = dialog
        dialog.setLabelText(label_text)
        dialog.show()
        return dialog

    def _close_progress_dialog(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None

    def _persist_view_state(self, view_state: ViewState) -> None:
        if self._suppress_view_state_persist:
            return
        if view_state.total_x <= 0.0 or view_state.total_y <= 0.0:
            return
        x_min_ratio = view_state.x_min / view_state.total_x
        x_max_ratio = view_state.x_max / view_state.total_x
        y_min_ratio = view_state.y_min / view_state.total_y
        y_max_ratio = view_state.y_max / view_state.total_y
        self._settings.setValue("view/x_min_ratio", float(x_min_ratio))
        self._settings.setValue("view/x_max_ratio", float(x_max_ratio))
        self._settings.setValue("view/y_min_ratio", float(y_min_ratio))
        self._settings.setValue("view/y_max_ratio", float(y_max_ratio))

    def _load_persisted_view_state(self, result: AnalyzeExecutionResult) -> ViewState | None:
        total_x = result.cqt_result.duration_seconds
        total_y = float(result.cqt_result.num_bins)
        raw_values = (
            self._settings.value("view/x_min_ratio", None),
            self._settings.value("view/x_max_ratio", None),
            self._settings.value("view/y_min_ratio", None),
            self._settings.value("view/y_max_ratio", None),
        )
        if any(value is None for value in raw_values):
            return None
        try:
            x_min_ratio = float(raw_values[0])
            x_max_ratio = float(raw_values[1])
            y_min_ratio = float(raw_values[2])
            y_max_ratio = float(raw_values[3])
        except (TypeError, ValueError):
            return None
        return ViewState(
            x_min=max(0.0, min(total_x, x_min_ratio * total_x)),
            x_max=max(0.0, min(total_x, x_max_ratio * total_x)),
            y_min=max(0.0, min(total_y, y_min_ratio * total_y)),
            y_max=max(0.0, min(total_y, y_max_ratio * total_y)),
            total_x=total_x,
            total_y=total_y,
        )

    def _update_scrollbars_from_view_state(self, view_state: ViewState) -> None:
        self._updating_scrollbars = True
        try:
            x_span = max(0.0, view_state.x_max - view_state.x_min)
            x_movable = max(0.0, view_state.total_x - x_span)
            if x_movable <= 0.0:
                self.horizontal_scrollbar.setRange(0, 0)
                self.horizontal_scrollbar.setValue(0)
            else:
                ratio = view_state.x_min / x_movable
                self.horizontal_scrollbar.setRange(0, SCROLLBAR_RESOLUTION)
                self.horizontal_scrollbar.setValue(int(round(max(0.0, min(1.0, ratio)) * SCROLLBAR_RESOLUTION)))

            y_span = max(0.0, view_state.y_max - view_state.y_min)
            y_movable = max(0.0, view_state.total_y - y_span)
            if y_movable <= 0.0:
                self.vertical_scrollbar.setRange(0, 0)
                self.vertical_scrollbar.setValue(0)
            else:
                ratio = view_state.y_min / y_movable
                self.vertical_scrollbar.setRange(0, SCROLLBAR_RESOLUTION)
                self.vertical_scrollbar.setValue(int(round(max(0.0, min(1.0, ratio)) * SCROLLBAR_RESOLUTION)))
        finally:
            self._updating_scrollbars = False

    def _set_analysis_busy(self, busy: bool) -> None:
        self._analysis_busy = bool(busy)
        interactive_ready = not self._analysis_busy
        has_audio = self._current_result is not None
        self.play_button.setEnabled(interactive_ready and has_audio)
        self.transport_slider.setEnabled(interactive_ready and has_audio)
        self.channel_mode_combo.setEnabled(interactive_ready and len(self._channel_results) > 1)

    def _set_display_controls(self, sensitivity: float, contrast: float, *, apply_to_view: bool = True) -> None:
        sensitivity_value = max(0.1, min(4.0, float(sensitivity)))
        contrast_value = max(0.1, min(4.0, float(contrast)))
        self._display_sensitivity = sensitivity_value
        self._display_contrast = contrast_value
        self.sensitivity_value_label.setText(f"{sensitivity_value:.2f}")
        self.contrast_value_label.setText(f"{contrast_value:.2f}")
        self.sensitivity_slider.blockSignals(True)
        self.sensitivity_slider.setValue(self._display_value_to_slider(sensitivity_value))
        self.sensitivity_slider.blockSignals(False)
        self.contrast_slider.blockSignals(True)
        self.contrast_slider.setValue(self._display_value_to_slider(contrast_value))
        self.contrast_slider.blockSignals(False)
        if apply_to_view:
            self.spectrogram_view.update_display_settings(
                sensitivity=sensitivity_value,
                contrast=contrast_value,
            )

    def _update_position_label(self, current_seconds: float, duration_seconds: float) -> None:
        self.position_label.setText(
            f"{self._format_seconds(current_seconds)} / {self._format_seconds(duration_seconds)}"
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        focus_widget = QApplication.focusWidget()
        focus_in_event_track = focus_widget is not None and (
            focus_widget is self.event_track_view or self.event_track_view.isAncestorOf(focus_widget)
        )
        if (
            event.key() == Qt.Key.Key_Delete
            and self._midi_session.editor_state.enabled
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not focus_in_event_track
        ):
            selected_note_ids = self._midi_session.selected_note_ids
            self._delete_selected_midi_notes()
            if selected_note_ids:
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._close_progress_dialog()
        self._panic_preview_notes()

        try:
            self.playback_engine.pause()
        except Exception:  # noqa: BLE001
            pass

        self._midi_editor_controller.close()
        self._midi_playback_controller.close()
        self.midi_synth.close()
        super().closeEvent(event)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Spectracer", message)

    def _read_bool_setting(self, key: str, default: bool) -> bool:
        raw_value = self._settings.value(key, default)
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}

    def _read_float_setting(
        self,
        key: str,
        default: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        try:
            value = float(self._settings.value(key, default))
        except (TypeError, ValueError):
            value = float(default)
        if minimum is not None:
            value = max(float(minimum), value)
        if maximum is not None:
            value = min(float(maximum), value)
        return value

    def _read_int_setting(self, key: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            value = int(self._settings.value(key, default))
        except (TypeError, ValueError):
            value = int(default)
        if minimum is not None:
            value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    def _fraction_to_slider_value(self, value: float) -> int:
        clamped = max(0.0, min(1.0, float(value)))
        return int(round(clamped * FRACTION_SLIDER_RANGE))

    def _slider_value_to_fraction(self, value: int) -> float:
        return max(0.0, min(1.0, float(value) / FRACTION_SLIDER_RANGE))

    def _format_percent_label(self, value: float) -> str:
        return f"{int(round(max(0.0, min(1.0, float(value))) * 100.0))}%"

    def _toggle_edit_mode_shortcut(self) -> None:
        self._apply_midi_editor_state(
            self._midi_session.editor_state.with_updates(enabled=not self._midi_session.editor_state.enabled),
            persist=True,
        )

    def _copy_selected_midi_notes(self) -> None:
        if not self._midi_session.editor_state.enabled:
            return
        copied_notes = self._midi_editor_controller.copy_selected_notes()
        if copied_notes:
            self.status_message.setText(f"已复制 {len(copied_notes)} 个音符")

    def _paste_copied_midi_notes(self) -> None:
        if not self._midi_session.editor_state.enabled:
            return
        pasted_notes = self._midi_editor_controller.paste_copied_notes()
        if pasted_notes:
            self.status_message.setText(f"已粘贴 {len(pasted_notes)} 个音符")

    def _delete_selected_midi_notes(self) -> None:
        if not self._midi_session.editor_state.enabled:
            return
        deleted_notes = self._midi_editor_controller.delete_selected_notes()
        if deleted_notes:
            self.status_message.setText(f"已删除 {len(deleted_notes)} 个音符")

    def _on_edit_mode_toggled(self, enabled: bool) -> None:
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(enabled=enabled), persist=True)

    def _set_editor_tool(self, tool: MidiEditorTool, *, persist: bool) -> None:
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(tool=tool), persist=persist)

    def _on_editor_tool_button_clicked(self, button) -> None:
        tool = MidiEditorTool.parse(button.property("midiTool"))
        self._set_editor_tool(tool, persist=True)

    def _on_editor_snap_toggled(self, enabled: bool) -> None:
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(snap_enabled=enabled), persist=True)

    def _on_editor_snap_resolution_changed(self, index: int) -> None:
        data = self.editor_snap_resolution_combo.itemData(index)
        if data is None:
            return
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(snap_resolution=data), persist=True)

    def _on_editor_active_channel_changed(self, index: int) -> None:
        data = self.editor_active_channel_combo.itemData(index)
        if data is None:
            return
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(active_channel=int(data)), persist=True)

    def _on_editor_darken_slider_changed(self, value: int) -> None:
        self._apply_midi_editor_state(self._midi_session.editor_state.with_updates(darken_amount=self._slider_value_to_fraction(value)), persist=True)

    def _on_background_mix_slider_changed(self, value: int) -> None:
        self._set_background_mix_gain(self._slider_value_to_fraction(value), persist=True)

    def _on_midi_mix_slider_changed(self, value: int) -> None:
        self._set_midi_mix_gain(self._slider_value_to_fraction(value), persist=True)

    def _slider_to_display_value(self, value: int) -> float:
        return max(0.1, min(4.0, float(value) / 100.0))

    def _display_value_to_slider(self, value: float) -> int:
        clamped = max(0.1, min(4.0, float(value)))
        return int(round(clamped * 100.0))

    def _format_seconds(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60.0)
        remainder = seconds - (minutes * 60.0)
        return f"{minutes:02d}:{remainder:06.3f}"

    def _load_initial_runtime_config(self) -> tuple[AnalyzeCliConfig, Path | None, str | None]:
        try:
            config, config_path = load_runtime_analyze_config(None)
            return config, config_path, None
        except Exception as exc:  # noqa: BLE001
            return AnalyzeCliConfig(), None, str(exc)


def launch_ui(initial_audio_path: str | Path | None = None) -> int:
    app = QApplication(sys.argv)
    window = SpectracerMainWindow(initial_audio_path=initial_audio_path)
    window.show()
    return app.exec()


def main(input_path: str | None = None) -> int:
    return launch_ui(initial_audio_path=input_path)
