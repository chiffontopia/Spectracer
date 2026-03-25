from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from spectracer.midi.editor_model import MidiChannelConfig, MidiNote
from spectracer.midi.exporter import LinearTempoExportStrategy, export_notes_to_midi
from spectracer.midi.grid import MidiGridTimeline, TempoEvent, TempoTransition, TimeSignature, TimeSignatureEvent


@dataclass(slots=True)
class ParsedMidiEvent:
    tick: int
    type: str
    channel: int | None = None
    data: tuple[int, ...] = ()
    text: str | None = None
    tempo: int | None = None
    numerator: int | None = None
    denominator: int | None = None


@dataclass(slots=True)
class ParsedMidiFile:
    format_type: int
    ticks_per_beat: int
    tracks: list[list[ParsedMidiEvent]]


def test_export_notes_to_midi_writes_conductor_and_channel_tracks(tmp_path: Path) -> None:
    output_path = tmp_path / "phrase"
    exported_path = export_notes_to_midi(
        output_path,
        notes=(
            MidiNote(id="piano-a", pitch=60, start_beat=0.0, duration_beats=1.0, velocity=96, channel=0),
            MidiNote(id="lead-b", pitch=67, start_beat=2.0, duration_beats=1.0, velocity=88, channel=2),
        ),
        channel_configs=(
            MidiChannelConfig(channel=0, name="Piano", program=5, pan=32),
            MidiChannelConfig(channel=2, name="Lead", program=81, pan=96),
        ),
        timeline=MidiGridTimeline(
            tempo_events=(
                TempoEvent(0.0, 120.0),
                TempoEvent(8.0, 90.0),
            ),
            time_signature_events=(
                TimeSignatureEvent(0.0, TimeSignature(4, 4)),
                TimeSignatureEvent(8.0, TimeSignature(3, 4)),
            ),
        ),
        ticks_per_beat=480,
    )

    assert exported_path.suffix == ".mid"
    assert exported_path.exists()

    parsed = _parse_midi_file(exported_path)
    assert parsed.format_type == 1
    assert parsed.ticks_per_beat == 480
    assert len(parsed.tracks) == 3

    conductor = parsed.tracks[0]
    assert _track_name(conductor) == "Conductor"
    assert [(event.tick, event.tempo) for event in _events_of_type(conductor, "set_tempo")] == [
        (0, 500000),
        (3840, 666667),
    ]
    assert [
        (event.tick, event.numerator, event.denominator) for event in _events_of_type(conductor, "time_signature")
    ] == [
        (0, 4, 4),
        (3840, 3, 4),
    ]

    piano_track = parsed.tracks[1]
    assert _track_name(piano_track) == "Ch 01 - Piano"
    assert any(event.type == "program_change" and event.channel == 0 and event.data == (5,) for event in piano_track)
    assert any(event.type == "control_change" and event.channel == 0 and event.data == (10, 32) for event in piano_track)
    assert any(event.type == "note_on" and event.channel == 0 and event.data == (60, 96) and event.tick == 0 for event in piano_track)
    assert any(event.type == "note_off" and event.channel == 0 and event.data == (60, 0) and event.tick == 480 for event in piano_track)

    lead_track = parsed.tracks[2]
    assert _track_name(lead_track) == "Ch 03 - Lead"
    assert any(event.type == "program_change" and event.channel == 2 and event.data == (81,) for event in lead_track)
    assert any(event.type == "control_change" and event.channel == 2 and event.data == (10, 96) for event in lead_track)
    assert any(event.type == "note_on" and event.channel == 2 and event.data == (67, 88) and event.tick == 960 for event in lead_track)
    assert any(event.type == "note_off" and event.channel == 2 and event.data == (67, 0) and event.tick == 1440 for event in lead_track)


def test_export_notes_to_midi_discretizes_linear_tempo_and_preserves_positive_offset(tmp_path: Path) -> None:
    exported_path = export_notes_to_midi(
        tmp_path / "linear.mid",
        notes=(
            MidiNote(id="held", pitch=72, start_beat=4.0, duration_beats=1.0, velocity=100, channel=0),
        ),
        channel_configs=(MidiChannelConfig(channel=0, name="Lead", program=80, pan=64),),
        timeline=MidiGridTimeline(
            tempo_events=(
                TempoEvent(0.0, 120.0, TempoTransition.LINEAR),
                TempoEvent(4.0, 60.0, TempoTransition.STEP),
            ),
            time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(4, 4)),),
            offset_ms=500.0,
        ),
        ticks_per_beat=480,
        linear_tempo_strategy=LinearTempoExportStrategy(sample_step_beats=1.0),
    )

    parsed = _parse_midi_file(exported_path)
    conductor = parsed.tracks[0]
    assert [(event.tick, event.tempo) for event in _events_of_type(conductor, "set_tempo")] == [
        (0, 500000),
        (960, 571429),
        (1440, 666667),
        (1920, 800000),
        (2400, 1000000),
    ]
    assert [(event.tick, event.numerator, event.denominator) for event in _events_of_type(conductor, "time_signature")] == [
        (0, 4, 4),
    ]

    lead_track = parsed.tracks[1]
    assert any(event.type == "note_on" and event.data == (72, 100) and event.tick == 2400 for event in lead_track)
    assert any(event.type == "note_off" and event.data == (72, 0) and event.tick == 2880 for event in lead_track)


