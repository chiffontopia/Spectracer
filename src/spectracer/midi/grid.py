from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Sequence

_GRID_EPSILON = 1e-9


class TempoTransition(str, Enum):
    """Tempo 事件到下一事件之间的过渡方式。"""

    STEP = "step"
    LINEAR = "linear"

    @classmethod
    def parse(cls, raw: "TempoTransition | str") -> "TempoTransition":
        if isinstance(raw, cls):
            return raw
        normalized = str(raw).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"未知 tempo transition: {raw}，可选值: {choices}") from exc


class GridLineKind(str, Enum):
    """网格线类型。"""

    BAR = "bar"
    BEAT = "beat"
    SUBDIVISION = "subdivision"


QuantizeMode = Literal["nearest", "floor", "ceil"]


@dataclass(slots=True, frozen=True)
class TempoEvent:
    beat_position: float
    bpm: float
    transition: TempoTransition = TempoTransition.STEP

    def __post_init__(self) -> None:
        object.__setattr__(self, "beat_position", float(self.beat_position))
        object.__setattr__(self, "bpm", float(self.bpm))
        object.__setattr__(self, "transition", TempoTransition.parse(self.transition))
        if self.beat_position < 0.0:
            raise ValueError("tempo event 的 beat_position 不可为负数")
        if self.bpm <= 0.0:
            raise ValueError("tempo event 的 bpm 必须大于 0")


@dataclass(slots=True, frozen=True)
class TimeSignature:
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        numerator = int(self.numerator)
        denominator = int(self.denominator)
        object.__setattr__(self, "numerator", numerator)
        object.__setattr__(self, "denominator", denominator)
        if numerator <= 0:
            raise ValueError("拍号 numerator 必须大于 0")
        if denominator <= 0:
            raise ValueError("拍号 denominator 必须大于 0")
        if denominator & (denominator - 1):
            raise ValueError("拍号 denominator 必须是 2 的整数次幂")

    @property
    def beat_unit_beats(self) -> float:
        """当前拍号下，一个基础拍占据多少个 quarter-note beats。"""

        return 4.0 / float(self.denominator)

    @property
    def bar_length_beats(self) -> float:
        return float(self.numerator) * self.beat_unit_beats


@dataclass(slots=True, frozen=True)
class TimeSignatureEvent:
    beat_position: float
    time_signature: TimeSignature

    def __post_init__(self) -> None:
        object.__setattr__(self, "beat_position", float(self.beat_position))
        if self.beat_position < 0.0:
            raise ValueError("time signature event 的 beat_position 不可为负数")


@dataclass(slots=True, frozen=True)
class GridDivision:
    """将当前拍号中的一个基础拍再均分为若干份。"""

    subdivisions_per_beat: int = 4

    def __post_init__(self) -> None:
        subdivisions = int(self.subdivisions_per_beat)
        object.__setattr__(self, "subdivisions_per_beat", subdivisions)
        if subdivisions <= 0:
            raise ValueError("subdivisions_per_beat 必须大于 0")

    def step_beats_for(self, time_signature: TimeSignature) -> float:
        return time_signature.beat_unit_beats / float(self.subdivisions_per_beat)


@dataclass(slots=True, frozen=True)
class BarPosition:
    beat: float
    time_signature: TimeSignature
    bar_number: int
    bar_start_beat: float
    beat_index_in_bar: int
    beat_start_beat: float
    offset_within_beat: float


@dataclass(slots=True, frozen=True)
class GridLine:
    beat_position: float
    seconds: float
    kind: GridLineKind
    bar_number: int
    beat_index_in_bar: int
    subdivision_index: int
    label: str | None = None


