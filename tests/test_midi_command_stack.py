from __future__ import annotations

import pytest

from spectracer.midi.commands import (
    AddNoteCommand,
    MoveNotesCommand,
    UpdateChannelConfigCommand,
    UpdateNotePropertyCommand,
)
from spectracer.midi.editor_model import MidiNote
from spectracer.midi.session import MidiSession


def test_command_stack_supports_single_command_undo_and_redo() -> None:
    session = MidiSession()
    note = MidiNote(id="cmd-add", pitch=60, start_beat=1.0, duration_beats=0.5)

    session.add_note(note, select=True)

    assert session.can_undo is True
    assert session.can_redo is False
    assert session.undo_count == 1
    assert session.redo_count == 0

    undone = session.undo()

    assert isinstance(undone, AddNoteCommand)
    assert session.notes == ()
    assert session.selected_note_ids == frozenset()
    assert session.can_redo is True

    redone = session.redo()

    assert isinstance(redone, AddNoteCommand)
    assert session.notes == (note,)
    assert session.selected_note_ids == frozenset({note.id})
    assert session.can_undo is True
    assert session.can_redo is False


def test_command_stack_supports_batch_note_updates_and_clears_redo_on_new_command() -> None:
    session = MidiSession()
    note_a = MidiNote(id="cmd-a", pitch=60, start_beat=0.0, duration_beats=0.5)
    note_b = MidiNote(id="cmd-b", pitch=64, start_beat=1.0, duration_beats=0.5)
    session.add_notes((note_a, note_b), record_undo=False)

    updated = session.update_notes((note_a.id, note_b.id), velocity=96, pan=24)

    assert isinstance(session.undo(), UpdateNotePropertyCommand)
    assert [note.velocity for note in updated] == [96, 96]
    assert session.require_note(note_a.id).velocity == 100
    assert session.require_note(note_b.id).pan is None
    assert session.redo_count == 1

    moved = session.move_notes((note_a.id, note_b.id), delta_beats=0.5, delta_pitch=2)

    assert [note.start_beat for note in moved] == pytest.approx([0.5, 1.5])
    assert session.require_note(note_a.id).pitch == 62
    assert session.redo_count == 0

    undone = session.undo()

    assert isinstance(undone, MoveNotesCommand)
    assert session.require_note(note_a.id).start_beat == pytest.approx(0.0)
    assert session.require_note(note_b.id).start_beat == pytest.approx(1.0)


def test_command_stack_supports_channel_config_undo_and_redo() -> None:
    session = MidiSession()

    updated = session.update_channel_config(
        2,
        name="Lead Synth",
        program=81,
        bank=1,
        pan=32,
        color="#FF4081",
        muted=True,
        solo=True,
    )

    assert updated.display_name == "Lead Synth"
    assert updated.program == 81
    assert updated.pan == 32
    assert updated.color.lower() == "#ff4081"
    assert session.undo_count == 1

    undone = session.undo()

    assert isinstance(undone, UpdateChannelConfigCommand)
    restored = session.get_channel_config(2)
    assert restored.display_name == "Channel 03"
    assert restored.program == 0
    assert restored.pan == 64
    assert restored.muted is False
    assert restored.solo is False

    redone = session.redo()

    assert isinstance(redone, UpdateChannelConfigCommand)
    reapplied = session.get_channel_config(2)
    assert reapplied.display_name == "Lead Synth"
    assert reapplied.program == 81
    assert reapplied.pan == 32
    assert reapplied.muted is True
    assert reapplied.solo is True
