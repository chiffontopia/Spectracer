from __future__ import annotations

import pytest

from spectracer.midi.editor_model import MidiNote, MidiSnapResolution
from spectracer.midi.session import MidiSession


def test_session_initializes_with_default_channels() -> None:
    session = MidiSession()

    assert len(session.channel_configs) == 16
    assert session.get_channel_config(9).name == "Percussion"
    assert session.notes == ()
    assert session.selected_note_ids == frozenset()


def test_session_add_update_move_remove_note_and_selection() -> None:
    session = MidiSession()
    note = MidiNote(pitch=60, start_beat=0.0, duration_beats=0.5)

    session.add_note(note, select=True)
    updated = session.update_note(note.id, velocity=72, pan=24)
    moved = session.move_notes([note.id], delta_beats=0.5, delta_pitch=2)[0]
    removed = session.remove_note(note.id)

    assert session.selected_note_ids == frozenset()
    assert updated.velocity == 72
    assert updated.pan == 24
    assert moved.id == note.id
    assert moved.start_beat == pytest.approx(0.5)
    assert moved.pitch == 62
    assert removed.id == note.id
    assert session.notes == ()


def test_session_range_hit_test_and_box_selection() -> None:
    session = MidiSession()
    note_a = MidiNote(pitch=60, start_beat=0.0, duration_beats=1.0)
    note_b = MidiNote(pitch=64, start_beat=1.0, duration_beats=0.5, channel=1)
    note_c = MidiNote(pitch=72, start_beat=2.0, duration_beats=0.25)
    session.add_notes([note_a, note_b, note_c])

    overlapping = session.notes_in_range(0.75, 1.25)
    exact_start = session.notes_in_range(0.75, 1.25, include_overlapping=False)
    hit = session.hit_test(1.1, 64, beat_tolerance=0.0, pitch_tolerance=0)
    selection = session.select_notes_in_box(0.5, 1.5, 60, 64)

    assert [note.id for note in overlapping] == [note_a.id, note_b.id]
    assert [note.id for note in exact_start] == [note_b.id]
    assert hit is not None and hit.id == note_b.id
    assert {note.id for note in selection} == {note_a.id, note_b.id}
    assert session.selected_note_ids == frozenset({note_a.id, note_b.id})


def test_session_updates_editor_and_channel_state() -> None:
    session = MidiSession()

    editor_state = session.update_editor_state(
        enabled=True,
        tool="place",
        active_channel=3,
        snap_resolution=MidiSnapResolution.THIRTY_SECOND,
        darken_amount=0.55,
    )
    config = session.update_channel_config(3, name="Bass", program=33, pan=20, color="#123456")

    assert editor_state.enabled is True
    assert editor_state.active_channel == 3
    assert editor_state.snap_resolution is MidiSnapResolution.THIRTY_SECOND
    assert config.name == "Bass"
    assert config.program == 33
    assert config.pan == 20
    assert config.color == "#123456"
