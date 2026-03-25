from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from spectracer.midi.editor_model import MidiChannelConfig, MidiNote
from spectracer.midi.grid import MidiGridTimeline, TempoEvent, TempoTransition, TimeSignatureEvent

_MIDI_EPSILON = 1e-9
_DEFAULT_TICKS_PER_BEAT = 480
_TRACK_PRIORITY_TRACK_NAME = 0
_TRACK_PRIORITY_META_TIME_SIGNATURE = 10
_TRACK_PRIORITY_META_TEMPO = 20
_TRACK_PRIORITY_BANK_SELECT = 10
_TRACK_PRIORITY_PROGRAM_CHANGE = 20
_TRACK_PRIORITY_CHANNEL_PAN = 30
_TRACK_PRIORITY_NOTE_OFF = 40
_TRACK_PRIORITY_NOTE_ON = 50
_MIDI_TIME_SIGNATURE_CLOCKS_PER_CLICK = 24
_MIDI_TIME_SIGNATURE_32NDS_PER_QUARTER = 8


@dataclass(slots=True, frozen=True)
class LinearTempoExportStrategy:
    """标准 MIDI 中线性 BPM 段的离散化策略。"""

    sample_step_beats: float = 0.5

    def __post_init__(self) -> None:
        sample_step_beats = float(self.sample_step_beats)
        if not math.isfinite(sample_step_beats) or sample_step_beats <= 0.0:
            raise ValueError("sample_step_beats 必须是大于 0 的有限数值")
        object.__setattr__(self, "sample_step_beats", sample_step_beats)


@dataclass(slots=True, frozen=True)
class _TrackEvent:
    tick: int
    priority: int
    payload: bytes


@dataclass(slots=True, frozen=True)
class _TempoPoint:
    tick: int
    order: int
    bpm: float


@dataclass(slots=True, frozen=True)
class _TimeSignaturePoint:
    tick: int
    order: int
    event: TimeSignatureEvent


def export_notes_to_midi(
    output_path: str | Path,
    *,
    notes: Sequence[MidiNote] = (),
    channel_configs: Sequence[MidiChannelConfig] = (),
    timeline: MidiGridTimeline,
    ticks_per_beat: int = _DEFAULT_TICKS_PER_BEAT,
    linear_tempo_strategy: LinearTempoExportStrategy | None = None,
) -> Path:
    """把当前会话中的音符、通道配置与时间线导出为标准 MIDI 文件。

    说明：
    - 音符内部已使用 quarter-note beat 坐标，因此音符 tick 位置直接由 beat 映射得到。
    - `TempoTransition.LINEAR` 在标准 MIDI 中无法原生表示，会按 `linear_tempo_strategy`
      近似离散为多个 tempo meta events。
    - 正的 `timeline.offset_ms` 会被导出为前导空拍；负 offset 无法以 MIDI 的负时间表示，
      因此会被截断为 0。
    """

    if not isinstance(timeline, MidiGridTimeline):
        raise TypeError("timeline 必须是 MidiGridTimeline")

    normalized_path = _normalize_output_path(output_path)
    normalized_ticks_per_beat = _normalize_ticks_per_beat(ticks_per_beat)
    strategy = linear_tempo_strategy if linear_tempo_strategy is not None else LinearTempoExportStrategy()
    normalized_notes = _normalize_notes(notes)
    channel_config_map = _normalize_channel_configs(channel_configs)
    lead_in_ticks = _lead_in_ticks_for_timeline(timeline, normalized_ticks_per_beat)

    conductor_track = _build_conductor_track(
        timeline=timeline,
        ticks_per_beat=normalized_ticks_per_beat,
        linear_tempo_strategy=strategy,
        lead_in_ticks=lead_in_ticks,
    )

    notes_by_channel: dict[int, list[MidiNote]] = {}
    for note in normalized_notes:
        notes_by_channel.setdefault(note.channel, []).append(note)

    channel_tracks = [
        _build_channel_track(
            channel=channel,
            notes=notes_by_channel[channel],
            channel_config=channel_config_map.get(channel, MidiChannelConfig(channel=channel)),
            ticks_per_beat=normalized_ticks_per_beat,
            lead_in_ticks=lead_in_ticks,
        )
        for channel in sorted(notes_by_channel)
    ]

    track_chunks = [conductor_track, *channel_tracks] if channel_tracks else [conductor_track]
    format_type = 1 if len(track_chunks) > 1 else 0
    midi_bytes = _encode_midi_file(track_chunks=track_chunks, format_type=format_type, ticks_per_beat=normalized_ticks_per_beat)

    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.write_bytes(midi_bytes)
    return normalized_path


