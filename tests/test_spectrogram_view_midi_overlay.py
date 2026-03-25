from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from spectracer.core.models import CqtResult
from spectracer.midi.editor_model import MidiEditorState, MidiNote, MidiProjectState
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.ui.overlays.midi_note_overlay import midi_note_to_frequency
from spectracer.ui.views.spectrogram_view import SpectrogramView


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_spectrogram_view_toggles_midi_overlay_and_dim_layer_with_editor_state(qapp: QApplication) -> None:
    result = _make_test_cqt_result()
    session = MidiSession(
        MidiProjectState(
            notes=(MidiNote(id="note-1", pitch=60, start_beat=1.0, duration_beats=2.0, velocity=90, channel=1),),
        )
    )
    timeline = MidiGridTimeline.constant(bpm=120.0, numerator=4, denominator=4)

    view = SpectrogramView()
    view.resize(720, 360)
    view.show()
    view.set_grid_timeline(timeline)
    view.set_midi_session(session)
    view.set_cqt_result(result)
    qapp.processEvents()

    assert view.is_midi_overlay_visible() is False
    assert view.is_dim_overlay_visible() is False
    assert view.midi_note_at(0.75, 24.5) is None

    view.set_midi_editor_state(MidiEditorState(enabled=True, darken_amount=0.6))
    qapp.processEvents()

    rect = view.midi_note_rect("note-1")
    assert rect is not None
    assert rect.left() == pytest.approx(0.5)
    assert rect.width() == pytest.approx(1.0)
    assert view.is_midi_overlay_visible() is True
    assert view.is_dim_overlay_visible() is True
    assert view.midi_overlay_darken_amount() == pytest.approx(0.6)
    assert view.dim_overlay_alpha() == 153
    assert view.midi_note_at(rect.center().x(), rect.center().y()).id == "note-1"

    view.set_midi_overlay_visible(False)
    qapp.processEvents()

    assert view.is_midi_overlay_visible() is False
    assert view.midi_note_at(rect.center().x(), rect.center().y()) is None


def test_spectrogram_view_reacts_to_session_note_and_editor_state_changes(qapp: QApplication) -> None:
    result = _make_test_cqt_result()
    session = MidiSession()

    view = SpectrogramView()
    view.resize(720, 360)
    view.show()
    view.set_midi_session(session)
    view.set_cqt_result(result)
    qapp.processEvents()

    assert view.midi_note_rect("live-note") is None
    assert view.is_midi_overlay_visible() is False

    session.add_note(MidiNote(id="live-note", pitch=67, start_beat=0.5, duration_beats=1.5, channel=3))
    session.update_editor_state(enabled=True, darken_amount=0.4)
    qapp.processEvents()

    rect = view.midi_note_rect("live-note")
    assert rect is not None
    assert rect.left() == pytest.approx(0.25)
    assert rect.width() == pytest.approx(0.75)
    assert view.is_midi_overlay_visible() is True
    assert view.dim_overlay_alpha() == 102
    assert view.midi_note_at(rect.center().x(), rect.center().y()).id == "live-note"


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
