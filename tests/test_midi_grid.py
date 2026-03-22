from __future__ import annotations

import math

import pytest

from spectracer.midi.grid import (
    GridDivision,
    GridLineKind,
    MidiGridTimeline,
    TempoEvent,
    TempoTransition,
    TimeSignature,
    TimeSignatureEvent,
)


def test_constant_timeline_supports_offset_and_roundtrip() -> None:
    timeline = MidiGridTimeline.constant(bpm=120.0, numerator=4, denominator=4, offset_ms=250.0)

    assert timeline.beat_to_seconds(0.0) == pytest.approx(0.25)
    assert timeline.beat_to_seconds(1.0) == pytest.approx(0.75)
    assert timeline.beat_to_seconds(4.0) == pytest.approx(2.25)
    assert timeline.seconds_to_beat(0.25) == pytest.approx(0.0)
    assert timeline.seconds_to_beat(1.50) == pytest.approx(2.5)



def test_step_tempo_segments_roundtrip() -> None:
    timeline = MidiGridTimeline(
        tempo_events=(
            TempoEvent(0.0, 120.0, TempoTransition.STEP),
            TempoEvent(4.0, 60.0, TempoTransition.STEP),
        ),
        time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
    )

    assert timeline.beat_to_seconds(4.0) == pytest.approx(2.0)
    assert timeline.beat_to_seconds(6.0) == pytest.approx(4.0)
    assert timeline.seconds_to_beat(3.0) == pytest.approx(5.0)



def test_linear_tempo_segments_roundtrip() -> None:
    timeline = MidiGridTimeline(
        tempo_events=(
            TempoEvent(0.0, 120.0, TempoTransition.LINEAR),
            TempoEvent(4.0, 60.0, TempoTransition.STEP),
        ),
        time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
    )

    assert timeline.beat_to_seconds(2.0) == pytest.approx(-4.0 * math.log(0.75))
    assert timeline.beat_to_seconds(4.0) == pytest.approx(4.0 * math.log(2.0))

    sample_seconds = timeline.beat_to_seconds(2.75)
    assert timeline.seconds_to_beat(sample_seconds) == pytest.approx(2.75)



def test_time_signature_change_updates_bar_position() -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0),),
        time_signature_events=(
            TimeSignatureEvent(0.0, TimeSignature(4, 4)),
            TimeSignatureEvent(8.0, TimeSignature(3, 4)),
        ),
    )

    first_bar = timeline.bar_position_at_beat(3.25)
    changed_bar = timeline.bar_position_at_beat(10.0)
    next_bar = timeline.bar_position_at_beat(11.0)

    assert first_bar.bar_number == 1
    assert first_bar.beat_index_in_bar == 3
    assert changed_bar.bar_number == 3
    assert changed_bar.beat_index_in_bar == 2
    assert next_bar.bar_number == 4
    assert next_bar.beat_index_in_bar == 0



def test_iter_grid_lines_respects_offset_and_division() -> None:
    timeline = MidiGridTimeline.constant(
        bpm=120.0,
        numerator=4,
        denominator=4,
        offset_ms=500.0,
        subdivisions_per_beat=2,
    )

    lines = timeline.iter_grid_lines_for_duration(2.0)

    assert [line.kind for line in lines[:4]] == [
        GridLineKind.BAR,
        GridLineKind.SUBDIVISION,
        GridLineKind.BEAT,
        GridLineKind.SUBDIVISION,
    ]
    assert [line.seconds for line in lines[:4]] == pytest.approx([0.5, 0.75, 1.0, 1.25])
    assert lines[0].bar_number == 1
    assert lines[0].label == "1"



def test_quantize_beat_and_seconds_use_current_meter_division() -> None:
    timeline = MidiGridTimeline(
        tempo_events=(TempoEvent(0.0, 120.0),),
        time_signature_events=(
            TimeSignatureEvent(0.0, TimeSignature(4, 4)),
            TimeSignatureEvent(8.0, TimeSignature(3, 8)),
        ),
        default_division=GridDivision(4),
    )

    division = GridDivision(1)
    assert timeline.quantize_beat(8.21, division=division, mode="nearest") == pytest.approx(8.0)
    assert timeline.quantize_beat(8.21, division=division, mode="floor") == pytest.approx(8.0)
    assert timeline.quantize_beat(8.21, division=division, mode="ceil") == pytest.approx(8.5)

    quantized_seconds = timeline.quantize_seconds(0.62)
    assert quantized_seconds == pytest.approx(0.625)
