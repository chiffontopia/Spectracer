from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QBrush, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from spectracer.core.harmonics import nearest_bin_index
from spectracer.core.models import CqtResult
from spectracer.midi.editor_model import MidiEditorState, MidiNote
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession

_NOTE_LAYER_Z = 6.0
_SELECTION_LAYER_Z = 7.0
_PREVIEW_LAYER_Z = 8.0
_DEFAULT_CHANNEL_COLOR = "#4FC3F7"
_SELECTED_BORDER_COLOR = QColor(255, 255, 255, 235)
_SELECTION_RECT_COLOR = QColor(255, 255, 255, 200)
_PREVIEW_RECT_COLOR = QColor(255, 255, 255, 180)


@dataclass(slots=True, frozen=True)
class MidiNoteGeometry:
    note: MidiNote
    rect: QRectF
    channel_color: QColor
    fill_color: QColor
    border_color: QColor
    is_selected: bool


class _MidiNoteLayerItem(QGraphicsItem):
    def __init__(self) -> None:
        super().__init__()
        self._bounds = QRectF()
        self._geometries: tuple[MidiNoteGeometry, ...] = ()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(_NOTE_LAYER_Z)
        self.hide()

    def boundingRect(self) -> QRectF:  # noqa: D401
        return QRectF(self._bounds)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # noqa: D401
        if not self._geometries:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for geometry in self._geometries:
            pen = QPen(geometry.border_color, 2.0 if geometry.is_selected else 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QBrush(geometry.fill_color))
            painter.drawRect(geometry.rect)

    def set_geometries(self, geometries: tuple[MidiNoteGeometry, ...], bounds: QRectF | None = None) -> None:
        normalized_bounds = QRectF() if bounds is None else QRectF(bounds)
        self.prepareGeometryChange()
        self._geometries = tuple(geometries)
        self._bounds = normalized_bounds
        self.update()


def midi_note_to_frequency(midi_note: float, *, a4_hz: float = 440.0) -> float:
    normalized_note = float(midi_note)
    if not math.isfinite(normalized_note):
        raise ValueError("midi_note 必须是有限数值")
    return float(a4_hz) * (2.0 ** ((normalized_note - 69.0) / 12.0))


def _coerce_color(raw: str | QColor | None, *, fallback: str = _DEFAULT_CHANNEL_COLOR) -> QColor:
    if isinstance(raw, QColor):
        color = QColor(raw)
    elif raw is None:
        color = QColor(fallback)
    else:
        color = QColor(str(raw).strip() or fallback)
    if not color.isValid():
        color = QColor(fallback)
    return color


