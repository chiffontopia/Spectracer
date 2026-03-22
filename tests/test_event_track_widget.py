from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from spectracer.midi.editor_model import EventTrackLane, EventTrackSelection
from spectracer.midi.grid import MidiGridTimeline, TempoEvent, TimeSignature, TimeSignatureEvent
from spectracer.ui.overlays.event_track_widget import GridEventTrackWidget, describe_tempo_event, describe_time_signature_event


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_describe_tempo_event_includes_bar_number_and_trimmed_bpm() -> None:
    timeline = MidiGridTimeline.constant(bpm=120.0, numerator=4, denominator=4)
    event = TempoEvent(4.0, 96.5)

    assert describe_tempo_event(timeline, event) == "96.5 BPM · 小节 2"


def test_describe_time_signature_event_uses_change_bar_number() -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0),),
        time_signature_events=(
            TimeSignatureEvent(0.0, TimeSignature(4, 4)),
            TimeSignatureEvent(8.0, TimeSignature(3, 4)),
        ),
    )

    assert describe_time_signature_event(timeline, timeline.time_signature_events[1]) == "3/4 · 小节 3"


def test_event_track_widget_hit_test_returns_tempo_selection_for_visible_badge(qapp: QApplication) -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0), TempoEvent(4.0, 90.0)),
        time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
    )
    widget = GridEventTrackWidget()
    widget.resize(600, 104)
    widget.set_grid_timeline(timeline)
    widget.set_duration_seconds(10.0)
    widget.set_view_window(0.0, 5.0, 10.0)
    qapp.processEvents()

    hit = widget.event_handle_at_position(QPointF(_x_for_seconds(widget.width(), 2.0, 0.0, 5.0), 36.0))

    assert hit == EventTrackSelection(EventTrackLane.TEMPO, 1)


def test_event_track_widget_hit_test_returns_meter_selection_for_visible_badge(qapp: QApplication) -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0),),
        time_signature_events=(
            TimeSignatureEvent(0.0, TimeSignature(4, 4)),
            TimeSignatureEvent(8.0, TimeSignature(3, 4)),
        ),
    )
    widget = GridEventTrackWidget()
    widget.resize(600, 104)
    widget.set_grid_timeline(timeline)
    widget.set_duration_seconds(10.0)
    widget.set_view_window(0.0, 5.0, 10.0)
    qapp.processEvents()

    hit = widget.event_handle_at_position(QPointF(_x_for_seconds(widget.width(), 4.0, 0.0, 5.0), 88.0))

    assert hit == EventTrackSelection(EventTrackLane.METER, 1)


def test_event_track_widget_drag_emits_quantized_move_request(qapp: QApplication) -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0), TempoEvent(4.0, 90.0)),
        time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
    )
    widget = GridEventTrackWidget()
    widget.resize(600, 104)
    widget.show()
    widget.set_grid_timeline(timeline)
    widget.set_duration_seconds(10.0)
    widget.set_view_window(0.0, 5.0, 10.0)
    qapp.processEvents()

    moves: list[tuple[EventTrackSelection, float]] = []
    widget.move_requested.connect(lambda selection, beat: moves.append((selection, beat)))

    start = QPoint(round(_x_for_seconds(widget.width(), 2.0, 0.0, 5.0)), 36)
    target_seconds = timeline.beat_to_seconds(5.3)
    end = QPoint(round(_x_for_seconds(widget.width(), target_seconds, 0.0, 5.0)), 36)

    _send_mouse_event(qapp, widget, QEvent.Type.MouseButtonPress, start, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    _send_mouse_event(qapp, widget, QEvent.Type.MouseMove, end, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton)
    _send_mouse_event(qapp, widget, QEvent.Type.MouseButtonRelease, end, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)

    assert moves[0][0] == EventTrackSelection(EventTrackLane.TEMPO, 1)
    assert moves[0][1] == pytest.approx(5.25)


def test_event_track_widget_drag_respects_snap_toggle_when_disabled(qapp: QApplication) -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0), TempoEvent(4.0, 90.0)),
        time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
    )
    widget = GridEventTrackWidget()
    widget.resize(600, 104)
    widget.show()
    widget.set_grid_timeline(timeline)
    widget.set_snap_enabled(False)
    widget.set_duration_seconds(10.0)
    widget.set_view_window(0.0, 5.0, 10.0)
    qapp.processEvents()

    moves: list[tuple[EventTrackSelection, float]] = []
    widget.move_requested.connect(lambda selection, beat: moves.append((selection, beat)))

    start = QPoint(round(_x_for_seconds(widget.width(), 2.0, 0.0, 5.0)), 36)
    target_seconds = timeline.beat_to_seconds(5.3)
    end = QPoint(round(_x_for_seconds(widget.width(), target_seconds, 0.0, 5.0)), 36)

    _send_mouse_event(qapp, widget, QEvent.Type.MouseButtonPress, start, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    _send_mouse_event(qapp, widget, QEvent.Type.MouseMove, end, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton)
    _send_mouse_event(qapp, widget, QEvent.Type.MouseButtonRelease, end, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)

    assert moves[0][0] == EventTrackSelection(EventTrackLane.TEMPO, 1)
    assert moves[0][1] == pytest.approx(5.3)


def _x_for_seconds(widget_width: int, seconds: float, view_start: float, view_end: float) -> float:
    left = 0.5
    width = float(widget_width) - 1.0
    return left + (width * ((seconds - view_start) / (view_end - view_start)))


def _send_mouse_event(
    qapp: QApplication,
    widget: GridEventTrackWidget,
    event_type: QEvent.Type,
    point: QPoint,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    event = QMouseEvent(
        event_type,
        QPointF(point),
        QPointF(point),
        QPointF(point),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)
    qapp.processEvents()