@dataclass(slots=True, frozen=True)
class _TempoSegment:
    start_beat: float
    end_beat: float
    start_seconds: float
    end_seconds: float
    start_bpm: float
    end_bpm: float
    transition: TempoTransition

    @property
    def is_infinite(self) -> bool:
        return math.isinf(self.end_beat)

    @property
    def beat_span(self) -> float:
        if self.is_infinite:
            return math.inf
        return self.end_beat - self.start_beat

    @property
    def slope(self) -> float:
        if self.transition is not TempoTransition.LINEAR or self.is_infinite:
            return 0.0
        span = self.end_beat - self.start_beat
        if span <= _GRID_EPSILON:
            return 0.0
        return (self.end_bpm - self.start_bpm) / span

    def seconds_from_beat(self, beat: float) -> float:
        delta_beats = float(beat) - self.start_beat
        if delta_beats <= 0.0:
            return self.start_seconds
        if self.transition is TempoTransition.STEP or self.is_infinite:
            return self.start_seconds + ((60.0 * delta_beats) / self.start_bpm)
        slope = self.slope
        if abs(slope) <= _GRID_EPSILON:
            return self.start_seconds + ((60.0 * delta_beats) / self.start_bpm)
        ratio = (self.start_bpm + (slope * delta_beats)) / self.start_bpm
        return self.start_seconds + ((60.0 / slope) * math.log(ratio))

    def beat_from_seconds(self, seconds: float) -> float:
        delta_seconds = float(seconds) - self.start_seconds
        if delta_seconds <= 0.0:
            return self.start_beat
        if self.transition is TempoTransition.STEP or self.is_infinite:
            return self.start_beat + ((delta_seconds * self.start_bpm) / 60.0)
        slope = self.slope
        if abs(slope) <= _GRID_EPSILON:
            return self.start_beat + ((delta_seconds * self.start_bpm) / 60.0)
        exponent = math.exp((delta_seconds * slope) / 60.0)
        return self.start_beat + ((self.start_bpm * (exponent - 1.0)) / slope)


@dataclass(slots=True, frozen=True)
class _MeterSegment:
    start_beat: float
    end_beat: float
    time_signature: TimeSignature
    start_bar_number: int


