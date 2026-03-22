from __future__ import annotations

import pytest

from spectracer.midi.editor_model import (
    MidiChannelConfig,
    MidiEditorRuntimeState,
    MidiEditorState,
    MidiEditorTool,
    MidiNote,
    MidiProjectState,
    MidiSnapResolution,
)


def test_midi_note_exposes_derived_properties_and_shift() -> None:
    note = MidiNote(pitch=60, start_beat=1.5, duration_beats=0.5, velocity=96, channel=2)

    shifted = note.shifted(delta_beats=0.25, delta_pitch=3)

    assert note.start_beats == pytest.approx(1.5)
    assert note.end_beat == pytest.approx(2.0)
    assert note.contains_beat(1.75)
    assert not note.contains_beat(2.6)
    assert shifted.id == note.id
    assert shifted.start_beat == pytest.approx(1.75)
    assert shifted.pitch == 63


def test_midi_note_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="pitch"):
        MidiNote(pitch=128, start_beat=0.0, duration_beats=0.5)
    with pytest.raises(ValueError, match="start_beat"):
        MidiNote(pitch=60, start_beat=-0.25, duration_beats=0.5)
    with pytest.raises(ValueError, match="duration_beats"):
        MidiNote(pitch=60, start_beat=0.0, duration_beats=0.0)


def test_snap_resolution_parse_and_quantize_supports_thirty_second_notes() -> None:
    resolution = MidiSnapResolution.parse("1/32")

    assert resolution is MidiSnapResolution.THIRTY_SECOND
    assert resolution.beat_length == pytest.approx(0.125)
    assert MidiSnapResolution.parse(0.125) is MidiSnapResolution.THIRTY_SECOND
    assert resolution.quantize(1.19) == pytest.approx(1.25)


def test_channel_config_defaults_drum_channel_name_and_bank() -> None:
    drum_config = MidiChannelConfig(channel=9)
    melodic_config = MidiChannelConfig(channel=1, name="", color="")

    assert drum_config.is_drum is True
    assert drum_config.name == "Percussion"
    assert drum_config.bank == 128
    assert drum_config.color.startswith("#")
    assert melodic_config.name == "Channel 02"
    assert melodic_config.bank == 0


def test_editor_state_and_runtime_state_normalize_values() -> None:
    editor_state = MidiEditorState(
        enabled=True,
        tool="erase",
        active_channel=15,
        snap_enabled=True,
        snap_resolution="1/32",
        darken_amount=0.6,
        box_select_enabled=False,
    )
    runtime_state = MidiEditorRuntimeState(selected_note_ids=frozenset({" note-a ", "", "note-b"}))

    assert editor_state.tool is MidiEditorTool.ERASE
    assert editor_state.snap_resolution is MidiSnapResolution.THIRTY_SECOND
    assert editor_state.active_channel == 15
    assert editor_state.darken_amount == pytest.approx(0.6)
    assert runtime_state.selected_note_ids == frozenset({"note-a", "note-b"})


def test_project_state_merges_channel_defaults_and_rejects_duplicate_note_ids() -> None:
    note = MidiNote(pitch=60, start_beat=0.0, duration_beats=1.0)
    custom_channel = MidiChannelConfig(channel=0, name="Lead", program=81)
    state = MidiProjectState(notes=[note], channel_configs=[custom_channel])

    assert len(state.channel_configs) == 16
    assert state.channel_config_for(0).name == "Lead"
    assert state.channel_config_for(9).name == "Percussion"

    with pytest.raises(ValueError, match="重复"):
        MidiProjectState(notes=[note, note.with_updates(start_beat=2.0)])