def _with_alpha(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(max(0, min(255, int(alpha))))
    return tinted


def _rect_union(rects: tuple[QRectF, ...]) -> QRectF:
    if not rects:
        return QRectF()
    merged = QRectF(rects[0])
    for rect in rects[1:]:
        merged = merged.united(rect)
    return merged


def _create_preview_item(*, color: QColor, fill_alpha: int, z_value: float) -> QGraphicsRectItem:
    item = QGraphicsRectItem()
    pen = QPen(color, 1.0, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)
    item.setPen(pen)
    item.setBrush(QBrush(_with_alpha(color, fill_alpha)))
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setZValue(z_value)
    item.hide()
    return item


class MidiNoteOverlay:
    def __init__(self) -> None:
        self._session = MidiSession()
        self._timeline = MidiGridTimeline.constant()
        self._editor_state = self._session.editor_state
        self._result: CqtResult | None = None
        self._duration_seconds = 0.0
        self._visible = True
        self._geometry_by_note_id: dict[str, MidiNoteGeometry] = {}
        self._selection_rect: QRectF | None = None
        self._preview_rect: QRectF | None = None

        self._note_layer_item = _MidiNoteLayerItem()
        self._selection_rect_item = _create_preview_item(
            color=_SELECTION_RECT_COLOR,
            fill_alpha=28,
            z_value=_SELECTION_LAYER_Z,
        )
        self._preview_rect_item = _create_preview_item(
            color=_PREVIEW_RECT_COLOR,
            fill_alpha=48,
            z_value=_PREVIEW_LAYER_Z,
        )
        self._attach_session_signals(self._session)

    def graphics_items(self) -> tuple[QGraphicsItem, ...]:
        return (self._note_layer_item, self._selection_rect_item, self._preview_rect_item)

    def set_result(self, result: CqtResult | None, *, duration_seconds: float | None = None) -> None:
        if result is not None and not isinstance(result, CqtResult):
            raise TypeError("result 必须是 CqtResult 或 None")
        self._result = result
        if result is None:
            self._duration_seconds = 0.0
        elif duration_seconds is None:
            self._duration_seconds = max(0.0, float(result.duration_seconds))
        else:
            self._duration_seconds = max(0.0, float(duration_seconds))
        self._refresh_geometry_cache()

    def set_session(self, session: MidiSession | None) -> None:
        if session is None:
            session = MidiSession()
        if not isinstance(session, MidiSession):
            raise TypeError("session 必须是 MidiSession")
        if session is self._session:
            self._refresh_geometry_cache()
            return
        self._detach_session_signals(self._session)
        self._session = session
        self._attach_session_signals(self._session)
        self._refresh_geometry_cache()

    def set_timeline(self, timeline: MidiGridTimeline) -> None:
        if not isinstance(timeline, MidiGridTimeline):
            raise TypeError("timeline 必须是 MidiGridTimeline")
        self._timeline = timeline
        self._refresh_geometry_cache()

    def set_editor_state(self, editor_state: MidiEditorState) -> None:
        if not isinstance(editor_state, MidiEditorState):
            raise TypeError("editor_state 必须是 MidiEditorState")
        self._editor_state = editor_state
        self._refresh_geometry_cache()

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._update_visibility()

    def is_visible(self) -> bool:
        return self._note_layer_item.isVisible()

    def clear(self) -> None:
        self.set_result(None)

    def geometries(self) -> tuple[MidiNoteGeometry, ...]:
        return tuple(self._geometry_by_note_id.values())

    def note_geometry(self, note_id: str) -> MidiNoteGeometry | None:
        normalized = str(note_id).strip()
        if not normalized:
            return None
        return self._geometry_by_note_id.get(normalized)

    def note_rect(self, note_id: str) -> QRectF | None:
        geometry = self.note_geometry(note_id)
        if geometry is None:
            return None
        return QRectF(geometry.rect)

    def pitch_band_for_pitch(self, pitch: int | float) -> tuple[float, float] | None:
        if self._result is None:
            return None
        frequencies = np.asarray(self._result.bin_frequencies, dtype=np.float64)
        if frequencies.ndim != 1 or frequencies.size == 0:
            return None

        normalized_pitch = float(pitch)
        center_frequency = midi_note_to_frequency(normalized_pitch)
        lower_frequency = midi_note_to_frequency(normalized_pitch - 0.5)
        upper_frequency = midi_note_to_frequency(normalized_pitch + 0.5)

        start_index = int(np.searchsorted(frequencies, lower_frequency, side="left"))
        end_index = int(np.searchsorted(frequencies, upper_frequency, side="right"))

        if start_index >= frequencies.size:
            start_index = int(frequencies.size - 1)
        else:
            start_index = max(0, start_index)

        end_index = max(start_index + 1, min(end_index, int(frequencies.size)))
        if end_index <= start_index:
            nearest_index = nearest_bin_index(frequencies, center_frequency)
            start_index = nearest_index
            end_index = min(int(frequencies.size), nearest_index + 1)

        return float(start_index), float(end_index)

    def pitch_to_plot_y(self, pitch: int | float) -> float | None:
        band = self.pitch_band_for_pitch(pitch)
        if band is None:
            return None
        return (band[0] + band[1]) * 0.5

    def hit_test(
        self,
        seconds: float,
        plot_y: float,
        *,
        channels: list[int] | tuple[int, ...] | set[int] | frozenset[int] | None = None,
    ) -> MidiNote | None:
        if not self._visible or self._result is None:
            return None
        allowed_channels = None if channels is None else {int(channel) for channel in channels}
        test_x = float(seconds)
        test_y = float(plot_y)
        for geometry in reversed(tuple(self._geometry_by_note_id.values())):
            if allowed_channels is not None and geometry.note.channel not in allowed_channels:
                continue
            if geometry.rect.contains(test_x, test_y):
                return geometry.note
        return None

    def set_selection_rect(self, rect: QRectF | None) -> None:
        self._selection_rect = None if rect is None else QRectF(rect)
        self._selection_rect_item.setRect(QRectF() if rect is None else QRectF(rect))
        self._update_visibility()

    def selection_rect(self) -> QRectF | None:
        if self._selection_rect is None:
            return None
        return QRectF(self._selection_rect)

    def set_preview_rect(self, rect: QRectF | None) -> None:
        self._preview_rect = None if rect is None else QRectF(rect)
        self._preview_rect_item.setRect(QRectF() if rect is None else QRectF(rect))
        self._update_visibility()

    def preview_rect(self) -> QRectF | None:
        if self._preview_rect is None:
            return None
        return QRectF(self._preview_rect)

    def _refresh_geometry_cache(self) -> None:
        if self._result is None:
            self._geometry_by_note_id.clear()
            self._note_layer_item.set_geometries(())
            self._update_visibility()
            return

        geometries: list[MidiNoteGeometry] = []
        for note in self._session.notes:
            geometry = self._build_note_geometry(note)
            if geometry is None:
                continue
            geometries.append(geometry)

        geometries.sort(key=lambda item: (item.is_selected, item.note.start_beat, item.note.pitch, item.note.channel, item.note.id))
        geometry_by_note_id = {geometry.note.id: geometry for geometry in geometries}
        bounds = _rect_union(tuple(geometry.rect for geometry in geometries))
        self._geometry_by_note_id = geometry_by_note_id
        self._note_layer_item.set_geometries(tuple(geometries), bounds)
        self._update_visibility()

    def _build_note_geometry(self, note: MidiNote) -> MidiNoteGeometry | None:
        pitch_band = self.pitch_band_for_pitch(note.pitch)
        if pitch_band is None:
            return None

        start_seconds = float(self._timeline.beat_to_seconds(note.start_beat))
        end_seconds = float(self._timeline.beat_to_seconds(note.end_beat))
        rect = QRectF(
            min(start_seconds, end_seconds),
            pitch_band[0],
            max(1e-9, abs(end_seconds - start_seconds)),
            max(1.0, pitch_band[1] - pitch_band[0]),
        )

        channel_config = self._session.get_channel_config(note.channel)
        channel_color = _coerce_color(channel_config.color)
        is_selected = note.id in self._session.selected_note_ids

        if is_selected:
            fill_color = _with_alpha(channel_color.lighter(150), 188)
            border_color = QColor(_SELECTED_BORDER_COLOR)
        else:
            fill_color = _with_alpha(channel_color, 112)
            border_color = _with_alpha(channel_color.lighter(130), 220)

        return MidiNoteGeometry(
            note=note,
            rect=rect,
            channel_color=channel_color,
            fill_color=fill_color,
            border_color=border_color,
            is_selected=is_selected,
        )

    def _update_visibility(self) -> None:
        overlay_available = self._visible and self._result is not None
        self._note_layer_item.setVisible(overlay_available and bool(self._geometry_by_note_id))
        self._selection_rect_item.setVisible(overlay_available and self._selection_rect is not None)
        self._preview_rect_item.setVisible(overlay_available and self._preview_rect is not None)

    def _attach_session_signals(self, session: MidiSession) -> None:
        session.notes_changed.connect(self._refresh_geometry_cache)
        session.selection_changed.connect(self._refresh_geometry_cache)
        session.channel_configs_changed.connect(self._refresh_geometry_cache)

    def _detach_session_signals(self, session: MidiSession) -> None:
        for signal in (
            session.notes_changed,
            session.selection_changed,
            session.channel_configs_changed,
        ):
            try:
                signal.disconnect(self._refresh_geometry_cache)
            except (TypeError, RuntimeError):
                continue


__all__ = ["MidiNoteGeometry", "MidiNoteOverlay", "midi_note_to_frequency"]