@dataclass(slots=True)
class MidiGridTimeline:
    """支持 tempo/meter 事件、偏移量与网格量化的 MIDI 时间线。"""

    tempo_events: Sequence[TempoEvent] = field(default_factory=lambda: (TempoEvent(0.0, 120.0),))
    time_signature_events: Sequence[TimeSignatureEvent] = field(
        default_factory=lambda: (TimeSignatureEvent(0.0, TimeSignature(4, 4)),)
    )
    offset_ms: float = 0.0
    default_division: GridDivision = field(default_factory=GridDivision)
    _tempo_segments: tuple[_TempoSegment, ...] = field(init=False, repr=False)
    _meter_segments: tuple[_MeterSegment, ...] = field(init=False, repr=False)
    _tempo_segment_starts: tuple[float, ...] = field(init=False, repr=False)
    _tempo_segment_seconds: tuple[float, ...] = field(init=False, repr=False)
    _meter_segment_starts: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.tempo_events = _normalize_tempo_events(self.tempo_events)
        self.time_signature_events = _normalize_time_signature_events(self.time_signature_events)
        self.offset_ms = float(self.offset_ms)
        self.default_division = GridDivision(self.default_division.subdivisions_per_beat)
        self._tempo_segments = _build_tempo_segments(self.tempo_events)
        self._tempo_segment_starts = tuple(segment.start_beat for segment in self._tempo_segments)
        self._tempo_segment_seconds = tuple(segment.start_seconds for segment in self._tempo_segments)
        self._meter_segments = _build_meter_segments(self.time_signature_events)
        self._meter_segment_starts = tuple(segment.start_beat for segment in self._meter_segments)

    @property
    def offset_seconds(self) -> float:
        return self.offset_ms / 1000.0

    @classmethod
    def constant(
        cls,
        *,
        bpm: float = 120.0,
        numerator: int = 4,
        denominator: int = 4,
        offset_ms: float = 0.0,
        subdivisions_per_beat: int = 4,
    ) -> "MidiGridTimeline":
        return cls(
            tempo_events=(TempoEvent(0.0, bpm),),
            time_signature_events=(TimeSignatureEvent(0.0, TimeSignature(numerator, denominator)),),
            offset_ms=offset_ms,
            default_division=GridDivision(subdivisions_per_beat),
        )

    def beat_to_seconds(self, beat: float) -> float:
        beat_value = float(beat)
        if beat_value <= 0.0:
            first_bpm = self.tempo_events[0].bpm
            return self.offset_seconds + ((60.0 * beat_value) / first_bpm)
        segment = self._tempo_segment_at_beat(beat_value)
        return self.offset_seconds + segment.seconds_from_beat(beat_value)

    def seconds_to_beat(self, seconds: float) -> float:
        local_seconds = float(seconds) - self.offset_seconds
        if local_seconds <= 0.0:
            first_bpm = self.tempo_events[0].bpm
            return (local_seconds * first_bpm) / 60.0
        segment = self._tempo_segment_at_seconds(local_seconds)
        return segment.beat_from_seconds(local_seconds)

    def tempo_event_at_beat(self, beat: float) -> TempoEvent:
        beat_value = max(0.0, float(beat))
        index = bisect.bisect_right([event.beat_position for event in self.tempo_events], beat_value) - 1
        return self.tempo_events[max(0, index)]

    def time_signature_at_beat(self, beat: float) -> TimeSignature:
        if float(beat) < 0.0:
            return self.time_signature_events[0].time_signature
        segment = self._meter_segment_at_beat(float(beat))
        return segment.time_signature

    def bar_position_at_beat(self, beat: float) -> BarPosition:
        beat_value = float(beat)
        if beat_value < 0.0:
            signature = self.time_signature_events[0].time_signature
            bar_length = signature.bar_length_beats
            beat_unit = signature.beat_unit_beats
            bar_offset = math.floor(beat_value / bar_length)
            bar_start = float(bar_offset) * bar_length
            local = beat_value - bar_start
            beat_index = min(signature.numerator - 1, max(0, int(math.floor(local / beat_unit))))
            beat_start = bar_start + (beat_index * beat_unit)
            return BarPosition(
                beat=beat_value,
                time_signature=signature,
                bar_number=bar_offset + 1,
                bar_start_beat=bar_start,
                beat_index_in_bar=beat_index,
                beat_start_beat=beat_start,
                offset_within_beat=beat_value - beat_start,
            )

        segment = self._meter_segment_at_beat(beat_value)
        signature = segment.time_signature
        bar_length = signature.bar_length_beats
        beat_unit = signature.beat_unit_beats
        local = beat_value - segment.start_beat
        bar_index = int(math.floor(local / bar_length)) if bar_length > _GRID_EPSILON else 0
        bar_start = segment.start_beat + (bar_index * bar_length)
        beat_within_bar = beat_value - bar_start
        beat_index = min(signature.numerator - 1, max(0, int(math.floor(beat_within_bar / beat_unit))))
        beat_start = bar_start + (beat_index * beat_unit)
        return BarPosition(
            beat=beat_value,
            time_signature=signature,
            bar_number=segment.start_bar_number + bar_index,
            bar_start_beat=bar_start,
            beat_index_in_bar=beat_index,
            beat_start_beat=beat_start,
            offset_within_beat=beat_value - beat_start,
        )

    def iter_grid_lines_for_duration(
        self,
        duration_seconds: float,
        *,
        division: GridDivision | None = None,
    ) -> list[GridLine]:
        return self.iter_grid_lines_for_seconds_range(0.0, duration_seconds, division=division)

    def iter_grid_lines_for_seconds_range(
        self,
        start_seconds: float,
        end_seconds: float,
        *,
        division: GridDivision | None = None,
    ) -> list[GridLine]:
        if float(end_seconds) <= float(start_seconds):
            return []

        chosen_division = self.default_division if division is None else GridDivision(division.subdivisions_per_beat)
        raw_start_beat = self.seconds_to_beat(start_seconds)
        raw_end_beat = self.seconds_to_beat(end_seconds)
        if raw_end_beat < 0.0:
            return []

        start_beat = max(0.0, raw_start_beat)
        end_beat = max(0.0, raw_end_beat)
        lines: list[GridLine] = []

        for segment in self._meter_segments:
            if segment.end_beat <= start_beat + _GRID_EPSILON:
                continue
            if segment.start_beat > end_beat + _GRID_EPSILON:
                break

            effective_start = max(start_beat, segment.start_beat)
            effective_end = min(end_beat, segment.end_beat)
            if effective_end < effective_start - _GRID_EPSILON:
                continue

            signature = segment.time_signature
            bar_length = signature.bar_length_beats
            beat_unit = signature.beat_unit_beats
            sub_step = chosen_division.step_beats_for(signature)
            bar_index = max(0, int(math.floor((effective_start - segment.start_beat) / bar_length)))
            bar_start = segment.start_beat + (bar_index * bar_length)

            while bar_start < segment.end_beat - _GRID_EPSILON and bar_start <= effective_end + _GRID_EPSILON:
                bar_number = segment.start_bar_number + bar_index
                for beat_index in range(signature.numerator):
                    beat_start = bar_start + (beat_index * beat_unit)
                    if beat_start >= segment.end_beat - _GRID_EPSILON:
                        break
                    if beat_start > effective_end + _GRID_EPSILON:
                        break
                    for subdivision_index in range(chosen_division.subdivisions_per_beat):
                        beat_position = beat_start + (subdivision_index * sub_step)
                        if beat_position >= segment.end_beat - _GRID_EPSILON:
                            break
                        if beat_position < effective_start - _GRID_EPSILON:
                            continue
                        if beat_position > effective_end + _GRID_EPSILON:
                            break
                        if beat_index == 0 and subdivision_index == 0:
                            kind = GridLineKind.BAR
                            label = str(bar_number)
                        elif subdivision_index == 0:
                            kind = GridLineKind.BEAT
                            label = None
                        else:
                            kind = GridLineKind.SUBDIVISION
                            label = None
                        lines.append(
                            GridLine(
                                beat_position=beat_position,
                                seconds=self.beat_to_seconds(beat_position),
                                kind=kind,
                                bar_number=bar_number,
                                beat_index_in_bar=beat_index,
                                subdivision_index=subdivision_index,
                                label=label,
                            )
                        )
                bar_index += 1
                bar_start += bar_length

        return lines

    def quantize_beat(
        self,
        beat: float,
        *,
        division: GridDivision | None = None,
        mode: QuantizeMode = "nearest",
    ) -> float:
        if mode not in {"nearest", "floor", "ceil"}:
            raise ValueError(f"未知量化模式: {mode}")

        beat_value = max(0.0, float(beat))
        chosen_division = self.default_division if division is None else GridDivision(division.subdivisions_per_beat)
        segment = self._meter_segment_at_beat(beat_value)
        signature = segment.time_signature
        step = chosen_division.step_beats_for(signature)
        anchor = segment.start_beat
        relative_index = (beat_value - anchor) / step
        floor_index = int(math.floor(relative_index + _GRID_EPSILON))
        ceil_index = int(math.ceil(relative_index - _GRID_EPSILON))

        def make_candidate(index: int) -> float:
            candidate = anchor + (float(index) * step)
            if candidate < anchor:
                candidate = anchor
            if not math.isinf(segment.end_beat) and candidate > segment.end_beat:
                candidate = segment.end_beat
            return candidate

        if mode == "floor":
            return max(0.0, make_candidate(floor_index))
        if mode == "ceil":
            return max(0.0, make_candidate(ceil_index))

        candidates = {
            make_candidate(floor_index),
            make_candidate(ceil_index),
        }
        if not math.isinf(segment.end_beat):
            candidates.add(segment.end_beat)
        return max(0.0, min(candidates, key=lambda value: (abs(value - beat_value), value)))

    def quantize_seconds(
        self,
        seconds: float,
        *,
        division: GridDivision | None = None,
        mode: QuantizeMode = "nearest",
    ) -> float:
        quantized_beat = self.quantize_beat(self.seconds_to_beat(seconds), division=division, mode=mode)
        return self.beat_to_seconds(quantized_beat)

    def _tempo_segment_at_beat(self, beat: float) -> _TempoSegment:
        index = bisect.bisect_right(self._tempo_segment_starts, float(beat)) - 1
        return self._tempo_segments[max(0, index)]

    def _tempo_segment_at_seconds(self, seconds: float) -> _TempoSegment:
        index = bisect.bisect_right(self._tempo_segment_seconds, float(seconds)) - 1
        return self._tempo_segments[max(0, index)]

    def _meter_segment_at_beat(self, beat: float) -> _MeterSegment:
        index = bisect.bisect_right(self._meter_segment_starts, float(beat)) - 1
        return self._meter_segments[max(0, index)]


