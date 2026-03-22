from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TypeAlias
from uuid import uuid4

from spectracer.midi.gm import effective_midi_bank, is_drum_channel

MidiNoteId: TypeAlias = str

_DEFAULT_CHANNEL_COLORS: tuple[str, ...] = (
    "#EF5350",
    "#AB47BC",
    "#5C6BC0",
    "#29B6F6",
    "#26A69A",
    "#66BB6A",
    "#D4E157",
    "#FFCA28",
    "#FFA726",
    "#8D6E63",
    "#EC407A",
    "#7E57C2",
    "#42A5F5",
    "#26C6DA",
    "#9CCC65",
    "#FF7043",
)


def _normalize_non_negative_float(value: float, *, field_name: str, allow_zero: bool = True) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} 必须是有限数值")
    if normalized < 0.0:
        raise ValueError(f"{field_name} 不可为负数")
    if not allow_zero and normalized <= 0.0:
        raise ValueError(f"{field_name} 必须大于 0")
    return normalized


def _normalize_int_in_range(value: int, *, field_name: str, minimum: int, maximum: int) -> int:
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} 超出范围，期望 {minimum}~{maximum}，实际为 {normalized}")
    return normalized


def _default_channel_name(channel: int) -> str:
    if is_drum_channel(channel):
        return "Percussion"
    return f"Channel {channel + 1:02d}"


def _default_channel_color(channel: int) -> str:
    return _DEFAULT_CHANNEL_COLORS[int(channel) % len(_DEFAULT_CHANNEL_COLORS)]


class EventTrackLane(str, Enum):
    TEMPO = "tempo"
    METER = "meter"

    @property
    def display_name(self) -> str:
        return "Tempo" if self is EventTrackLane.TEMPO else "Meter"

    @classmethod
    def parse(cls, raw: EventTrackLane | str) -> EventTrackLane:
        if isinstance(raw, cls):
            return raw
        normalized = str(raw).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"未知事件轨道类型: {raw}，可选值: {choices}") from exc


@dataclass(slots=True, frozen=True)
class EventTrackSelection:
    lane: EventTrackLane
    event_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", EventTrackLane.parse(self.lane))
        object.__setattr__(self, "event_index", int(self.event_index))
        if self.event_index < 0:
            raise ValueError("event_index 不可为负数")

    @property
    def is_root_event(self) -> bool:
        return self.event_index == 0


class MidiEditorTool(str, Enum):
    PLACE = "place"
    SELECT = "select"
    ERASE = "erase"

    @property
    def display_name(self) -> str:
        mapping = {
            MidiEditorTool.PLACE: "放置",
            MidiEditorTool.SELECT: "选择",
            MidiEditorTool.ERASE: "擦除",
        }
        return mapping[self]

    @classmethod
    def parse(cls, raw: MidiEditorTool | str) -> MidiEditorTool:
        if isinstance(raw, cls):
            return raw
        normalized = str(raw).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"未知编辑工具: {raw}，可选值: {choices}") from exc


class MidiSnapResolution(str, Enum):
    QUARTER = "1/4"
    EIGHTH = "1/8"
    SIXTEENTH = "1/16"
    THIRTY_SECOND = "1/32"

    @property
    def beat_length(self) -> float:
        mapping = {
            MidiSnapResolution.QUARTER: 1.0,
            MidiSnapResolution.EIGHTH: 0.5,
            MidiSnapResolution.SIXTEENTH: 0.25,
            MidiSnapResolution.THIRTY_SECOND: 0.125,
        }
        return mapping[self]

    @property
    def display_name(self) -> str:
        return self.value

    @classmethod
    def ordered(cls) -> tuple[MidiSnapResolution, ...]:
        return (
            cls.QUARTER,
            cls.EIGHTH,
            cls.SIXTEENTH,
            cls.THIRTY_SECOND,
        )

    @classmethod
    def parse(cls, raw: MidiSnapResolution | str | float) -> MidiSnapResolution:
        if isinstance(raw, cls):
            return raw

        if isinstance(raw, (int, float)):
            normalized_value = float(raw)
            value_map = {
                1.0: cls.QUARTER,
                0.5: cls.EIGHTH,
                0.25: cls.SIXTEENTH,
                0.125: cls.THIRTY_SECOND,
            }
            for beat_length, resolution in value_map.items():
                if math.isclose(normalized_value, beat_length, rel_tol=1e-9, abs_tol=1e-9):
                    return resolution
            choices = ", ".join(f"{item.value} ({item.beat_length:g} beat)" for item in cls.ordered())
            raise ValueError(f"未知吸附分辨率: {raw}，可选值: {choices}")

        normalized = str(raw).strip().lower().replace(" ", "")
        aliases = {
            "1/4": cls.QUARTER,
            "quarter": cls.QUARTER,
            "quarternote": cls.QUARTER,
            "1/8": cls.EIGHTH,
            "eighth": cls.EIGHTH,
            "eighthnote": cls.EIGHTH,
            "1/16": cls.SIXTEENTH,
            "sixteenth": cls.SIXTEENTH,
            "sixteenthnote": cls.SIXTEENTH,
            "1/32": cls.THIRTY_SECOND,
            "thirtysecond": cls.THIRTY_SECOND,
            "thirtysecondnote": cls.THIRTY_SECOND,
        }
        if normalized in aliases:
            return aliases[normalized]

        choices = ", ".join(item.value for item in cls.ordered())
        raise ValueError(f"未知吸附分辨率: {raw}，可选值: {choices}")

    def quantize(self, beat: float) -> float:
        normalized = _normalize_non_negative_float(beat, field_name="beat")
        step = self.beat_length
        return round(normalized / step) * step


