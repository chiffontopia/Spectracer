from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from spectracer.midi.editor_model import MidiNoteId, MidiProjectState

if TYPE_CHECKING:
    from spectracer.midi.session import MidiSession


_NOTE_FIELD_LABELS = {
    "pitch": "音高",
    "start_beat": "起点",
    "duration_beats": "时值",
    "velocity": "力度",
    "channel": "通道",
    "pan": "Pan",
}

_CHANNEL_FIELD_LABELS = {
    "name": "名称",
    "program": "Program",
    "bank": "Bank",
    "pan": "Pan",
    "color": "颜色",
    "muted": "静音",
    "solo": "独奏",
}


def _normalize_note_ids(
    note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_note_id in note_ids:
        note_id = str(raw_note_id).strip()
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        normalized.append(note_id)
    return tuple(normalized)


def _normalize_selected_note_ids(
    note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
) -> frozenset[str]:
    return frozenset(_normalize_note_ids(note_ids))


def _note_count_text(note_ids: tuple[str, ...]) -> str:
    count = len(note_ids)
    return "1 个 note" if count == 1 else f"{count} 个 note"


def _join_labels(fields: tuple[str, ...], mapping: dict[str, str]) -> str:
    labels = [mapping.get(field, field) for field in fields]
    return " / ".join(labels)


@dataclass(slots=True, frozen=True)
class MidiSessionEditSnapshot:
    project_state: MidiProjectState
    selected_note_ids: frozenset[MidiNoteId] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.project_state, MidiProjectState):
            raise TypeError("project_state 必须是 MidiProjectState")
        object.__setattr__(self, "selected_note_ids", _normalize_selected_note_ids(self.selected_note_ids))


class MidiSessionCommand:
    summary: str

    def undo(self, session: MidiSession) -> None:
        raise NotImplementedError

    def redo(self, session: MidiSession) -> None:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class _SnapshotCommand(MidiSessionCommand):
    summary: str
    before: MidiSessionEditSnapshot
    after: MidiSessionEditSnapshot

    def undo(self, session: MidiSession) -> None:
        session._restore_edit_snapshot(self.before)

    def redo(self, session: MidiSession) -> None:
        session._restore_edit_snapshot(self.after)


@dataclass(slots=True, frozen=True)
class AddNoteCommand(_SnapshotCommand):
    note_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> AddNoteCommand:
        normalized_ids = _normalize_note_ids(note_ids)
        return cls(summary=f"添加 {_note_count_text(normalized_ids)}", before=before, after=after, note_ids=normalized_ids)


@dataclass(slots=True, frozen=True)
class DeleteNotesCommand(_SnapshotCommand):
    note_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> DeleteNotesCommand:
        normalized_ids = _normalize_note_ids(note_ids)
        return cls(summary=f"删除 {_note_count_text(normalized_ids)}", before=before, after=after, note_ids=normalized_ids)


