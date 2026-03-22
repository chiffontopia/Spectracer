from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QKeyEvent, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from spectracer.midi.editor_model import EventTrackLane, EventTrackSelection
from spectracer.midi.grid import MidiGridTimeline, TempoEvent, TempoTransition, TimeSignatureEvent

EVENT_TRACK_HEIGHT = 104
EVENT_TRACK_LABELS_MIN_WIDTH = 96
_EVENT_BADGE_MAX_WIDTH = 176
_EVENT_BADGE_HEIGHT = 18.0
_EVENT_BADGE_GAP = 6.0
_EVENT_CHIP_LEVELS = (6.0, 28.0)
_EVENT_MARKER_TOLERANCE = 6.0
_EVENT_DRAG_MARGIN_BEATS = 1e-6

_TEMPO_ACCENT = QColor(79, 195, 247, 220)
_TEMPO_FILL = QColor(79, 195, 247, 40)
_METER_ACCENT = QColor(129, 199, 132, 220)
_METER_FILL = QColor(129, 199, 132, 40)
_CURSOR_COLOR = QColor(255, 214, 79, 220)
_PANEL_BORDER = QColor(255, 255, 255, 34)
_DIVIDER_COLOR = QColor(255, 255, 255, 28)
_BACKGROUND = QColor(20, 22, 28)
_TEMPO_LANE_BG = QColor(24, 30, 38)
_METER_LANE_BG = QColor(22, 26, 24)
_EMPTY_TEXT = QColor(255, 255, 255, 96)
_LABEL_TEXT = QColor(255, 255, 255, 180)
_SELECTION_TEXT = QColor(255, 255, 255, 248)


@dataclass(slots=True)
class _Badge:
    handle: EventTrackSelection
    seconds: float
    text: str
    accent: QColor
    fill: QColor
    edge_pinned: bool = False


@dataclass(slots=True)
class _BadgeLayout:
    handle: EventTrackSelection
    rect: QRectF
    anchor_x: float | None
    text: str
    accent: QColor
    fill: QColor


def _format_number(value: float) -> str:
    rounded = round(float(value))
    if abs(float(value) - rounded) <= 1e-9:
        return str(int(rounded))
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def describe_tempo_event(timeline: MidiGridTimeline, event: TempoEvent) -> str:
    bar_number = timeline.bar_position_at_beat(event.beat_position).bar_number
    transition_suffix = " → Linear" if event.transition == TempoTransition.LINEAR else ""
    return f"{_format_number(event.bpm)} BPM{transition_suffix} · 小节 {bar_number}"


def describe_time_signature_event(timeline: MidiGridTimeline, event: TimeSignatureEvent) -> str:
    bar_number = timeline.bar_position_at_beat(event.beat_position).bar_number
    signature = event.time_signature
    return f"{signature.numerator}/{signature.denominator} · 小节 {bar_number}"


