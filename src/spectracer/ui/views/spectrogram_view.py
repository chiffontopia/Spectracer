from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsSimpleTextItem

from spectracer.core.harmonics import bin_index_from_plot_y, harmonic_bin_indices
from spectracer.core.models import CqtResult
from spectracer.core.pitch import frequency_to_midi, frequency_to_note_name
from spectracer.midi.editor_model import MidiEditorState, MidiNote
from spectracer.dsp.colormap import ColorStop, default_spectracer_colormap_stops, make_linear_colormap
from spectracer.dsp.visualization import NormalizationMode, normalize_cqt_for_display
from spectracer.midi.grid import GridDivision, GridLine, GridLineKind, MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.ui.overlays.midi_note_overlay import MidiNoteOverlay

pg.setConfigOptions(imageAxisOrder="row-major")


@dataclass(slots=True)
class HoverInfo:
    time_seconds: float
    bin_index: int
    frequency_hz: float
    note_name: str
    harmonic_bins: tuple[int, ...] = ()


@dataclass(slots=True)
class ViewState:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    total_x: float
    total_y: float


@dataclass(slots=True, frozen=True)
class MidiEditorPointerEvent:
    seconds: float
    plot_y: float
    midi_pitch: int
    modifiers: Qt.KeyboardModifier


@dataclass(slots=True, frozen=True)
class MidiEditorContextMenuRequest:
    pointer_event: MidiEditorPointerEvent
    global_pos: QPoint


