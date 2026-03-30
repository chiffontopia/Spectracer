from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from spectracer.core.models import ChannelMode


SCHEMA_TEMPO_ANALYSIS_V1 = "spectracer-tempo-analysis-v1"
SCHEMA_CHORD_ANALYSIS_V1 = "spectracer-chord-analysis-v1"


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _parse_optional_channel_mode(value: Any) -> ChannelMode | None:
    if value is None or value == "":
        return None
    if isinstance(value, ChannelMode):
        return value
    return ChannelMode.parse(str(value))


@dataclass(slots=True)
class TempoAnalysisCandidate:
    bpm: float
    first_beat_seconds: float
    offset_ms: float
    confidence: float = 0.0
    candidate_rank: int = 1
    label: str | None = None

    def __post_init__(self) -> None:
        self.bpm = float(self.bpm)
        self.first_beat_seconds = float(self.first_beat_seconds)
        self.offset_ms = float(self.offset_ms)
        self.confidence = _clamp_confidence(self.confidence)
        self.candidate_rank = max(1, int(self.candidate_rank))
        self.label = _optional_text(self.label)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bpm": self.bpm,
            "first_beat_seconds": self.first_beat_seconds,
            "offset_ms": self.offset_ms,
            "confidence": self.confidence,
            "candidate_rank": self.candidate_rank,
        }
        if self.label is not None:
            payload["label"] = self.label
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TempoAnalysisCandidate":
        return cls(
            bpm=float(payload.get("bpm", 0.0)),
            first_beat_seconds=float(payload.get("first_beat_seconds", 0.0)),
            offset_ms=float(payload.get("offset_ms", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
            candidate_rank=int(payload.get("candidate_rank", 1)),
            label=_optional_text(payload.get("label")),
        )


@dataclass(slots=True)
class BeatAnchor:
    beat_index: float
    time_seconds: float
    confidence: float = 0.0

    def __post_init__(self) -> None:
        self.beat_index = float(self.beat_index)
        self.time_seconds = float(self.time_seconds)
        self.confidence = _clamp_confidence(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "beat_index": self.beat_index,
            "time_seconds": self.time_seconds,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BeatAnchor":
        return cls(
            beat_index=float(payload.get("beat_index", 0.0)),
            time_seconds=float(payload.get("time_seconds", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
        )


@dataclass(slots=True)
class TempoSegment:
    start_seconds: float
    end_seconds: float
    bpm: float
    confidence: float = 0.0
    start_beat: float | None = None
    end_beat: float | None = None

    def __post_init__(self) -> None:
        self.start_seconds = float(self.start_seconds)
        self.end_seconds = float(self.end_seconds)
        self.bpm = float(self.bpm)
        self.confidence = _clamp_confidence(self.confidence)
        self.start_beat = _optional_float(self.start_beat)
        self.end_beat = _optional_float(self.end_beat)
        if self.end_seconds < self.start_seconds:
            raise ValueError("tempo segment 的结束时间不可早于开始时间")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "bpm": self.bpm,
            "confidence": self.confidence,
        }
        if self.start_beat is not None:
            payload["start_beat"] = self.start_beat
        if self.end_beat is not None:
            payload["end_beat"] = self.end_beat
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TempoSegment":
        return cls(
            start_seconds=float(payload.get("start_seconds", 0.0)),
            end_seconds=float(payload.get("end_seconds", 0.0)),
            bpm=float(payload.get("bpm", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
            start_beat=_optional_float(payload.get("start_beat")),
            end_beat=_optional_float(payload.get("end_beat")),
        )


@dataclass(slots=True)
class TempoAnalysisResult:
    candidates: tuple[TempoAnalysisCandidate, ...] = ()
    channel_mode: ChannelMode | None = None
    selected_candidate_rank: int | None = None
    beat_anchors: tuple[BeatAnchor, ...] = ()
    tempo_segments: tuple[TempoSegment, ...] = ()
    analysis_basis: str = "global_bpm"
    schema_version: str = SCHEMA_TEMPO_ANALYSIS_V1
    notes: str | None = None

    def __post_init__(self) -> None:
        self.candidates = tuple(self.candidates)
        self.channel_mode = _parse_optional_channel_mode(self.channel_mode)
        self.selected_candidate_rank = None if self.selected_candidate_rank is None else max(1, int(self.selected_candidate_rank))
        self.beat_anchors = tuple(self.beat_anchors)
        self.tempo_segments = tuple(self.tempo_segments)
        self.analysis_basis = str(self.analysis_basis).strip() or "global_bpm"
        self.schema_version = str(self.schema_version).strip() or SCHEMA_TEMPO_ANALYSIS_V1
        self.notes = _optional_text(self.notes)

    def with_channel_mode(self, channel_mode: ChannelMode) -> "TempoAnalysisResult":
        return replace(self, channel_mode=channel_mode)

    def primary_candidate(self) -> TempoAnalysisCandidate | None:
        if not self.candidates:
            return None
        if self.selected_candidate_rank is not None:
            for candidate in self.candidates:
                if candidate.candidate_rank == self.selected_candidate_rank:
                    return candidate
        return min(self.candidates, key=lambda candidate: candidate.candidate_rank)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "analysis_basis": self.analysis_basis,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "beat_anchors": [anchor.to_dict() for anchor in self.beat_anchors],
            "tempo_segments": [segment.to_dict() for segment in self.tempo_segments],
        }
        if self.channel_mode is not None:
            payload["channel_mode"] = self.channel_mode.value
        if self.selected_candidate_rank is not None:
            payload["selected_candidate_rank"] = self.selected_candidate_rank
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TempoAnalysisResult":
        raw_candidates = payload.get("candidates")
        raw_anchors = payload.get("beat_anchors")
        raw_segments = payload.get("tempo_segments")
        return cls(
            candidates=tuple(
                TempoAnalysisCandidate.from_dict(candidate)
                for candidate in raw_candidates
                if isinstance(candidate, dict)
            )
            if isinstance(raw_candidates, list)
            else (),
            channel_mode=_parse_optional_channel_mode(payload.get("channel_mode")),
            selected_candidate_rank=(
                None if payload.get("selected_candidate_rank") is None else int(payload.get("selected_candidate_rank", 1))
            ),
            beat_anchors=tuple(
                BeatAnchor.from_dict(anchor)
                for anchor in raw_anchors
                if isinstance(anchor, dict)
            )
            if isinstance(raw_anchors, list)
            else (),
            tempo_segments=tuple(
                TempoSegment.from_dict(segment)
                for segment in raw_segments
                if isinstance(segment, dict)
            )
            if isinstance(raw_segments, list)
            else (),
            analysis_basis=str(payload.get("analysis_basis", "global_bpm") or "global_bpm"),
            schema_version=str(payload.get("schema_version", SCHEMA_TEMPO_ANALYSIS_V1) or SCHEMA_TEMPO_ANALYSIS_V1),
            notes=_optional_text(payload.get("notes")),
        )


@dataclass(slots=True)
class ChordSegment:
    start_seconds: float
    end_seconds: float
    label: str
    confidence: float = 0.0
    start_beat: float | None = None
    end_beat: float | None = None

    def __post_init__(self) -> None:
        self.start_seconds = float(self.start_seconds)
        self.end_seconds = float(self.end_seconds)
        self.label = str(self.label).strip() or "N.C."
        self.confidence = _clamp_confidence(self.confidence)
        self.start_beat = _optional_float(self.start_beat)
        self.end_beat = _optional_float(self.end_beat)
        if self.end_seconds < self.start_seconds:
            raise ValueError("chord segment 的结束时间不可早于开始时间")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "label": self.label,
            "confidence": self.confidence,
        }
        if self.start_beat is not None:
            payload["start_beat"] = self.start_beat
        if self.end_beat is not None:
            payload["end_beat"] = self.end_beat
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChordSegment":
        return cls(
            start_seconds=float(payload.get("start_seconds", 0.0)),
            end_seconds=float(payload.get("end_seconds", 0.0)),
            label=str(payload.get("label", "N.C.") or "N.C."),
            confidence=float(payload.get("confidence", 0.0)),
            start_beat=_optional_float(payload.get("start_beat")),
            end_beat=_optional_float(payload.get("end_beat")),
        )


@dataclass(slots=True)
class ChordAnalysisResult:
    segments: tuple[ChordSegment, ...] = ()
    channel_mode: ChannelMode | None = None
    analysis_basis: str = "fixed_window"
    schema_version: str = SCHEMA_CHORD_ANALYSIS_V1
    window_seconds: float | None = None
    grid_aligned: bool = False
    notes: str | None = None

    def __post_init__(self) -> None:
        self.segments = tuple(self.segments)
        self.channel_mode = _parse_optional_channel_mode(self.channel_mode)
        self.analysis_basis = str(self.analysis_basis).strip() or "fixed_window"
        self.schema_version = str(self.schema_version).strip() or SCHEMA_CHORD_ANALYSIS_V1
        self.window_seconds = _optional_float(self.window_seconds)
        self.grid_aligned = bool(self.grid_aligned)
        self.notes = _optional_text(self.notes)

    def with_channel_mode(self, channel_mode: ChannelMode) -> "ChordAnalysisResult":
        return replace(self, channel_mode=channel_mode)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "analysis_basis": self.analysis_basis,
            "segments": [segment.to_dict() for segment in self.segments],
            "grid_aligned": self.grid_aligned,
        }
        if self.channel_mode is not None:
            payload["channel_mode"] = self.channel_mode.value
        if self.window_seconds is not None:
            payload["window_seconds"] = self.window_seconds
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChordAnalysisResult":
        raw_segments = payload.get("segments")
        return cls(
            segments=tuple(
                ChordSegment.from_dict(segment)
                for segment in raw_segments
                if isinstance(segment, dict)
            )
            if isinstance(raw_segments, list)
            else (),
            channel_mode=_parse_optional_channel_mode(payload.get("channel_mode")),
            analysis_basis=str(payload.get("analysis_basis", "fixed_window") or "fixed_window"),
            schema_version=str(payload.get("schema_version", SCHEMA_CHORD_ANALYSIS_V1) or SCHEMA_CHORD_ANALYSIS_V1),
            window_seconds=_optional_float(payload.get("window_seconds")),
            grid_aligned=bool(payload.get("grid_aligned", False)),
            notes=_optional_text(payload.get("notes")),
        )


__all__ = [
    "SCHEMA_TEMPO_ANALYSIS_V1",
    "SCHEMA_CHORD_ANALYSIS_V1",
    "TempoAnalysisCandidate",
    "BeatAnchor",
    "TempoSegment",
    "TempoAnalysisResult",
    "ChordSegment",
    "ChordAnalysisResult",
]