@dataclass(slots=True, frozen=True)
class MoveNotesCommand(_SnapshotCommand):
    note_ids: tuple[str, ...] = ()
    delta_beats: float = 0.0
    delta_pitch: int = 0

    @classmethod
    def create(
        cls,
        *,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        delta_beats: float,
        delta_pitch: int,
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> MoveNotesCommand:
        normalized_ids = _normalize_note_ids(note_ids)
        return cls(
            summary=f"移动 {_note_count_text(normalized_ids)}",
            before=before,
            after=after,
            note_ids=normalized_ids,
            delta_beats=float(delta_beats),
            delta_pitch=int(delta_pitch),
        )


@dataclass(slots=True, frozen=True)
class ResizeNotesCommand(_SnapshotCommand):
    note_ids: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        fields: tuple[str, ...] = ("duration_beats",),
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> ResizeNotesCommand:
        normalized_ids = _normalize_note_ids(note_ids)
        normalized_fields = tuple(str(field).strip() for field in fields if str(field).strip()) or ("duration_beats",)
        return cls(
            summary=f"调整 {_note_count_text(normalized_ids)} 时值",
            before=before,
            after=after,
            note_ids=normalized_ids,
            fields=normalized_fields,
        )


@dataclass(slots=True, frozen=True)
class UpdateNotePropertyCommand(_SnapshotCommand):
    note_ids: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        fields: tuple[str, ...],
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> UpdateNotePropertyCommand:
        normalized_ids = _normalize_note_ids(note_ids)
        normalized_fields = tuple(str(field).strip() for field in fields if str(field).strip())
        field_suffix = f"（{_join_labels(normalized_fields, _NOTE_FIELD_LABELS)}）" if normalized_fields else ""
        return cls(
            summary=f"修改 {_note_count_text(normalized_ids)} 属性{field_suffix}",
            before=before,
            after=after,
            note_ids=normalized_ids,
            fields=normalized_fields,
        )


@dataclass(slots=True, frozen=True)
class UpdateChannelConfigCommand(_SnapshotCommand):
    channel: int = 0
    fields: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        channel: int,
        fields: tuple[str, ...] = (),
        before: MidiSessionEditSnapshot,
        after: MidiSessionEditSnapshot,
    ) -> UpdateChannelConfigCommand:
        normalized_channel = int(channel)
        normalized_fields = tuple(str(field).strip() for field in fields if str(field).strip())
        field_suffix = f"（{_join_labels(normalized_fields, _CHANNEL_FIELD_LABELS)}）" if normalized_fields else ""
        return cls(
            summary=f"修改通道 {normalized_channel + 1:02d} 配置{field_suffix}",
            before=before,
            after=after,
            channel=normalized_channel,
            fields=normalized_fields,
        )


@dataclass(slots=True, frozen=True)
class CommandStackState:
    undo_count: int = 0
    redo_count: int = 0
    undo_text: str | None = None
    redo_text: str | None = None

    @property
    def can_undo(self) -> bool:
        return self.undo_count > 0

    @property
    def can_redo(self) -> bool:
        return self.redo_count > 0


class CommandStack:
    def __init__(self, *, limit: int = 128) -> None:
        normalized_limit = int(limit)
        self._limit = max(1, normalized_limit)
        self._commands: list[MidiSessionCommand] = []
        self._next_index = 0

    @property
    def undo_count(self) -> int:
        return self._next_index

    @property
    def redo_count(self) -> int:
        return len(self._commands) - self._next_index

    @property
    def can_undo(self) -> bool:
        return self.undo_count > 0

    @property
    def can_redo(self) -> bool:
        return self.redo_count > 0

    def state(self) -> CommandStackState:
        undo_text = self._commands[self._next_index - 1].summary if self.can_undo else None
        redo_text = self._commands[self._next_index].summary if self.can_redo else None
        return CommandStackState(
            undo_count=self.undo_count,
            redo_count=self.redo_count,
            undo_text=undo_text,
            redo_text=redo_text,
        )

    def clear(self) -> CommandStackState:
        self._commands.clear()
        self._next_index = 0
        return self.state()

    def push_applied(self, command: MidiSessionCommand) -> CommandStackState:
        if self._next_index < len(self._commands):
            del self._commands[self._next_index :]
        self._commands.append(command)
        if len(self._commands) > self._limit:
            overflow = len(self._commands) - self._limit
            del self._commands[:overflow]
        self._next_index = len(self._commands)
        return self.state()

    def undo(self, session: MidiSession) -> MidiSessionCommand | None:
        if not self.can_undo:
            return None
        target_index = self._next_index - 1
        command = self._commands[target_index]
        command.undo(session)
        self._next_index = target_index
        return command

    def redo(self, session: MidiSession) -> MidiSessionCommand | None:
        if not self.can_redo:
            return None
        command = self._commands[self._next_index]
        command.redo(session)
        self._next_index += 1
        return command


__all__ = [
    "AddNoteCommand",
    "CommandStack",
    "CommandStackState",
    "DeleteNotesCommand",
    "MidiSessionCommand",
    "MidiSessionEditSnapshot",
    "MoveNotesCommand",
    "ResizeNotesCommand",
    "UpdateChannelConfigCommand",
    "UpdateNotePropertyCommand",
]