def _normalize_output_path(output_path: str | Path) -> Path:
    path = Path(output_path).expanduser()
    if not path.suffix:
        path = path.with_suffix(".mid")
    if path.exists() and path.is_dir():
        raise ValueError(f"输出路径必须是文件，不能是目录：{path}")
    return path


def _normalize_ticks_per_beat(ticks_per_beat: int) -> int:
    normalized = int(ticks_per_beat)
    if normalized <= 0:
        raise ValueError("ticks_per_beat 必须大于 0")
    if normalized > 0x7FFF:
        raise ValueError("ticks_per_beat 不能超过 32767")
    return normalized


def _normalize_notes(notes: Sequence[MidiNote]) -> tuple[MidiNote, ...]:
    normalized: list[MidiNote] = []
    for note in tuple(notes):
        if not isinstance(note, MidiNote):
            raise TypeError("notes 只能包含 MidiNote")
        if note.velocity <= 0:
            raise ValueError(f"无法导出 velocity <= 0 的 note：{note.id}")
        normalized.append(note)
    return tuple(sorted(normalized, key=lambda item: (item.channel, item.start_beat, item.pitch, item.id)))


def _normalize_channel_configs(channel_configs: Sequence[MidiChannelConfig]) -> dict[int, MidiChannelConfig]:
    normalized: dict[int, MidiChannelConfig] = {}
    for config in tuple(channel_configs):
        if not isinstance(config, MidiChannelConfig):
            raise TypeError("channel_configs 只能包含 MidiChannelConfig")
        if config.channel in normalized:
            raise ValueError(f"channel_configs 中存在重复 channel：{config.channel}")
        normalized[config.channel] = config
    return normalized


def _lead_in_ticks_for_timeline(timeline: MidiGridTimeline, ticks_per_beat: int) -> int:
    if timeline.offset_seconds <= 0.0:
        return 0
    first_bpm = timeline.tempo_events[0].bpm
    lead_in_beats = (timeline.offset_seconds * first_bpm) / 60.0
    return _tick_from_beat(lead_in_beats, ticks_per_beat)


def _build_conductor_track(
    *,
    timeline: MidiGridTimeline,
    ticks_per_beat: int,
    linear_tempo_strategy: LinearTempoExportStrategy,
    lead_in_ticks: int,
) -> bytes:
    events: list[_TrackEvent] = [
        _TrackEvent(0, _TRACK_PRIORITY_TRACK_NAME, _meta_track_name("Conductor")),
    ]

    for point in _build_tempo_points(
        timeline=timeline,
        ticks_per_beat=ticks_per_beat,
        linear_tempo_strategy=linear_tempo_strategy,
        lead_in_ticks=lead_in_ticks,
    ):
        events.append(_TrackEvent(point.tick, _TRACK_PRIORITY_META_TEMPO, _meta_set_tempo(point.bpm)))

    for point in _build_time_signature_points(
        timeline=timeline,
        ticks_per_beat=ticks_per_beat,
        lead_in_ticks=lead_in_ticks,
    ):
        events.append(
            _TrackEvent(
                point.tick,
                _TRACK_PRIORITY_META_TIME_SIGNATURE,
                _meta_time_signature(point.event.time_signature.numerator, point.event.time_signature.denominator),
            )
        )

    return _encode_track(events)