def _normalize_tempo_events(events: Sequence[TempoEvent]) -> tuple[TempoEvent, ...]:
    if not events:
        raise ValueError("tempo_events 至少需要一个事件")
    normalized = tuple(sorted(events, key=lambda event: event.beat_position))
    if normalized[0].beat_position != 0.0:
        raise ValueError("tempo_events 必须从 beat 0 开始")
    _ensure_strictly_increasing([event.beat_position for event in normalized], "tempo_events")
    return normalized


def _normalize_time_signature_events(events: Sequence[TimeSignatureEvent]) -> tuple[TimeSignatureEvent, ...]:
    if not events:
        raise ValueError("time_signature_events 至少需要一个事件")
    normalized = tuple(sorted(events, key=lambda event: event.beat_position))
    if normalized[0].beat_position != 0.0:
        raise ValueError("time_signature_events 必须从 beat 0 开始")
    _ensure_strictly_increasing([event.beat_position for event in normalized], "time_signature_events")
    return normalized


def _ensure_strictly_increasing(values: Sequence[float], label: str) -> None:
    for previous, current in zip(values, values[1:], strict=False):
        if current <= previous:
            raise ValueError(f"{label} 中的 beat_position 必须严格递增")


def _build_tempo_segments(events: Sequence[TempoEvent]) -> tuple[_TempoSegment, ...]:
    segments: list[_TempoSegment] = []
    current_seconds = 0.0
    for index, event in enumerate(events):
        next_event = events[index + 1] if index + 1 < len(events) else None
        if next_event is None:
            segments.append(
                _TempoSegment(
                    start_beat=event.beat_position,
                    end_beat=math.inf,
                    start_seconds=current_seconds,
                    end_seconds=math.inf,
                    start_bpm=event.bpm,
                    end_bpm=event.bpm,
                    transition=TempoTransition.STEP,
                )
            )
            continue

        beat_span = next_event.beat_position - event.beat_position
        elapsed_seconds = _elapsed_seconds_for_segment(
            beat_span=beat_span,
            start_bpm=event.bpm,
            end_bpm=next_event.bpm,
            transition=event.transition,
        )
        segments.append(
            _TempoSegment(
                start_beat=event.beat_position,
                end_beat=next_event.beat_position,
                start_seconds=current_seconds,
                end_seconds=current_seconds + elapsed_seconds,
                start_bpm=event.bpm,
                end_bpm=next_event.bpm,
                transition=event.transition,
            )
        )
        current_seconds += elapsed_seconds
    return tuple(segments)


