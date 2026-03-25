from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from spectracer.core.models import CqtResult
from spectracer.midi.editor_model import MidiEditorRuntimeState, MidiNote, MidiProjectState
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.ui.overlays.midi_note_overlay import MidiNoteOverlay, midi_note_to_frequency


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_midi_note_overlay_maps_timeline_pitch_and_channel_color(qapp: QApplication) -> None:
    _ = qapp
    result = _make_test_cqt_result()
    session = MidiSession(
        MidiProjectState(
            notes=(MidiNote(id="note-1", pitch=60, start_beat=1.0, duration_beats=2.0, velocity=96, channel=2),),
        ),
        MidiEditorRuntimeState(selected_note_ids=frozenset({"note-1"})),
    )
    session.update_channel_config(2, color="#FF4081")

    overlay = MidiNoteOverlay()
    overlay.set_timeline(MidiGridTimeline.constant(bpm=120.0, numerator=4, denominator=4))
    overlay.set_result(result, duration_seconds=result.duration_seconds)
    overlay.set_session(session)
    overlay.set_visible(True)

    geometry = overlay.note_geometry("note-1")

    assert geometry is not None
    assert geometry.channel_color.name().lower() == "#ff4081"
    assert geometry.is_selected is True
    assert geometry.rect.left() == pytest.approx(0.5)
    assert geometry.rect.width() == pytest.approx(1.0)
    assert geometry.rect.height() >= 1.0
    assert overlay.hit_test(0.75, geometry.rect.center().y()).id == "note-1"


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