def _build_tempo_points(
    *,
    timeline: MidiGridTimeline,
    ticks_per_beat: int,
    linear_tempo_strategy: LinearTempoExportStrategy,
    lead_in_ticks: int,
) -> tuple[_TempoPoint, ...]:
    tempo_points: list[_TempoPoint] = []
    order = 0

    if lead_in_ticks > 0:
        tempo_points.append(_TempoPoint(tick=0, order=order, bpm=timeline.tempo_events[0].bpm))
        order += 1

    tempo_events = tuple(timeline.tempo_events)
    for index, event in enumerate(tempo_events):
        next_event = tempo_events[index + 1] if index + 1 < len(tempo_events) else None
        if not (index == 0 and lead_in_ticks > 0):
            tempo_points.append(
                _TempoPoint(
                    tick=lead_in_ticks + _tick_from_beat(event.beat_position, ticks_per_beat),
                    order=order,
                    bpm=event.bpm,
                )
            )
            order += 1

        if next_event is None or event.transition is not TempoTransition.LINEAR:
            continue

        sample_beat = event.beat_position + linear_tempo_strategy.sample_step_beats
        while sample_beat < (next_event.beat_position - _MIDI_EPSILON):
            tempo_points.append(
                _TempoPoint(
                    tick=lead_in_ticks + _tick_from_beat(sample_beat, ticks_per_beat),
                    order=order,
                    bpm=_linear_tempo_bpm_at_beat(event, next_event, sample_beat),
                )
            )
            order += 1
            sample_beat += linear_tempo_strategy.sample_step_beats

    return _dedupe_tempo_points(tempo_points)


def _build_time_signature_points(
    *,
    timeline: MidiGridTimeline,
    ticks_per_beat: int,
    lead_in_ticks: int,
) -> tuple[_TimeSignaturePoint, ...]:
    points: list[_TimeSignaturePoint] = []
    order = 0

    if lead_in_ticks > 0:
        points.append(_TimeSignaturePoint(tick=0, order=order, event=timeline.time_signature_events[0]))
        order += 1

    for index, event in enumerate(tuple(timeline.time_signature_events)):
        if index == 0 and lead_in_ticks > 0:
            continue
        points.append(
            _TimeSignaturePoint(
                tick=lead_in_ticks + _tick_from_beat(event.beat_position, ticks_per_beat),
                order=order,
                event=event,
            )
        )
        order += 1

    return _dedupe_time_signature_points(points)


def _linear_tempo_bpm_at_beat(start_event: TempoEvent, end_event: TempoEvent, beat: float) -> float:
    beat_span = end_event.beat_position - start_event.beat_position
    if beat_span <= _MIDI_EPSILON:
        return start_event.bpm
    ratio = (float(beat) - start_event.beat_position) / beat_span
    clamped_ratio = max(0.0, min(1.0, ratio))
    return start_event.bpm + ((end_event.bpm - start_event.bpm) * clamped_ratio)


def _dedupe_tempo_points(points: Sequence[_TempoPoint]) -> tuple[_TempoPoint, ...]:
    deduped: dict[int, _TempoPoint] = {}
    for point in sorted(points, key=lambda item: (item.tick, item.order)):
        deduped[point.tick] = point
    return tuple(sorted(deduped.values(), key=lambda item: (item.tick, item.order)))


def _dedupe_time_signature_points(points: Sequence[_TimeSignaturePoint]) -> tuple[_TimeSignaturePoint, ...]:
    deduped: dict[int, _TimeSignaturePoint] = {}
    for point in sorted(points, key=lambda item: (item.tick, item.order)):
        deduped[point.tick] = point
    return tuple(sorted(deduped.values(), key=lambda item: (item.tick, item.order)))


def _build_channel_track(
    *,
    channel: int,
    notes: Sequence[MidiNote],
    channel_config: MidiChannelConfig,
    ticks_per_beat: int,
    lead_in_ticks: int,
) -> bytes:
    sorted_notes = tuple(sorted(notes, key=lambda item: (item.start_beat, item.pitch, item.id)))
    track_name = f"Ch {channel + 1:02d} - {channel_config.display_name}"
    events: list[_TrackEvent] = [
        _TrackEvent(0, _TRACK_PRIORITY_TRACK_NAME, _meta_track_name(track_name)),
    ]

    bank_msb, bank_lsb = _split_bank_value(channel_config.bank)
    events.append(_TrackEvent(0, _TRACK_PRIORITY_BANK_SELECT, _control_change(channel, 0, bank_msb)))
    events.append(_TrackEvent(0, _TRACK_PRIORITY_BANK_SELECT, _control_change(channel, 32, bank_lsb)))
    events.append(_TrackEvent(0, _TRACK_PRIORITY_PROGRAM_CHANGE, _program_change(channel, channel_config.program)))
    events.append(_TrackEvent(0, _TRACK_PRIORITY_CHANNEL_PAN, _control_change(channel, 10, channel_config.pan)))

    current_pan = channel_config.pan
    for note in sorted_notes:
        start_tick = lead_in_ticks + _tick_from_beat(note.start_beat, ticks_per_beat)
        end_tick = max(start_tick + 1, lead_in_ticks + _tick_from_beat(note.end_beat, ticks_per_beat))
        desired_pan = channel_config.pan if note.pan is None else int(note.pan)
        if desired_pan != current_pan:
            events.append(_TrackEvent(start_tick, _TRACK_PRIORITY_CHANNEL_PAN, _control_change(channel, 10, desired_pan)))
            current_pan = desired_pan
        events.append(_TrackEvent(start_tick, _TRACK_PRIORITY_NOTE_ON, _note_on(channel, note.pitch, note.velocity)))
        events.append(_TrackEvent(end_tick, _TRACK_PRIORITY_NOTE_OFF, _note_off(channel, note.pitch)))

    return _encode_track(events)