class SpectrogramView(pg.PlotWidget):
    """基于 PyQtGraph 的最小热图视图。"""

    hover_changed = pyqtSignal(object)
    seek_requested = pyqtSignal(float)
    note_audition_requested = pyqtSignal(object)
    view_state_changed = pyqtSignal(object)
    midi_editor_pointer_pressed = pyqtSignal(object)
    midi_editor_pointer_moved = pyqtSignal(object)
    midi_editor_pointer_released = pyqtSignal(object)
    midi_editor_context_menu_requested = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self._result: CqtResult | None = None
        self._duration_seconds = 0.0
        self._display_sensitivity = 1.0
        self._display_contrast = 1.0
        self._harmonics_enabled = True
        self._harmonic_count = 6
        self._seek_on_click_enabled = True
        self._normalization_mode = NormalizationMode.DB_PERCENTILE
        self._normalization_ref_percentile = 99.5
        self._last_hover_info: HoverInfo | None = None
        self._harmonic_band_items: list[QGraphicsRectItem] = []
        self._min_x_span = 0.05
        self._min_y_span = 1.0
        self._grid_timeline = MidiGridTimeline.constant()
        self._grid_visible = True
        self._grid_division = GridDivision(4)
        self._max_grid_label_count = 128
        self._grid_label_items: list[QGraphicsSimpleTextItem] = []
        self._midi_session = MidiSession()
        self._midi_editor_state = self._midi_session.editor_state
        self._midi_overlay_enabled = True
        self._midi_note_overlay = MidiNoteOverlay()
        self._midi_note_overlay.set_session(self._midi_session)
        self._midi_note_overlay.set_editor_state(self._midi_editor_state)
        self._attach_midi_session_signals(self._midi_session)

        plot_item = self.getPlotItem()
        plot_item.hideButtons()
        plot_item.setMenuEnabled(False)

        self.setBackground("#111111")
        self.showGrid(x=False, y=False, alpha=0.0)
        self.setMouseEnabled(x=False, y=False)
        self.setLabel("bottom", "Time", units="s")
        self.setLabel("left", "Pitch bin")

        self._image_item = pg.ImageItem()
        self.addItem(self._image_item)

        self._grid_subdivision_path_item = self._create_path_item(QColor(255, 255, 255, 32), z_value=2)
        self._grid_beat_path_item = self._create_path_item(QColor(255, 255, 255, 64), z_value=3)
        self._grid_bar_path_item = self._create_path_item(QColor(255, 214, 79, 150), z_value=4)
        self.addItem(self._grid_subdivision_path_item, ignoreBounds=True)
        self.addItem(self._grid_beat_path_item, ignoreBounds=True)
        self.addItem(self._grid_bar_path_item, ignoreBounds=True)

        self._dim_overlay_item = QGraphicsRectItem()
        dim_pen = QPen()
        dim_pen.setStyle(Qt.PenStyle.NoPen)
        self._dim_overlay_item.setPen(dim_pen)
        self._dim_overlay_item.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self._dim_overlay_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._dim_overlay_item.setZValue(1.0)
        self._dim_overlay_item.hide()
        self.addItem(self._dim_overlay_item, ignoreBounds=True)
        for overlay_item in self._midi_note_overlay.graphics_items():
            self.addItem(overlay_item, ignoreBounds=True)

        self._cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#FFD54F", width=2))
        self._cursor_line.hide()
        self._cursor_line.setZValue(8)
        self.addItem(self._cursor_line)

        self._primary_band_item = self._create_band_item(
            fill_color=QColor(255, 214, 79, 100),
            border_color=QColor(255, 214, 79, 220),
        )
        self.addItem(self._primary_band_item, ignoreBounds=True)

        self.set_colormap_stops(default_spectracer_colormap_stops())

        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.getViewBox().sigRangeChanged.connect(self._on_view_box_range_changed)

    def clear_result(self) -> None:
        self._result = None
        self._duration_seconds = 0.0
        self._image_item.clear()
        self._cursor_line.hide()
        self._hide_all_hover_bands()
        self._clear_grid_overlay()
        self._dim_overlay_item.setRect(QRectF())
        self._dim_overlay_item.hide()
        self._midi_note_overlay.set_result(None)
        self._last_hover_info = None
        self.view_state_changed.emit(ViewState(0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

    def set_colormap_stops(self, stops: Sequence[ColorStop]) -> None:
        """设置热图色盘（颜色节点）。"""

        cmap = make_linear_colormap(name="spectracer-custom", stops=stops)
        lut = (cmap(np.linspace(0.0, 1.0, 256)) * 255).astype(np.uint8)
        self._image_item.setLookupTable(lut)

    def set_normalization_settings(
        self,
        *,
        mode: NormalizationMode | str | None = None,
        ref_percentile: float | None = None,
    ) -> None:
        """更新归一化策略（并尽量保持视图缩放/位置不变）。"""

        if mode is not None:
            self._normalization_mode = NormalizationMode.parse(mode)
        if ref_percentile is not None:
            self._normalization_ref_percentile = max(0.0, min(100.0, float(ref_percentile)))

        if self._result is None:
            return

        current_state = self.current_view_state()
        self._apply_display_image()
        self._set_view_range(
            current_state.x_min,
            current_state.x_max,
            current_state.y_min,
            current_state.y_max,
        )

    def set_harmonics_enabled(self, enabled: bool) -> None:
        self._harmonics_enabled = bool(enabled)
        self._refresh_hover_highlights()

    def set_harmonic_count(self, harmonic_count: int) -> None:
        self._harmonic_count = max(1, int(harmonic_count))
        self._refresh_hover_highlights()

    def set_seek_on_click_enabled(self, enabled: bool) -> None:
        self._seek_on_click_enabled = bool(enabled)

    def set_grid_timeline(self, timeline: MidiGridTimeline) -> None:
        self._grid_timeline = timeline
        self._midi_note_overlay.set_timeline(timeline)
        self._refresh_grid_overlay()

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._refresh_grid_overlay()

    def set_grid_division(self, division: GridDivision) -> None:
        self._grid_division = GridDivision(division.subdivisions_per_beat)
        self._refresh_grid_overlay()

    def set_midi_session(self, session: MidiSession) -> None:
        if not isinstance(session, MidiSession):
            raise TypeError("session 必须是 MidiSession")
        if session is not self._midi_session:
            self._detach_midi_session_signals(self._midi_session)
            self._midi_session = session
            self._attach_midi_session_signals(self._midi_session)
        self._midi_note_overlay.set_session(self._midi_session)
        self._on_session_editor_state_changed(self._midi_session.editor_state)

    def set_midi_editor_state(self, editor_state: MidiEditorState) -> None:
        if not isinstance(editor_state, MidiEditorState):
            raise TypeError("editor_state 必须是 MidiEditorState")
        self._midi_editor_state = editor_state
        self._midi_note_overlay.set_editor_state(editor_state)
        self._refresh_midi_overlay_state()

    def set_midi_overlay_visible(self, visible: bool) -> None:
        self._midi_overlay_enabled = bool(visible)
        self._refresh_midi_overlay_state()

    def set_midi_overlay_darken_amount(self, amount: float) -> None:
        self.set_midi_editor_state(self._midi_editor_state.with_updates(darken_amount=amount))

    def midi_overlay_darken_amount(self) -> float:
        return float(self._midi_editor_state.darken_amount)

    def is_midi_overlay_visible(self) -> bool:
        return self._midi_note_overlay.is_visible()

    def is_dim_overlay_visible(self) -> bool:
        return self._dim_overlay_item.isVisible()

    def dim_overlay_alpha(self) -> int:
        return int(self._dim_overlay_item.brush().color().alpha())

    def midi_note_rect(self, note_id: str) -> QRectF | None:
        return self._midi_note_overlay.note_rect(note_id)

    def midi_note_at(self, seconds: float, plot_y: float) -> MidiNote | None:
        return self._midi_note_overlay.hit_test(seconds, plot_y)

    def set_cqt_result(
        self,
        result: CqtResult,
        *,
        sensitivity: float = 1.0,
        contrast: float = 1.0,
        initial_view_state: ViewState | None = None,
        cursor_seconds: float | None = 0.0,
    ) -> None:
        self._result = result
        self._duration_seconds = max(result.duration_seconds, 1e-3)
        self._display_sensitivity = float(sensitivity)
        self._display_contrast = float(contrast)
        self._min_x_span = max(0.02, self._duration_seconds / max(16.0, float(result.num_frames)))
        self._min_y_span = 1.0
        self._last_hover_info = None
        self._hide_all_hover_bands()

        self._apply_display_image()
        self._midi_note_overlay.set_result(result, duration_seconds=self._duration_seconds)
        self._update_dim_overlay_geometry()

        total_bins = float(result.num_bins)
        view_box = self.getViewBox()
        view_box.setLimits(
            xMin=0.0,
            xMax=self._duration_seconds,
            yMin=0.0,
            yMax=total_bins,
            minXRange=self._min_x_span,
            maxXRange=self._duration_seconds,
            minYRange=self._min_y_span,
            maxYRange=total_bins,
        )

        if initial_view_state is None:
            self.reset_view()
        else:
            self._set_view_range(
                initial_view_state.x_min,
                initial_view_state.x_max,
                initial_view_state.y_min,
                initial_view_state.y_max,
            )

        if cursor_seconds is None:
            cursor_seconds = 0.0
        self.set_cursor_seconds(float(cursor_seconds))
        self._refresh_midi_overlay_state()
        self._refresh_grid_overlay()

    def update_display_settings(self, *, sensitivity: float, contrast: float) -> None:
        if self._result is None:
            self._display_sensitivity = float(sensitivity)
            self._display_contrast = float(contrast)
            return

        current_state = self.current_view_state()
        self._display_sensitivity = float(sensitivity)
        self._display_contrast = float(contrast)
        self._apply_display_image()
        self._set_view_range(
            current_state.x_min,
            current_state.x_max,
            current_state.y_min,
            current_state.y_max,
        )

    def reset_view(self) -> None:
        if self._result is None:
            return
        self._set_view_range(0.0, self._duration_seconds, 0.0, float(self._result.num_bins))

    def center_on_time(self, seconds: float) -> None:
        if self._result is None:
            return
        state = self.current_view_state()
        span = max(self._min_x_span, state.x_max - state.x_min)
        if span <= 0.0:
            return

        target = max(0.0, min(float(seconds), self._max_time()))
        self._set_view_range(target - (span * 0.5), target + (span * 0.5), state.y_min, state.y_max)

    def ensure_time_visible(
        self,
        seconds: float,
        *,
        anchor_ratio: float = 0.3,
        margin_ratio: float = 0.15,
    ) -> bool:
        """确保某个时间点在当前视口范围内可见，必要时仅做水平平移。

        - anchor_ratio: 发生滚动时，将游标放在视口内的相对位置（0=最左，0.5=居中）
        - margin_ratio: 安全边距；当游标进入边距区才触发滚动。
        """

        if self._result is None:
            return False

        state = self.current_view_state()
        span = state.x_max - state.x_min
        if span <= 0.0 or state.total_x <= 0.0:
            return False

        margin = max(0.0, min(span * float(margin_ratio), span))
        target = max(0.0, min(float(seconds), self._max_time()))

        if (state.x_min + margin) <= target <= (state.x_max - margin):
            return False

        anchor = max(0.0, min(1.0, float(anchor_ratio)))
        x_min = target - (span * anchor)
        x_max = x_min + span
        self._set_view_range(x_min, x_max, state.y_min, state.y_max)
        return True

    def zoom_horizontal(self, factor: float, *, anchor_x: float | None = None) -> None:
        state = self.current_view_state()
        if state.total_x <= 0.0:
            return
        center = state.x_min + ((state.x_max - state.x_min) * 0.5) if anchor_x is None else float(anchor_x)
        x_min, x_max = self._scaled_range(state.x_min, state.x_max, 0.0, state.total_x, factor, center, self._min_x_span)
        self._set_view_range(x_min, x_max, state.y_min, state.y_max)

    def zoom_vertical(self, factor: float, *, anchor_y: float | None = None) -> None:
        state = self.current_view_state()
        if state.total_y <= 0.0:
            return
        center = state.y_min + ((state.y_max - state.y_min) * 0.5) if anchor_y is None else float(anchor_y)
        y_min, y_max = self._scaled_range(state.y_min, state.y_max, 0.0, state.total_y, factor, center, self._min_y_span)
        self._set_view_range(state.x_min, state.x_max, y_min, y_max)

    def pan_horizontal_by_fraction(self, fraction: float) -> None:
        state = self.current_view_state()
        span = state.x_max - state.x_min
        if state.total_x <= span:
            return
        delta = span * float(fraction)
        x_min, x_max = self._clamp_range(state.x_min + delta, state.x_max + delta, 0.0, state.total_x, self._min_x_span)
        self._set_view_range(x_min, x_max, state.y_min, state.y_max)

    def set_horizontal_scroll_ratio(self, ratio: float) -> None:
        state = self.current_view_state()
        movable = max(0.0, state.total_x - (state.x_max - state.x_min))
        if movable <= 0.0:
            return
        clamped_ratio = max(0.0, min(1.0, float(ratio)))
        x_min = movable * clamped_ratio
        x_max = x_min + (state.x_max - state.x_min)
        self._set_view_range(x_min, x_max, state.y_min, state.y_max)

    def set_vertical_scroll_ratio(self, ratio: float) -> None:
        state = self.current_view_state()
        movable = max(0.0, state.total_y - (state.y_max - state.y_min))
        if movable <= 0.0:
            return
        clamped_ratio = max(0.0, min(1.0, float(ratio)))
        y_min = movable * (1.0 - clamped_ratio)
        y_max = y_min + (state.y_max - state.y_min)
        self._set_view_range(state.x_min, state.x_max, y_min, y_max)

    def current_view_state(self) -> ViewState:
        if self._result is None:
            return ViewState(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        x_range, y_range = self.getViewBox().viewRange()
        return ViewState(
            x_min=float(x_range[0]),
            x_max=float(x_range[1]),
            y_min=float(y_range[0]),
            y_max=float(y_range[1]),
            total_x=self._duration_seconds,
            total_y=float(self._result.num_bins),
        )

    def seconds_to_plot_x(self, seconds: float) -> float:
        return float(seconds)

    def plot_x_to_seconds(self, plot_x: float) -> float:
        return float(plot_x)

    def pitch_to_plot_y(self, pitch: int | float) -> float | None:
        return self._midi_note_overlay.pitch_to_plot_y(pitch)

    def midi_pitch_band(self, pitch: int | float) -> tuple[float, float] | None:
        return self._midi_note_overlay.pitch_band_for_pitch(pitch)

    def plot_y_to_midi_pitch(self, plot_y: float) -> int:
        if self._result is None:
            raise ValueError("当前没有频谱结果")
        bin_index = bin_index_from_plot_y(plot_y, self._result.num_bins)
        return int(round(frequency_to_midi(float(self._result.bin_frequencies[bin_index]))))

    def set_midi_selection_rect(self, rect: QRectF | None) -> None:
        self._midi_note_overlay.set_selection_rect(rect)

    def midi_selection_rect(self) -> QRectF | None:
        return self._midi_note_overlay.selection_rect()

    def set_midi_preview_rect(self, rect: QRectF | None) -> None:
        self._midi_note_overlay.set_preview_rect(rect)

    def midi_preview_rect(self) -> QRectF | None:
        return self._midi_note_overlay.preview_rect()

    def set_cursor_seconds(self, seconds: float) -> None:
        if self._result is None:
            return
        clamped = max(0.0, min(float(seconds), self._max_time()))
        self._cursor_line.setPos(clamped)
        self._cursor_line.show()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._last_hover_info = None
        self._hide_all_hover_bands()
        self.hover_changed.emit(None)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._result is None:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.accept()
            return

        scene_position = self.mapToScene(event.position().toPoint())
        point = self.getViewBox().mapSceneToView(scene_position)
        modifiers = event.modifiers()
        zoom_in_factor = 0.85
        zoom_out_factor = 1.0 / zoom_in_factor

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            factor = zoom_in_factor if delta > 0 else zoom_out_factor
            self.zoom_vertical(factor, anchor_y=float(point.y()))
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            factor = zoom_in_factor if delta > 0 else zoom_out_factor
            self.zoom_horizontal(factor, anchor_x=float(point.x()))
        else:
            direction = -1.0 if delta > 0 else 1.0
            self.pan_horizontal_by_fraction(0.12 * direction)

        event.accept()

    def _max_time(self) -> float:
        if self._result is None or self._result.frame_times.size == 0:
            return 0.0
        return float(self._result.frame_times[-1])

    def _apply_display_image(self) -> None:
        if self._result is None:
            return
        image = normalize_cqt_for_display(
            self._result,
            sensitivity=self._display_sensitivity,
            contrast=self._display_contrast,
            mode=self._normalization_mode,
            ref_percentile=self._normalization_ref_percentile,
        )
        self._image_item.setImage(image, autoLevels=False, levels=(0.0, 1.0))
        self._image_item.setRect(QRectF(0.0, 0.0, self._duration_seconds, float(self._result.num_bins)))

    def _update_dim_overlay_geometry(self) -> None:
        if self._result is None:
            self._dim_overlay_item.setRect(QRectF())
            self._dim_overlay_item.hide()
            return
        self._dim_overlay_item.setRect(QRectF(0.0, 0.0, self._duration_seconds, float(self._result.num_bins)))
        self._refresh_dim_overlay()

    def _refresh_dim_overlay(self) -> None:
        if self._result is None or not self._midi_editor_state.enabled:
            self._dim_overlay_item.hide()
            return
        alpha = int(round(max(0.0, min(1.0, self._midi_editor_state.darken_amount)) * 255.0))
        self._dim_overlay_item.setBrush(QBrush(QColor(0, 0, 0, alpha)))
        self._dim_overlay_item.setVisible(alpha > 0)

    def _refresh_midi_overlay_state(self) -> None:
        overlay_visible = self._midi_overlay_enabled and self._midi_editor_state.enabled and self._result is not None
        self._midi_note_overlay.set_visible(overlay_visible)
        self._refresh_dim_overlay()

    def _attach_midi_session_signals(self, session: MidiSession) -> None:
        session.editor_state_changed.connect(self._on_session_editor_state_changed)

    def _detach_midi_session_signals(self, session: MidiSession) -> None:
        try:
            session.editor_state_changed.disconnect(self._on_session_editor_state_changed)
        except (TypeError, RuntimeError):
            return

    def _on_session_editor_state_changed(self, editor_state: object) -> None:
        if not isinstance(editor_state, MidiEditorState):
            return
        self._midi_editor_state = editor_state
        self._midi_note_overlay.set_editor_state(editor_state)
        self._refresh_midi_overlay_state()

    def _create_path_item(self, color: QColor, *, z_value: float) -> QGraphicsPathItem:
        pen = QPen(color, 1)
        pen.setCosmetic(True)
        item = QGraphicsPathItem()
        item.setPen(pen)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setZValue(z_value)
        item.hide()
        return item

    def _clear_grid_overlay(self) -> None:
        empty_path = QPainterPath()
        self._grid_subdivision_path_item.setPath(empty_path)
        self._grid_beat_path_item.setPath(empty_path)
        self._grid_bar_path_item.setPath(empty_path)
        self._grid_subdivision_path_item.hide()
        self._grid_beat_path_item.hide()
        self._grid_bar_path_item.hide()
        self._clear_grid_label_items()

    def _clear_grid_label_items(self) -> None:
        for item in self._grid_label_items:
            self.removeItem(item)
        self._grid_label_items.clear()

    def _refresh_grid_overlay(self) -> None:
        if self._result is None:
            self._clear_grid_overlay()
            return
        if not self._grid_visible:
            self._grid_subdivision_path_item.hide()
            self._grid_beat_path_item.hide()
            self._grid_bar_path_item.hide()
            self._clear_grid_label_items()
            return

        lines = self._grid_timeline.iter_grid_lines_for_duration(
            self._duration_seconds,
            division=self._grid_division,
        )
        total_bins = float(self._result.num_bins)
        subdivision_path = QPainterPath()
        beat_path = QPainterPath()
        bar_path = QPainterPath()
        bar_lines: list[GridLine] = []

        for line in lines:
            if line.kind is GridLineKind.BAR:
                path = bar_path
                bar_lines.append(line)
            elif line.kind is GridLineKind.BEAT:
                path = beat_path
            else:
                path = subdivision_path
            path.moveTo(line.seconds, 0.0)
            path.lineTo(line.seconds, total_bins)

        self._grid_subdivision_path_item.setPath(subdivision_path)
        self._grid_beat_path_item.setPath(beat_path)
        self._grid_bar_path_item.setPath(bar_path)
        self._grid_subdivision_path_item.setVisible(not subdivision_path.isEmpty())
        self._grid_beat_path_item.setVisible(not beat_path.isEmpty())
        self._grid_bar_path_item.setVisible(not bar_path.isEmpty())

        self._clear_grid_label_items()
        if bar_lines:
            step = max(1, math.ceil(len(bar_lines) / self._max_grid_label_count))
            for index, line in enumerate(bar_lines):
                if index % step != 0:
                    continue
                item = QGraphicsSimpleTextItem(line.label or str(line.bar_number))
                font = item.font()
                font.setPointSize(9)
                font.setBold(True)
                item.setFont(font)
                item.setBrush(QBrush(QColor(255, 236, 179, 220)))
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                item.setZValue(5)
                item.setData(0, float(line.seconds))
                self.addItem(item, ignoreBounds=True)
                self._grid_label_items.append(item)
        self._refresh_grid_label_positions()

    def _refresh_grid_label_positions(self) -> None:
        if self._result is None:
            return
        state = self.current_view_state()
        y_position = max(0.0, min(float(self._result.num_bins) - 0.5, state.y_max - 0.65))
        for item in self._grid_label_items:
            item.setPos(float(item.data(0)) + 0.02, y_position)

    def _create_band_item(self, *, fill_color: QColor, border_color: QColor) -> QGraphicsRectItem:
        item = QGraphicsRectItem()
        item.setBrush(QBrush(fill_color))
        item.setPen(QPen(border_color, 1))
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setZValue(10)
        item.hide()
        return item

    def _ensure_harmonic_band_items(self, count: int) -> None:
        while len(self._harmonic_band_items) < count:
            item = self._create_band_item(
                fill_color=QColor(255, 158, 61, 80),
                border_color=QColor(255, 158, 61, 180),
            )
            self._harmonic_band_items.append(item)
            self.addItem(item, ignoreBounds=True)

    def _hide_all_hover_bands(self) -> None:
        self._primary_band_item.hide()
        for item in self._harmonic_band_items:
            item.hide()

    def _band_rect_for_bin(self, bin_index: int) -> QRectF:
        return QRectF(0.0, float(bin_index), self._duration_seconds, 1.0)

    def _refresh_hover_highlights(self) -> None:
        if self._last_hover_info is None or self._result is None:
            return
        refreshed = self._build_hover_info(
            time_seconds=self._last_hover_info.time_seconds,
            bin_index=self._last_hover_info.bin_index,
        )
        self._apply_hover_bands(refreshed)
        self._last_hover_info = refreshed
        self.hover_changed.emit(refreshed)

    def _build_hover_info(self, *, time_seconds: float, bin_index: int) -> HoverInfo:
        if self._result is None:
            raise ValueError("当前没有频谱结果")

        frequency_hz = float(self._result.bin_frequencies[bin_index])
        note_name = frequency_to_note_name(frequency_hz)
        harmonic_indices = harmonic_bin_indices(
            self._result.bin_frequencies,
            bin_index,
            harmonic_count=self._harmonic_count,
        )
        if not self._harmonics_enabled:
            harmonic_indices = harmonic_indices[:1]

        harmonic_bins = tuple(index for index in harmonic_indices[1:] if index != bin_index)
        return HoverInfo(
            time_seconds=max(0.0, float(time_seconds)),
            bin_index=bin_index,
            frequency_hz=frequency_hz,
            note_name=note_name,
            harmonic_bins=harmonic_bins,
        )

    def _apply_hover_bands(self, hover_info: HoverInfo) -> None:
        self._primary_band_item.setRect(self._band_rect_for_bin(hover_info.bin_index))
        self._primary_band_item.show()

        self._ensure_harmonic_band_items(len(hover_info.harmonic_bins))
        for item, harmonic_bin in zip(self._harmonic_band_items, hover_info.harmonic_bins, strict=False):
            item.setRect(self._band_rect_for_bin(harmonic_bin))
            item.show()

        for item in self._harmonic_band_items[len(hover_info.harmonic_bins) :]:
            item.hide()

    def _scaled_range(
        self,
        current_min: float,
        current_max: float,
        total_min: float,
        total_max: float,
        factor: float,
        anchor: float,
        min_span: float,
    ) -> tuple[float, float]:
        clamped_anchor = max(total_min, min(float(anchor), total_max))
        new_min = clamped_anchor - ((clamped_anchor - current_min) * factor)
        new_max = clamped_anchor + ((current_max - clamped_anchor) * factor)
        return self._clamp_range(new_min, new_max, total_min, total_max, min_span)

    def _clamp_range(
        self,
        start: float,
        end: float,
        total_min: float,
        total_max: float,
        min_span: float,
    ) -> tuple[float, float]:
        total_span = max(0.0, total_max - total_min)
        span = max(min_span, min(total_span, end - start))

        if total_span <= 0.0:
            return total_min, total_max

        start = max(total_min, min(float(start), total_max - span))
        end = start + span
        if end > total_max:
            end = total_max
            start = end - span
        return start, end

    def _set_view_range(self, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        if self._result is None:
            return
        clamped_x_min, clamped_x_max = self._clamp_range(x_min, x_max, 0.0, self._duration_seconds, self._min_x_span)
        clamped_y_min, clamped_y_max = self._clamp_range(y_min, y_max, 0.0, float(self._result.num_bins), self._min_y_span)
        self.getViewBox().setRange(
            xRange=(clamped_x_min, clamped_x_max),
            yRange=(clamped_y_min, clamped_y_max),
            padding=0.0,
            disableAutoRange=True,
        )

    def _on_mouse_moved(self, scene_position) -> None:
        if self._result is None:
            return
        if not self.sceneBoundingRect().contains(scene_position):
            return

        point = self.getViewBox().mapSceneToView(scene_position)
        if point.x() < 0.0 or point.x() > self._max_time() or point.y() < 0.0 or point.y() > float(self._result.num_bins):
            return

        bin_index = bin_index_from_plot_y(point.y(), self._result.num_bins)
        hover_info = self._build_hover_info(time_seconds=float(point.x()), bin_index=bin_index)
        self._apply_hover_bands(hover_info)
        self._last_hover_info = hover_info
        self.hover_changed.emit(hover_info)

    def mousePressEvent(self, event) -> None:
        if self._midi_editor_state.enabled:
            pointer_event = self._map_mouse_event_to_pointer_event(event, clamp=False)
            if event.button() == Qt.MouseButton.LeftButton and pointer_event is not None:
                self.midi_editor_pointer_pressed.emit(pointer_event)
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton and pointer_event is not None:
                self.midi_editor_context_menu_requested.emit(
                    MidiEditorContextMenuRequest(
                        pointer_event=pointer_event,
                        global_pos=event.globalPosition().toPoint(),
                    )
                )
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            scene_position = self.mapToScene(event.position().toPoint())
            self._emit_click_interaction(scene_position)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._midi_editor_state.enabled and event.buttons() & Qt.MouseButton.LeftButton:
            pointer_event = self._map_mouse_event_to_pointer_event(event, clamp=True)
            if pointer_event is not None:
                self.midi_editor_pointer_moved.emit(pointer_event)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._midi_editor_state.enabled and event.button() == Qt.MouseButton.LeftButton:
            pointer_event = self._map_mouse_event_to_pointer_event(event, clamp=True)
            if pointer_event is not None:
                self.midi_editor_pointer_released.emit(pointer_event)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _emit_click_interaction(self, scene_position) -> None:
        if self._result is None:
            return
        if not self.sceneBoundingRect().contains(scene_position):
            return

        point = self.getViewBox().mapSceneToView(scene_position)
        if point.x() < 0.0 or point.x() > self._max_time() or point.y() < 0.0 or point.y() > float(self._result.num_bins):
            return

        bin_index = bin_index_from_plot_y(point.y(), self._result.num_bins)
        hover_info = self._build_hover_info(time_seconds=float(point.x()), bin_index=bin_index)
        self._apply_hover_bands(hover_info)
        self._last_hover_info = hover_info
        self.hover_changed.emit(hover_info)
        self.note_audition_requested.emit(hover_info)
        if not self._seek_on_click_enabled:
            return
        target = max(0.0, min(float(point.x()), self._max_time()))
        self.seek_requested.emit(target)

    def _map_mouse_event_to_pointer_event(self, event, *, clamp: bool) -> MidiEditorPointerEvent | None:
        if self._result is None:
            return None
        scene_position = self.mapToScene(event.position().toPoint())
        if not clamp and not self.sceneBoundingRect().contains(scene_position):
            return None

        point = self.getViewBox().mapSceneToView(scene_position)
        seconds = float(point.x())
        plot_y = float(point.y())
        if clamp:
            seconds = max(0.0, min(seconds, self._max_time()))
            plot_y = max(0.0, min(plot_y, float(self._result.num_bins) - 1e-6))
        elif seconds < 0.0 or seconds > self._max_time() or plot_y < 0.0 or plot_y > float(self._result.num_bins):
            return None

        return MidiEditorPointerEvent(
            seconds=seconds,
            plot_y=plot_y,
            midi_pitch=self.plot_y_to_midi_pitch(plot_y),
            modifiers=event.modifiers(),
        )

    def _on_view_box_range_changed(self, *_args) -> None:
        if self._grid_visible:
            self._refresh_grid_label_positions()
        self.view_state_changed.emit(self.current_view_state())