@dataclass(slots=True, frozen=True)
class MidiNote:
    pitch: int
    start_beat: float
    duration_beats: float
    velocity: int = 100
    channel: int = 0
    pan: int | None = None
    id: MidiNoteId = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        note_id = str(self.id).strip()
        if not note_id:
            raise ValueError("id 不可为空")
        object.__setattr__(self, "id", note_id)
        object.__setattr__(self, "pitch", _normalize_int_in_range(self.pitch, field_name="pitch", minimum=0, maximum=127))
        object.__setattr__(
            self,
            "start_beat",
            _normalize_non_negative_float(self.start_beat, field_name="start_beat"),
        )
        object.__setattr__(
            self,
            "duration_beats",
            _normalize_non_negative_float(self.duration_beats, field_name="duration_beats", allow_zero=False),
        )
        object.__setattr__(
            self,
            "velocity",
            _normalize_int_in_range(self.velocity, field_name="velocity", minimum=0, maximum=127),
        )
        object.__setattr__(self, "channel", _normalize_int_in_range(self.channel, field_name="channel", minimum=0, maximum=15))
        if self.pan is not None:
            object.__setattr__(self, "pan", _normalize_int_in_range(self.pan, field_name="pan", minimum=0, maximum=127))

    @property
    def start_beats(self) -> float:
        return self.start_beat

    @property
    def end_beat(self) -> float:
        return self.start_beat + self.duration_beats

    def contains_beat(self, beat: float, *, tolerance: float = 0.0) -> bool:
        normalized_beat = float(beat)
        normalized_tolerance = _normalize_non_negative_float(tolerance, field_name="tolerance")
        return (self.start_beat - normalized_tolerance) <= normalized_beat <= (self.end_beat + normalized_tolerance)

    def overlaps_beat_range(self, start_beat: float, end_beat: float) -> bool:
        normalized_start = float(start_beat)
        normalized_end = float(end_beat)
        if normalized_end < normalized_start:
            normalized_start, normalized_end = normalized_end, normalized_start
        return not (self.end_beat < normalized_start or self.start_beat > normalized_end)

    def shifted(self, *, delta_beats: float = 0.0, delta_pitch: int = 0) -> MidiNote:
        return replace(
            self,
            start_beat=self.start_beat + float(delta_beats),
            pitch=self.pitch + int(delta_pitch),
        )

    def with_updates(self, **changes: object) -> MidiNote:
        return replace(self, **changes)


@dataclass(slots=True, frozen=True)
class MidiChannelConfig:
    channel: int
    name: str = ""
    program: int = 0
    bank: int = 0
    pan: int = 64
    color: str = ""
    muted: bool = False
    solo: bool = False

    def __post_init__(self) -> None:
        channel = _normalize_int_in_range(self.channel, field_name="channel", minimum=0, maximum=15)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "program", _normalize_int_in_range(self.program, field_name="program", minimum=0, maximum=127))
        bank = int(self.bank)
        if bank < 0:
            raise ValueError("bank 不可为负数")
        object.__setattr__(self, "bank", effective_midi_bank(channel, bank))
        object.__setattr__(self, "pan", _normalize_int_in_range(self.pan, field_name="pan", minimum=0, maximum=127))

        name = str(self.name).strip() or _default_channel_name(channel)
        color = str(self.color).strip() or _default_channel_color(channel)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "color", color)
        object.__setattr__(self, "muted", bool(self.muted))
        object.__setattr__(self, "solo", bool(self.solo))

    @property
    def is_drum(self) -> bool:
        return is_drum_channel(self.channel)

    @property
    def display_name(self) -> str:
        return self.name

    def with_updates(self, **changes: object) -> MidiChannelConfig:
        return replace(self, **changes)