def _build_meter_segments(events: Sequence[TimeSignatureEvent]) -> tuple[_MeterSegment, ...]:
    segments: list[_MeterSegment] = []
    next_bar_number = 1
    for index, event in enumerate(events):
        next_event = events[index + 1] if index + 1 < len(events) else None
        end_beat = math.inf if next_event is None else next_event.beat_position
        segments.append(
            _MeterSegment(
                start_beat=event.beat_position,
                end_beat=end_beat,
                time_signature=event.time_signature,
                start_bar_number=next_bar_number,
            )
        )
        if next_event is None:
            continue

        span = max(0.0, next_event.beat_position - event.beat_position)
        bar_length = event.time_signature.bar_length_beats
        bars_used = 0 if span <= _GRID_EPSILON else int(math.ceil((span - _GRID_EPSILON) / bar_length))
        next_bar_number += bars_used
    return tuple(segments)


def _elapsed_seconds_for_segment(
    *,
    beat_span: float,
    start_bpm: float,
    end_bpm: float,
    transition: TempoTransition,
) -> float:
    if beat_span <= 0.0:
        return 0.0
    if transition is TempoTransition.STEP:
        return (60.0 * beat_span) / start_bpm
    slope = (end_bpm - start_bpm) / beat_span
    if abs(slope) <= _GRID_EPSILON:
        return (60.0 * beat_span) / start_bpm
    return (60.0 / slope) * math.log(end_bpm / start_bpm)


__all__ = [
    "BarPosition",
    "GridDivision",
    "GridLine",
    "GridLineKind",
    "MidiGridTimeline",
    "QuantizeMode",
    "TempoEvent",
    "TempoTransition",
    "TimeSignature",
    "TimeSignatureEvent",
]