def _tick_from_beat(beat: float, ticks_per_beat: int) -> int:
    return max(0, int(round(float(beat) * ticks_per_beat)))


def _split_bank_value(bank: int) -> tuple[int, int]:
    normalized_bank = max(0, int(bank))
    return max(0, min(127, normalized_bank // 128)), max(0, min(127, normalized_bank % 128))


def _encode_midi_file(*, track_chunks: Sequence[bytes], format_type: int, ticks_per_beat: int) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, int(format_type), len(track_chunks), int(ticks_per_beat))
    body = b"".join(b"MTrk" + struct.pack(">I", len(track)) + track for track in track_chunks)
    return header + body


def _encode_track(events: Sequence[_TrackEvent]) -> bytes:
    payload = bytearray()
    previous_tick = 0
    for event in sorted(events, key=lambda item: (item.tick, item.priority)):
        delta = max(0, int(event.tick) - previous_tick)
        payload.extend(_encode_variable_length_quantity(delta))
        payload.extend(event.payload)
        previous_tick = max(previous_tick, int(event.tick))
    payload.extend(_encode_variable_length_quantity(0))
    payload.extend(_meta_end_of_track())
    return bytes(payload)


def _encode_variable_length_quantity(value: int) -> bytes:
    if value < 0:
        raise ValueError("VLQ 不能编码负数")
    buffer = [value & 0x7F]
    remaining = int(value) >> 7
    while remaining:
        buffer.append(0x80 | (remaining & 0x7F))
        remaining >>= 7
    buffer.reverse()
    return bytes(buffer)


def _meta_event(meta_type: int, payload: bytes) -> bytes:
    return bytes((0xFF, meta_type)) + _encode_variable_length_quantity(len(payload)) + payload


def _meta_track_name(name: str) -> bytes:
    return _meta_event(0x03, str(name).encode("utf-8"))


def _meta_set_tempo(bpm: float) -> bytes:
    tempo_value = max(1, min(0xFFFFFF, int(round(60_000_000.0 / float(bpm)))))
    return _meta_event(0x51, tempo_value.to_bytes(3, byteorder="big", signed=False))


def _meta_time_signature(numerator: int, denominator: int) -> bytes:
    normalized_numerator = max(1, int(numerator))
    normalized_denominator = max(1, int(denominator))
    power_of_two = int(round(math.log2(normalized_denominator)))
    payload = bytes(
        (
            normalized_numerator,
            power_of_two,
            _MIDI_TIME_SIGNATURE_CLOCKS_PER_CLICK,
            _MIDI_TIME_SIGNATURE_32NDS_PER_QUARTER,
        )
    )
    return _meta_event(0x58, payload)


def _meta_end_of_track() -> bytes:
    return _meta_event(0x2F, b"")


def _channel_message(status: int, channel: int, *data: int) -> bytes:
    normalized_channel = max(0, min(15, int(channel)))
    normalized_data = bytes(max(0, min(127, int(value))) for value in data)
    return bytes((status | normalized_channel,)) + normalized_data


def _control_change(channel: int, control: int, value: int) -> bytes:
    return _channel_message(0xB0, channel, control, value)


def _program_change(channel: int, program: int) -> bytes:
    return _channel_message(0xC0, channel, program)


def _note_on(channel: int, note: int, velocity: int) -> bytes:
    return _channel_message(0x90, channel, note, velocity)


def _note_off(channel: int, note: int) -> bytes:
    return _channel_message(0x80, channel, note, 0)


__all__ = [
    "LinearTempoExportStrategy",
    "export_notes_to_midi",
]