class MidiEditorState:
    __slots__ = ("enabled", "tool", "active_channel", "snap_enabled", "snap_resolution", "darken_amount", "box_select_enabled")

    def __init__(
        self,
        *,
        enabled: bool = False,
        tool: MidiEditorTool | str = MidiEditorTool.SELECT,
        active_channel: int = 0,
        snap_enabled: bool = True,
        snap_resolution: MidiSnapResolution | str | float = MidiSnapResolution.SIXTEENTH,
        darken_amount: float = 0.35,
        box_select_enabled: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.tool = MidiEditorTool.parse(tool)
        self.active_channel = _normalize_int_in_range(active_channel, field_name="active_channel", minimum=0, maximum=15)
        self.snap_enabled = bool(snap_enabled)
        self.snap_resolution = MidiSnapResolution.parse(snap_resolution)
        darken_value = float(darken_amount)
        if not math.isfinite(darken_value) or darken_value < 0.0 or darken_value > 1.0:
            raise ValueError("darken_amount 必须位于 0.0~1.0")
        self.darken_amount = darken_value
        self.box_select_enabled = bool(box_select_enabled)

    def __repr__(self) -> str:
        return (
            "MidiEditorState("
            f"enabled={self.enabled!r}, tool={self.tool!r}, active_channel={self.active_channel!r}, "
            f"snap_enabled={self.snap_enabled!r}, snap_resolution={self.snap_resolution!r}, "
            f"darken_amount={self.darken_amount!r}, box_select_enabled={self.box_select_enabled!r}"
            ")"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MidiEditorState):
            return False
        return (
            self.enabled == other.enabled
            and self.tool == other.tool
            and self.active_channel == other.active_channel
            and self.snap_enabled == other.snap_enabled
            and self.snap_resolution == other.snap_resolution
            and math.isclose(self.darken_amount, other.darken_amount, rel_tol=1e-9, abs_tol=1e-9)
            and self.box_select_enabled == other.box_select_enabled
        )

    def with_updates(self, **changes: object) -> MidiEditorState:
        payload = {
            "enabled": self.enabled,
            "tool": self.tool,
            "active_channel": self.active_channel,
            "snap_enabled": self.snap_enabled,
            "snap_resolution": self.snap_resolution,
            "darken_amount": self.darken_amount,
            "box_select_enabled": self.box_select_enabled,
        }
        payload.update(changes)
        return MidiEditorState(**payload)


@dataclass(slots=True, frozen=True)
class MidiProjectState:
    notes: tuple[MidiNote, ...] = ()
    channel_configs: tuple[MidiChannelConfig, ...] = field(default_factory=lambda: default_midi_channel_configs())

    def __post_init__(self) -> None:
        notes = tuple(self.notes)
        normalized_notes: list[MidiNote] = []
        seen_note_ids: set[str] = set()
        for note in notes:
            if not isinstance(note, MidiNote):
                raise TypeError("notes 只能包含 MidiNote")
            if note.id in seen_note_ids:
                raise ValueError(f"存在重复的 note id: {note.id}")
            seen_note_ids.add(note.id)
            normalized_notes.append(note)
        normalized_notes.sort(key=lambda item: (item.start_beat, item.pitch, item.channel, item.id))
        object.__setattr__(self, "notes", tuple(normalized_notes))

        default_map = {config.channel: config for config in default_midi_channel_configs()}
        provided_channels: set[int] = set()
        for config in tuple(self.channel_configs):
            if not isinstance(config, MidiChannelConfig):
                raise TypeError("channel_configs 只能包含 MidiChannelConfig")
            if config.channel in provided_channels:
                raise ValueError(f"存在重复的 channel config: {config.channel}")
            provided_channels.add(config.channel)
            default_map[config.channel] = config
        merged_configs = tuple(default_map[channel] for channel in range(16))
        object.__setattr__(self, "channel_configs", merged_configs)

    @classmethod
    def empty(cls) -> MidiProjectState:
        return cls()

    def channel_config_for(self, channel: int) -> MidiChannelConfig:
        normalized_channel = _normalize_int_in_range(channel, field_name="channel", minimum=0, maximum=15)
        return self.channel_configs[normalized_channel]


@dataclass(slots=True, frozen=True)
class MidiEditorRuntimeState:
    editor_state: MidiEditorState = field(default_factory=MidiEditorState)
    selected_note_ids: frozenset[MidiNoteId] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        normalized_ids = frozenset(
            note_id
            for note_id in (str(raw).strip() for raw in self.selected_note_ids)
            if note_id
        )
        object.__setattr__(self, "selected_note_ids", normalized_ids)


def default_midi_channel_configs() -> tuple[MidiChannelConfig, ...]:
    return tuple(MidiChannelConfig(channel=channel) for channel in range(16))


__all__ = [
    "EventTrackLane",
    "EventTrackSelection",
    "MidiChannelConfig",
    "MidiEditorRuntimeState",
    "MidiEditorState",
    "MidiEditorTool",
    "MidiNote",
    "MidiNoteId",
    "MidiProjectState",
    "MidiSnapResolution",
    "default_midi_channel_configs",
]