class EventTrackLaneLabels(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(EVENT_TRACK_LABELS_MIN_WIDTH)
        self.setMaximumWidth(EVENT_TRACK_LABELS_MIN_WIDTH)
        self.setMinimumHeight(EVENT_TRACK_HEIGHT)
        self.setMaximumHeight(EVENT_TRACK_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(EVENT_TRACK_LABELS_MIN_WIDTH, EVENT_TRACK_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: D401
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        top_lane, bottom_lane = _split_lanes(outer)

        painter.fillRect(top_lane, _TEMPO_LANE_BG)
        painter.fillRect(bottom_lane, _METER_LANE_BG)
        painter.setPen(QPen(_PANEL_BORDER, 1.0))
        painter.drawRect(outer)
        painter.setPen(QPen(_DIVIDER_COLOR, 1.0))
        painter.drawLine(QPointF(outer.left(), top_lane.bottom()), QPointF(outer.right(), top_lane.bottom()))

        painter.setPen(_LABEL_TEXT)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(top_lane, Qt.AlignmentFlag.AlignCenter, "Tempo")
        painter.drawText(bottom_lane, Qt.AlignmentFlag.AlignCenter, "Meter")


class GridEventTrackWidget(QWidget):
    create_requested = pyqtSignal(str, float)
    edit_requested = pyqtSignal(object)
    delete_requested = pyqtSignal(object)
    move_requested = pyqtSignal(object, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._timeline = MidiGridTimeline.constant()
        self._duration_seconds = 0.0
        self._view_start_seconds = 0.0
        self._view_end_seconds = 0.0
        self._total_seconds = 0.0
        self._cursor_seconds = 0.0
        self._selected_event: EventTrackSelection | None = None
        self._snap_enabled = True
        self._pressed_selection: EventTrackSelection | None = None
        self._pressed_position: QPointF | None = None
        self._drag_selection: EventTrackSelection | None = None
        self._drag_preview_beat: float | None = None
        self._drag_preview_timeline: MidiGridTimeline | None = None

        self.setMinimumHeight(EVENT_TRACK_HEIGHT)
        self.setMaximumHeight(EVENT_TRACK_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip(
            "双击空白区域可新增事件，双击事件可编辑，可直接拖动非起始事件；"
            "Delete / Backspace 可删除选中事件，右键可打开快捷菜单。"
        )

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(520, EVENT_TRACK_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: D401
        return QSize(200, EVENT_TRACK_HEIGHT)

    def selected_event(self) -> EventTrackSelection | None:
        return self._selected_event

    def set_selected_event(self, selection: EventTrackSelection | None) -> None:
        normalized = selection if self._selection_exists(selection) else None
        if normalized == self._selected_event:
            return
        self._selected_event = normalized
        self.update()

    def clear_selection(self) -> None:
        self.set_selected_event(None)

    def set_snap_enabled(self, enabled: bool) -> None:
        self._snap_enabled = bool(enabled)

    def clear(self) -> None:
        self._cancel_pointer_interaction()
        self._duration_seconds = 0.0
        self._view_start_seconds = 0.0
        self._view_end_seconds = 0.0
        self._total_seconds = 0.0
        self._cursor_seconds = 0.0
        self._selected_event = None
        self.update()

    def set_grid_timeline(self, timeline: MidiGridTimeline) -> None:
        self._cancel_pointer_interaction()
        self._timeline = timeline
        if not self._selection_exists(self._selected_event):
            self._selected_event = None
        self.update()

    def set_duration_seconds(self, duration_seconds: float) -> None:
        self._duration_seconds = max(0.0, float(duration_seconds))
        if self._total_seconds <= 0.0:
            self._total_seconds = self._duration_seconds
        if self._view_end_seconds <= self._view_start_seconds:
            self._view_start_seconds = 0.0
            self._view_end_seconds = self._duration_seconds
        self.update()

    def set_view_window(self, start_seconds: float, end_seconds: float, total_seconds: float) -> None:
        total = max(0.0, float(total_seconds))
        if total <= 0.0:
            total = self._duration_seconds

        start = max(0.0, float(start_seconds))
        end = max(start, float(end_seconds))
        if total > 0.0:
            start = min(start, total)
            end = min(max(end, start), total)
            if end <= start:
                start = 0.0
                end = total

        self._view_start_seconds = start
        self._view_end_seconds = end
        self._total_seconds = total
        self.update()

    def set_cursor_seconds(self, seconds: float) -> None:
        self._cursor_seconds = max(0.0, float(seconds))
        self.update()

    def active_event_at_beat(self, lane: EventTrackLane, beat: float) -> TempoEvent | TimeSignatureEvent | None:
        events = self._events_for_lane(lane)
        if not events:
            return None
        beat_value = float(beat)
        active_event: TempoEvent | TimeSignatureEvent | None = events[0]
        for candidate in events:
            if float(candidate.beat_position) > beat_value:
                break
            active_event = candidate
        return active_event

    def event_handle_at_position(self, position: QPointF) -> EventTrackSelection | None:
        lane = self._lane_at_position(position)
        if lane is None:
            return None

        view_start, view_end, total = self._effective_window()
        if total <= 0.0 or (view_end - view_start) <= 1e-9:
            return None

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        tempo_lane, meter_lane = _split_lanes(outer)
        lane_rect = tempo_lane if lane == EventTrackLane.TEMPO else meter_lane
        layouts = self._badge_layouts_for_lane(lane, lane_rect, view_start, view_end)
        for layout in reversed(layouts):
            if layout.rect.adjusted(-4.0, -4.0, 4.0, 4.0).contains(position):
                return layout.handle
            if layout.anchor_x is None:
                continue
            marker_top = layout.rect.bottom()
            marker_bottom = lane_rect.bottom() - 5.0
            if marker_top <= position.y() <= marker_bottom and abs(position.x() - layout.anchor_x) <= _EVENT_MARKER_TOLERANCE:
                return layout.handle
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        tempo_lane, meter_lane = _split_lanes(outer)

        painter.fillRect(outer, _BACKGROUND)
        painter.fillRect(tempo_lane, _TEMPO_LANE_BG)
        painter.fillRect(meter_lane, _METER_LANE_BG)
        painter.setPen(QPen(_PANEL_BORDER, 1.0))
        painter.drawRect(outer)
        painter.setPen(QPen(_DIVIDER_COLOR, 1.0))
        painter.drawLine(QPointF(outer.left(), tempo_lane.bottom()), QPointF(outer.right(), tempo_lane.bottom()))

        view_start, view_end, total = self._effective_window()
        if total <= 0.0 or (view_end - view_start) <= 1e-9:
            painter.setPen(_EMPTY_TEXT)
            painter.drawText(outer, Qt.AlignmentFlag.AlignCenter, "Tempo / Meter 事件轨道（待加载音频）")
            return

        self._draw_tempo_lane(painter, tempo_lane, view_start, view_end)
        self._draw_meter_lane(painter, meter_lane, view_start, view_end)
        self._draw_cursor(painter, outer, view_start, view_end)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._cancel_drag_preview(update_widget=False)
            selection = self.event_handle_at_position(event.position())
            self.set_selected_event(selection)
            if selection is not None and not selection.is_root_event:
                self._pressed_selection = selection
                self._pressed_position = QPointF(event.position())
            else:
                self._pressed_selection = None
                self._pressed_position = None
            self._update_hover_cursor(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._cancel_pointer_interaction()
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        selection = self.event_handle_at_position(event.position())
        if selection is not None:
            self.set_selected_event(selection)
            self.edit_requested.emit(selection)
            event.accept()
            return

        lane = self._lane_at_position(event.position())
        beat = self._beat_for_position(event.position())
        if lane is None or beat is None:
            super().mouseDoubleClickEvent(event)
            return

        self.create_requested.emit(lane.value, beat)
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self._cancel_pointer_interaction()
        position = QPointF(event.pos())
        lane = self._lane_at_position(position)
        beat = self._beat_for_position(position)
        if lane is None or beat is None:
            super().contextMenuEvent(event)
            return

        selection = self.event_handle_at_position(position)
        if selection is not None:
            self.set_selected_event(selection)

        menu = QMenu(self)
        add_action = menu.addAction(f"在此新增 {lane.display_name} 事件")
        edit_action = None
        delete_action = None
        if selection is not None:
            edit_action = menu.addAction(f"编辑 {selection.lane.display_name} 事件")
            if not selection.is_root_event:
                delete_action = menu.addAction(f"删除 {selection.lane.display_name} 事件")

        chosen_action = menu.exec(event.globalPos())
        if chosen_action == add_action:
            self.create_requested.emit(lane.value, beat)
            return
        if edit_action is not None and chosen_action == edit_action:
            self.edit_requested.emit(selection)
            return
        if delete_action is not None and chosen_action == delete_action:
            self.delete_requested.emit(selection)
            return

        super().contextMenuEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected_event is not None and not self._selected_event.is_root_event:
                self.delete_requested.emit(self._selected_event)
                event.accept()
                return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._pressed_selection is not None
            and self._pressed_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            if self._drag_selection is None:
                delta = event.position() - self._pressed_position
                if (abs(delta.x()) + abs(delta.y())) >= float(QApplication.startDragDistance()):
                    self._drag_selection = self._pressed_selection
            if self._drag_selection is not None:
                self._update_drag_preview(event.position())
                event.accept()
                return

        self._update_hover_cursor(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            selection = self._drag_selection
            target_beat = self._drag_preview_beat
            original_event = self._event_for_selection(selection) if selection is not None else None
            self._cancel_pointer_interaction()
            self._update_hover_cursor(event.position())
            if (
                selection is not None
                and target_beat is not None
                and original_event is not None
                and abs(float(original_event.beat_position) - float(target_beat)) > _EVENT_DRAG_MARGIN_BEATS
            ):
                self.move_requested.emit(selection, float(target_beat))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._drag_selection is None:
            self.unsetCursor()
        super().leaveEvent(_event)

    def _effective_window(self) -> tuple[float, float, float]:
        total = max(self._duration_seconds, self._total_seconds)
        if total <= 0.0:
            return 0.0, 0.0, 0.0

        start = max(0.0, min(self._view_start_seconds, total))
        end = max(start, min(self._view_end_seconds, total))
        if end <= start:
            start = 0.0
            end = total
        return start, end, total

    def _draw_tempo_lane(self, painter: QPainter, lane: QRectF, view_start: float, view_end: float) -> None:
        timeline = self._display_timeline()
        events = timeline.tempo_events
        if not events:
            return

        graph_rect = lane.adjusted(6.0, 22.0, -6.0, -8.0)
        bpm_values = [float(event.bpm) for event in events]
        min_bpm = min(bpm_values)
        max_bpm = max(bpm_values)
        bpm_span = max(1.0, max_bpm - min_bpm)
        padded_min = min_bpm - (bpm_span * 0.15)
        padded_max = max_bpm + (bpm_span * 0.15)
        if abs(padded_max - padded_min) <= 1e-9:
            padded_min -= 1.0
            padded_max += 1.0

        painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
        painter.drawLine(
            QPointF(graph_rect.left(), graph_rect.center().y()),
            QPointF(graph_rect.right(), graph_rect.center().y()),
        )

        segment_pen = QPen(_TEMPO_ACCENT, 2.0)
        segment_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(segment_pen)
        for index, event in enumerate(events):
            next_event = events[index + 1] if index + 1 < len(events) else None
            event_seconds = timeline.beat_to_seconds(event.beat_position)
            next_seconds = timeline.beat_to_seconds(next_event.beat_position) if next_event is not None else view_end

            segment_start_seconds = max(view_start, event_seconds)
            segment_end_seconds = min(view_end, next_seconds)
            if segment_end_seconds <= view_start or segment_start_seconds >= view_end:
                continue

            start_beat = timeline.seconds_to_beat(segment_start_seconds)
            end_beat = timeline.seconds_to_beat(segment_end_seconds)
            start_bpm = _tempo_value_for_segment(event, next_event, start_beat)
            end_bpm = _tempo_value_for_segment(event, next_event, end_beat)
            start_point = QPointF(
                _x_for_seconds(segment_start_seconds, lane, view_start, view_end),
                _y_for_value(start_bpm, graph_rect, padded_min, padded_max),
            )
            end_point = QPointF(
                _x_for_seconds(segment_end_seconds, lane, view_start, view_end),
                _y_for_value(end_bpm, graph_rect, padded_min, padded_max),
            )
            painter.drawLine(start_point, end_point)

            if (
                next_event is not None
                and event.transition == TempoTransition.STEP
                and view_start <= next_seconds <= view_end
            ):
                jump_x = _x_for_seconds(next_seconds, lane, view_start, view_end)
                painter.drawLine(
                    QPointF(jump_x, _y_for_value(event.bpm, graph_rect, padded_min, padded_max)),
                    QPointF(jump_x, _y_for_value(next_event.bpm, graph_rect, padded_min, padded_max)),
                )

        self._draw_badge_layouts(
            painter,
            lane,
            self._badge_layouts_for_lane(EventTrackLane.TEMPO, lane, view_start, view_end),
        )

    def _draw_meter_lane(self, painter: QPainter, lane: QRectF, view_start: float, view_end: float) -> None:
        timeline = self._display_timeline()
        events = timeline.time_signature_events
        if not events:
            return

        center_y = lane.center().y() + 8.0
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
        painter.drawLine(QPointF(lane.left() + 6.0, center_y), QPointF(lane.right() - 6.0, center_y))

        meter_pen = QPen(_METER_ACCENT, 2.0)
        meter_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(meter_pen)
        for index, event in enumerate(events):
            next_event = events[index + 1] if index + 1 < len(events) else None
            event_seconds = timeline.beat_to_seconds(event.beat_position)
            next_seconds = timeline.beat_to_seconds(next_event.beat_position) if next_event is not None else view_end

            segment_start_seconds = max(view_start, event_seconds)
            segment_end_seconds = min(view_end, next_seconds)
            if segment_end_seconds <= view_start or segment_start_seconds >= view_end:
                continue

            painter.drawLine(
                QPointF(_x_for_seconds(segment_start_seconds, lane, view_start, view_end), center_y),
                QPointF(_x_for_seconds(segment_end_seconds, lane, view_start, view_end), center_y),
            )

            if next_event is not None and view_start <= next_seconds <= view_end:
                jump_x = _x_for_seconds(next_seconds, lane, view_start, view_end)
                painter.drawLine(QPointF(jump_x, lane.top() + 22.0), QPointF(jump_x, lane.bottom() - 8.0))

        self._draw_badge_layouts(
            painter,
            lane,
            self._badge_layouts_for_lane(EventTrackLane.METER, lane, view_start, view_end),
        )

    def _badge_layouts_for_lane(
        self,
        lane_kind: EventTrackLane,
        lane_rect: QRectF,
        view_start: float,
        view_end: float,
    ) -> list[_BadgeLayout]:
        if lane_kind == EventTrackLane.TEMPO:
            badges = self._build_tempo_badges(view_start, view_end)
        else:
            badges = self._build_meter_badges(view_start, view_end)
        return self._layout_badges(lane_rect, badges, view_start, view_end)

    def _build_tempo_badges(self, view_start: float, view_end: float) -> list[_Badge]:
        timeline = self._display_timeline()
        events = timeline.tempo_events
        if not events:
            return []

        active_event = timeline.tempo_event_at_beat(timeline.seconds_to_beat(view_start))
        active_seconds = timeline.beat_to_seconds(active_event.beat_position)
        badges = [
            _Badge(
                handle=EventTrackSelection(EventTrackLane.TEMPO, index),
                seconds=timeline.beat_to_seconds(event.beat_position),
                text=describe_tempo_event(timeline, event),
                accent=_TEMPO_ACCENT,
                fill=_TEMPO_FILL,
            )
            for index, event in enumerate(events)
            if view_start <= timeline.beat_to_seconds(event.beat_position) <= view_end
        ]
        if active_seconds < (view_start - 1e-6):
            active_index = events.index(active_event)
            badges.insert(
                0,
                _Badge(
                    handle=EventTrackSelection(EventTrackLane.TEMPO, active_index),
                    seconds=view_start,
                    text=f"◀ {describe_tempo_event(timeline, active_event)}",
                    accent=_TEMPO_ACCENT,
                    fill=_TEMPO_FILL,
                    edge_pinned=True,
                ),
            )
        return badges

    def _build_meter_badges(self, view_start: float, view_end: float) -> list[_Badge]:
        timeline = self._display_timeline()
        events = timeline.time_signature_events
        if not events:
            return []

        active_event = events[0]
        start_beat = timeline.seconds_to_beat(view_start)
        for candidate in timeline.time_signature_events:
            if candidate.beat_position <= start_beat + 1e-9:
                active_event = candidate
            else:
                break

        active_seconds = timeline.beat_to_seconds(active_event.beat_position)
        badges = [
            _Badge(
                handle=EventTrackSelection(EventTrackLane.METER, index),
                seconds=timeline.beat_to_seconds(event.beat_position),
                text=describe_time_signature_event(timeline, event),
                accent=_METER_ACCENT,
                fill=_METER_FILL,
            )
            for index, event in enumerate(events)
            if view_start <= timeline.beat_to_seconds(event.beat_position) <= view_end
        ]
        if active_seconds < (view_start - 1e-6):
            active_index = events.index(active_event)
            badges.insert(
                0,
                _Badge(
                    handle=EventTrackSelection(EventTrackLane.METER, active_index),
                    seconds=view_start,
                    text=f"◀ {describe_time_signature_event(timeline, active_event)}",
                    accent=_METER_ACCENT,
                    fill=_METER_FILL,
                    edge_pinned=True,
                ),
            )
        return badges

    def _layout_badges(
        self,
        lane: QRectF,
        badges: list[_Badge],
        view_start: float,
        view_end: float,
    ) -> list[_BadgeLayout]:
        if not badges:
            return []

        metrics = self.fontMetrics()
        last_right_by_level = [lane.left() - 1000.0 for _ in _EVENT_CHIP_LEVELS]
        max_left = lane.left() + 6.0
        max_right = lane.right() - 6.0
        layouts: list[_BadgeLayout] = []

        for badge in badges:
            text = metrics.elidedText(
                badge.text,
                Qt.TextElideMode.ElideRight,
                _EVENT_BADGE_MAX_WIDTH - 14,
            )
            width = min(
                _EVENT_BADGE_MAX_WIDTH,
                max(52.0, float(metrics.horizontalAdvance(text)) + 14.0),
            )

            anchor_x: float | None = None
            if badge.edge_pinned:
                rect = QRectF(max_left, lane.top() + _EVENT_CHIP_LEVELS[0], width, _EVENT_BADGE_HEIGHT)
                level_index = 0
            else:
                anchor_x = _x_for_seconds(badge.seconds, lane, view_start, view_end)
                proposed_left = anchor_x - (width * 0.5)
                level_index = 0
                if proposed_left <= (last_right_by_level[0] + _EVENT_BADGE_GAP):
                    level_index = 1 if len(_EVENT_CHIP_LEVELS) > 1 else 0
                rect = QRectF(
                    anchor_x - (width * 0.5),
                    lane.top() + _EVENT_CHIP_LEVELS[level_index],
                    width,
                    _EVENT_BADGE_HEIGHT,
                )
                if rect.left() < max_left:
                    rect.moveLeft(max_left)
                if rect.right() > max_right:
                    rect.moveRight(max_right)

            last_right_by_level[level_index] = rect.right()
            layouts.append(
                _BadgeLayout(
                    handle=badge.handle,
                    rect=rect,
                    anchor_x=anchor_x,
                    text=text,
                    accent=badge.accent,
                    fill=badge.fill,
                )
            )

        return layouts

    def _draw_badge_layouts(self, painter: QPainter, lane: QRectF, layouts: list[_BadgeLayout]) -> None:
        for layout in layouts:
            is_selected = layout.handle == self._selected_event
            if layout.anchor_x is not None:
                marker_pen = QPen(layout.accent, 1.75 if is_selected else 1.0)
                painter.setPen(marker_pen)
                painter.drawLine(
                    QPointF(layout.anchor_x, layout.rect.bottom()),
                    QPointF(layout.anchor_x, lane.bottom() - 5.0),
                )

            painter.setPen(QPen(layout.accent, 2.0 if is_selected else 1.0))
            painter.setBrush(_selected_fill(layout.fill) if is_selected else layout.fill)
            painter.drawRoundedRect(layout.rect, 5.0, 5.0)
            if is_selected:
                text_font = painter.font()
                text_font.setBold(True)
                painter.setFont(text_font)
                painter.setPen(QPen(_SELECTION_TEXT, 1.0))
                painter.drawText(layout.rect, Qt.AlignmentFlag.AlignCenter, layout.text)
                text_font.setBold(False)
                painter.setFont(text_font)
            else:
                painter.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
                painter.drawText(layout.rect, Qt.AlignmentFlag.AlignCenter, layout.text)

    def _draw_cursor(self, painter: QPainter, outer: QRectF, view_start: float, view_end: float) -> None:
        if self._cursor_seconds < view_start or self._cursor_seconds > view_end:
            return

        x = _x_for_seconds(self._cursor_seconds, outer, view_start, view_end)
        cursor_pen = QPen(_CURSOR_COLOR, 1.5)
        cursor_pen.setCosmetic(True)
        painter.setPen(cursor_pen)
        painter.drawLine(QPointF(x, outer.top() + 1.0), QPointF(x, outer.bottom() - 1.0))

    def _selection_exists(self, selection: EventTrackSelection | None) -> bool:
        if selection is None:
            return False
        events = self._events_for_lane(selection.lane, self._timeline)
        return 0 <= selection.event_index < len(events)

    def _events_for_lane(
        self,
        lane: EventTrackLane,
        timeline: MidiGridTimeline | None = None,
    ) -> tuple[TempoEvent, ...] | tuple[TimeSignatureEvent, ...]:
        active_timeline = self._display_timeline() if timeline is None else timeline
        if lane == EventTrackLane.TEMPO:
            return tuple(active_timeline.tempo_events)
        return tuple(active_timeline.time_signature_events)

    def _lane_at_position(self, position: QPointF) -> EventTrackLane | None:
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        tempo_lane, meter_lane = _split_lanes(outer)
        if tempo_lane.contains(position):
            return EventTrackLane.TEMPO
        if meter_lane.contains(position):
            return EventTrackLane.METER
        return None

    def _beat_for_position(
        self,
        position: QPointF,
        *,
        snap: bool | None = None,
        allow_outside: bool = False,
    ) -> float | None:
        view_start, view_end, total = self._effective_window()
        if total <= 0.0 or (view_end - view_start) <= 1e-9:
            return None

        interaction_rect = QRectF(self.rect())
        if not allow_outside and not interaction_rect.contains(position):
            return None

        # 绘制使用 0.5 inset 的边框矩形以获得清晰像素边缘；
        # 交互坐标如果也复用该矩形，会在整数像素位置反推时间时引入
        # 一个稳定的小偏差（例如 600px 宽视图中拖到 x=344 时）。
        # 这里改用完整 widget rect，使鼠标所在的离散像素列能更均匀地
        # 映射到整个可视时间范围。
        seconds = _seconds_for_x(position.x(), interaction_rect, view_start, view_end)
        beat = self._timeline.seconds_to_beat(seconds)
        if self._snap_enabled if snap is None else bool(snap):
            beat = self._timeline.quantize_beat(beat)
        return max(0.0, float(beat))

    def _update_hover_cursor(self, position: QPointF) -> None:
        if self._drag_selection is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        handle = self.event_handle_at_position(position)
        if handle is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor if not handle.is_root_event else Qt.CursorShape.PointingHandCursor)
            return
        if self._lane_at_position(position) is not None and self._beat_for_position(position) is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        self.unsetCursor()

    def _display_timeline(self) -> MidiGridTimeline:
        return self._drag_preview_timeline or self._timeline

    def _event_for_selection(self, selection: EventTrackSelection | None) -> TempoEvent | TimeSignatureEvent | None:
        if selection is None:
            return None
        events = self._events_for_lane(selection.lane, self._timeline)
        if not 0 <= selection.event_index < len(events):
            return None
        return events[selection.event_index]

    def _movement_bounds(self, selection: EventTrackSelection) -> tuple[float, float | None]:
        events = self._events_for_lane(selection.lane, self._timeline)
        if not 0 <= selection.event_index < len(events):
            return 0.0, None
        lower_bound = float(events[selection.event_index - 1].beat_position) if selection.event_index > 0 else 0.0
        upper_bound = (
            float(events[selection.event_index + 1].beat_position)
            if (selection.event_index + 1) < len(events)
            else None
        )
        return lower_bound, upper_bound

    def _drag_beat_for_position(self, selection: EventTrackSelection, position: QPointF) -> float | None:
        raw_beat = self._beat_for_position(position, snap=False, allow_outside=True)
        if raw_beat is None:
            return None

        lower_bound, upper_bound = self._movement_bounds(selection)
        if self._snap_enabled:
            return self._snapped_drag_beat(raw_beat, lower_bound, upper_bound)
        return _clamp_beat_to_bounds(raw_beat, lower_bound, upper_bound)

    def _snapped_drag_beat(self, raw_beat: float, lower_bound: float, upper_bound: float | None) -> float:
        candidates = {
            self._timeline.quantize_beat(raw_beat, mode="nearest"),
            self._timeline.quantize_beat(raw_beat, mode="floor"),
            self._timeline.quantize_beat(raw_beat, mode="ceil"),
        }

        view_start, view_end, total = self._effective_window()
        if total > 0.0 and (view_end - view_start) > 1e-9:
            lower_probe = max(0.0, lower_bound + _EVENT_DRAG_MARGIN_BEATS)
            upper_probe = self._timeline.seconds_to_beat(view_end)
            if upper_bound is not None:
                upper_probe = min(upper_probe, upper_bound - _EVENT_DRAG_MARGIN_BEATS)
            if upper_probe > lower_probe:
                for line in self._timeline.iter_grid_lines_for_seconds_range(
                    self._timeline.beat_to_seconds(lower_probe),
                    self._timeline.beat_to_seconds(upper_probe),
                ):
                    candidates.add(float(line.beat_position))

        valid_candidates = [
            value
            for value in candidates
            if value > (lower_bound + _EVENT_DRAG_MARGIN_BEATS)
            and (upper_bound is None or value < (upper_bound - _EVENT_DRAG_MARGIN_BEATS))
        ]
        if valid_candidates:
            return min(valid_candidates, key=lambda value: (abs(value - raw_beat), value))
        return _clamp_beat_to_bounds(self._timeline.quantize_beat(raw_beat), lower_bound, upper_bound)

    def _update_drag_preview(self, position: QPointF) -> None:
        if self._drag_selection is None:
            return
        target_beat = self._drag_beat_for_position(self._drag_selection, position)
        preview_timeline = (
            None if target_beat is None else self._timeline_with_moved_event(self._drag_selection, target_beat)
        )
        if target_beat is None or preview_timeline is None:
            return
        self._drag_preview_beat = target_beat
        self._drag_preview_timeline = preview_timeline
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def _timeline_with_moved_event(self, selection: EventTrackSelection, beat_position: float) -> MidiGridTimeline | None:
        try:
            if selection.lane == EventTrackLane.TEMPO:
                events = list(self._timeline.tempo_events)
                current = events[selection.event_index]
                events[selection.event_index] = TempoEvent(beat_position, current.bpm, current.transition)
                return MidiGridTimeline(
                    tempo_events=tuple(events),
                    time_signature_events=self._timeline.time_signature_events,
                    offset_ms=self._timeline.offset_ms,
                    default_division=self._timeline.default_division,
                )

            events = list(self._timeline.time_signature_events)
            current = events[selection.event_index]
            events[selection.event_index] = TimeSignatureEvent(beat_position, current.time_signature)
            return MidiGridTimeline(
                tempo_events=self._timeline.tempo_events,
                time_signature_events=tuple(events),
                offset_ms=self._timeline.offset_ms,
                default_division=self._timeline.default_division,
            )
        except (IndexError, ValueError):
            return None

    def _cancel_drag_preview(self, *, update_widget: bool = True) -> None:
        self._drag_selection = None
        self._drag_preview_beat = None
        self._drag_preview_timeline = None
        if update_widget:
            self.update()

    def _cancel_pointer_interaction(self) -> None:
        self._pressed_selection = None
        self._pressed_position = None
        self._cancel_drag_preview()


def _selected_fill(fill: QColor) -> QColor:
    selected = QColor(fill)
    selected.setAlpha(min(255, fill.alpha() + 56))
    return selected


def _split_lanes(rect: QRectF) -> tuple[QRectF, QRectF]:
    half_height = rect.height() * 0.5
    top = QRectF(rect.left(), rect.top(), rect.width(), half_height)
    bottom = QRectF(rect.left(), rect.top() + half_height, rect.width(), rect.height() - half_height)
    return top, bottom


def _x_for_seconds(seconds: float, lane: QRectF, view_start: float, view_end: float) -> float:
    span = max(1e-9, view_end - view_start)
    ratio = (float(seconds) - view_start) / span
    clamped = max(0.0, min(1.0, ratio))
    return lane.left() + (lane.width() * clamped)


def _seconds_for_x(x: float, lane: QRectF, view_start: float, view_end: float) -> float:
    if lane.width() <= 1e-9:
        return view_start
    ratio = (float(x) - lane.left()) / lane.width()
    clamped = max(0.0, min(1.0, ratio))
    return view_start + ((view_end - view_start) * clamped)


def _y_for_value(value: float, rect: QRectF, minimum: float, maximum: float) -> float:
    span = max(1e-9, maximum - minimum)
    ratio = (float(value) - minimum) / span
    return rect.bottom() - (rect.height() * max(0.0, min(1.0, ratio)))


def _clamp_beat_to_bounds(beat: float, lower_bound: float, upper_bound: float | None) -> float:
    value = max(0.0, float(beat))
    minimum = max(0.0, float(lower_bound) + _EVENT_DRAG_MARGIN_BEATS)
    if upper_bound is None:
        return max(minimum, value)
    maximum = max(minimum, float(upper_bound) - _EVENT_DRAG_MARGIN_BEATS)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _tempo_value_for_segment(event: TempoEvent, next_event: TempoEvent | None, beat: float) -> float:
    if (
        next_event is None
        or event.transition != TempoTransition.LINEAR
        or next_event.beat_position <= event.beat_position + 1e-9
    ):
        return float(event.bpm)

    progress = (float(beat) - event.beat_position) / (next_event.beat_position - event.beat_position)
    clamped = max(0.0, min(1.0, progress))
    return float(event.bpm) + ((float(next_event.bpm) - float(event.bpm)) * clamped)


__all__ = [
    "EVENT_TRACK_HEIGHT",
    "EVENT_TRACK_LABELS_MIN_WIDTH",
    "EventTrackLaneLabels",
    "GridEventTrackWidget",
    "describe_tempo_event",
    "describe_time_signature_event",
]