def test_export_notes_to_midi_rejects_zero_velocity_notes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="velocity <= 0"):
        export_notes_to_midi(
            tmp_path / "silent.mid",
            notes=(MidiNote(id="silent", pitch=60, start_beat=0.0, duration_beats=1.0, velocity=0, channel=0),),
            channel_configs=(MidiChannelConfig(channel=0),),
            timeline=MidiGridTimeline.constant(),
        )


def test_linear_tempo_export_strategy_requires_positive_step() -> None:
    with pytest.raises(ValueError, match="sample_step_beats"):
        LinearTempoExportStrategy(sample_step_beats=0.0)


def _parse_midi_file(path: Path) -> ParsedMidiFile:
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise AssertionError("缺少 MIDI 头")

    header_length = int.from_bytes(data[4:8], byteorder="big", signed=False)
    if header_length != 6:
        raise AssertionError(f"不支持的 MIDI header 长度：{header_length}")

    format_type = int.from_bytes(data[8:10], byteorder="big", signed=False)
    track_count = int.from_bytes(data[10:12], byteorder="big", signed=False)
    ticks_per_beat = int.from_bytes(data[12:14], byteorder="big", signed=False)

    tracks: list[list[ParsedMidiEvent]] = []
    offset = 14
    for _index in range(track_count):
        if data[offset : offset + 4] != b"MTrk":
            raise AssertionError("缺少 MTrk track chunk")
        track_length = int.from_bytes(data[offset + 4 : offset + 8], byteorder="big", signed=False)
        track_start = offset + 8
        track_end = track_start + track_length
        tracks.append(_parse_track_events(data[track_start:track_end]))
        offset = track_end

    return ParsedMidiFile(format_type=format_type, ticks_per_beat=ticks_per_beat, tracks=tracks)


def _parse_track_events(data: bytes) -> list[ParsedMidiEvent]:
    events: list[ParsedMidiEvent] = []
    offset = 0
    absolute_tick = 0
    running_status: int | None = None

    while offset < len(data):
        delta, offset = _read_variable_length_quantity(data, offset)
        absolute_tick += delta

        status = data[offset]
        if status < 0x80:
            if running_status is None:
                raise AssertionError("MIDI running status 丢失")
            status = running_status
        else:
            offset += 1
            if status < 0xF0:
                running_status = status
            else:
                running_status = None

        if status == 0xFF:
            meta_type = data[offset]
            offset += 1
            length, offset = _read_variable_length_quantity(data, offset)
            payload = data[offset : offset + length]
            offset += length

            if meta_type == 0x2F:
                events.append(ParsedMidiEvent(tick=absolute_tick, type="end_of_track"))
                continue
            if meta_type == 0x03:
                events.append(ParsedMidiEvent(tick=absolute_tick, type="track_name", text=payload.decode("utf-8")))
                continue
            if meta_type == 0x51:
                events.append(
                    ParsedMidiEvent(
                        tick=absolute_tick,
                        type="set_tempo",
                        tempo=int.from_bytes(payload, byteorder="big", signed=False),
                    )
                )
                continue
            if meta_type == 0x58:
                events.append(
                    ParsedMidiEvent(
                        tick=absolute_tick,
                        type="time_signature",
                        numerator=payload[0],
                        denominator=2 ** payload[1],
                    )
                )
                continue

            events.append(ParsedMidiEvent(tick=absolute_tick, type=f"meta_{meta_type:02x}", data=tuple(payload)))
            continue

        event_type = status & 0xF0
        channel = status & 0x0F
        if event_type in {0x80, 0x90, 0xB0, 0xE0}:
            first = data[offset]
            second = data[offset + 1]
            offset += 2
            name_map = {
                0x80: "note_off",
                0x90: "note_on",
                0xB0: "control_change",
                0xE0: "pitch_bend",
            }
            events.append(ParsedMidiEvent(tick=absolute_tick, type=name_map[event_type], channel=channel, data=(first, second)))
            continue

        if event_type in {0xC0, 0xD0}:
            first = data[offset]
            offset += 1
            name_map = {
                0xC0: "program_change",
                0xD0: "channel_pressure",
            }
            events.append(ParsedMidiEvent(tick=absolute_tick, type=name_map[event_type], channel=channel, data=(first,)))
            continue

        raise AssertionError(f"未处理的 MIDI 事件状态字节：0x{status:02X}")

    return events


def _read_variable_length_quantity(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value, offset


def _events_of_type(events: list[ParsedMidiEvent], event_type: str) -> list[ParsedMidiEvent]:
    return [event for event in events if event.type == event_type]


def _track_name(events: list[ParsedMidiEvent]) -> str | None:
    for event in events:
        if event.type == "track_name":
            return event.text
    return None
