from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, TypeVar

from PyQt6.QtCore import QObject, pyqtSignal

from spectracer.midi.commands import (
    AddNoteCommand,
    CommandStack,
    CommandStackState,
    DeleteNotesCommand,
    MidiSessionCommand,
    MidiSessionEditSnapshot,
    MoveNotesCommand,
    ResizeNotesCommand,
    UpdateChannelConfigCommand,
    UpdateNotePropertyCommand,
)
from spectracer.midi.editor_model import (
    MidiChannelConfig,
    MidiEditorRuntimeState,
    MidiEditorState,
    MidiNote,
    MidiNoteId,
    MidiProjectState,
)

_MISSING: Final = object()
_NOTE_MUTABLE_FIELDS: Final[tuple[str, ...]] = (
    "pitch",
    "start_beat",
    "duration_beats",
    "velocity",
    "channel",
    "pan",
)
_CHANNEL_MUTABLE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "program",
    "bank",
    "pan",
    "color",
    "muted",
    "solo",
)
_T = TypeVar("_T")


@dataclass(slots=True)
class _PendingCommandGroup:
    before: MidiSessionEditSnapshot
    factory: Callable[[MidiSessionEditSnapshot, MidiSessionEditSnapshot], MidiSessionCommand]


def _normalize_note_ids(note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_note_id in note_ids:
        note_id = str(raw_note_id).strip()
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        normalized.append(note_id)
    return tuple(normalized)


def _normalize_channel_filter(channels: list[int] | tuple[int, ...] | set[int] | frozenset[int] | None) -> set[int] | None:
    if channels is None:
        return None
    normalized: set[int] = set()
    for raw_channel in channels:
        channel = int(raw_channel)
        if channel < 0 or channel > 15:
            raise ValueError(f"channel 超出范围，期望 0~15，实际为 {channel}")
        normalized.add(channel)
    return normalized


def _sorted_notes(notes: list[MidiNote]) -> tuple[MidiNote, ...]:
    return tuple(sorted(notes, key=lambda item: (item.start_beat, item.pitch, item.channel, item.id)))


def _normalized_fields(fields: tuple[str, ...], allowed_fields: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(allowed_fields)
    normalized: list[str] = []
    for field in fields:
        name = str(field).strip()
        if name and name in allowed and name not in normalized:
            normalized.append(name)
    return tuple(normalized)


class MidiSession(QObject):
    notes_changed = pyqtSignal(object)
    selection_changed = pyqtSignal(object)
    editor_state_changed = pyqtSignal(object)
    channel_configs_changed = pyqtSignal(object)
    command_stack_changed = pyqtSignal(object)

    def __init__(
        self,
        project_state: MidiProjectState | None = None,
        runtime_state: MidiEditorRuntimeState | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._notes_by_id: dict[str, MidiNote] = {}
        self._channel_configs_by_channel: dict[int, MidiChannelConfig] = {}
        self._selected_note_ids: set[str] = set()
        self._editor_state = MidiEditorState()
        self._command_stack = CommandStack()
        self._active_command_group: _PendingCommandGroup | None = None
        self.replace_state(
            project_state if project_state is not None else MidiProjectState.empty(),
            runtime_state if runtime_state is not None else MidiEditorRuntimeState(),
        )

    @property
    def notes(self) -> tuple[MidiNote, ...]:
        return _sorted_notes(list(self._notes_by_id.values()))

    @property
    def channel_configs(self) -> tuple[MidiChannelConfig, ...]:
        return tuple(self._channel_configs_by_channel[channel] for channel in range(16))

    @property
    def selected_note_ids(self) -> frozenset[str]:
        return frozenset(self._selected_note_ids)

    @property
    def editor_state(self) -> MidiEditorState:
        return self._editor_state

    @property
    def project_state(self) -> MidiProjectState:
        return MidiProjectState(notes=self.notes, channel_configs=self.channel_configs)

    @property
    def runtime_state(self) -> MidiEditorRuntimeState:
        return MidiEditorRuntimeState(editor_state=self._editor_state, selected_note_ids=frozenset(self._selected_note_ids))

    @property
    def command_stack_state(self) -> CommandStackState:
        return self._command_stack.state()

    @property
    def can_undo(self) -> bool:
        return self._command_stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._command_stack.can_redo

    @property
    def undo_count(self) -> int:
        return self._command_stack.undo_count

    @property
    def redo_count(self) -> int:
        return self._command_stack.redo_count

    def replace_state(
        self,
        project_state: MidiProjectState,
        runtime_state: MidiEditorRuntimeState | None = None,
        *,
        clear_command_history: bool = True,
    ) -> None:
        if not isinstance(project_state, MidiProjectState):
            raise TypeError("project_state 必须是 MidiProjectState")
        runtime = runtime_state if runtime_state is not None else MidiEditorRuntimeState()
        if not isinstance(runtime, MidiEditorRuntimeState):
            raise TypeError("runtime_state 必须是 MidiEditorRuntimeState")

        self._notes_by_id = {note.id: note for note in project_state.notes}
        self._channel_configs_by_channel = {config.channel: config for config in project_state.channel_configs}
        self._editor_state = runtime.editor_state
        self._selected_note_ids = {note_id for note_id in runtime.selected_note_ids if note_id in self._notes_by_id}
        if clear_command_history:
            self._active_command_group = None
            self._command_stack.clear()
        self._emit_notes_changed()
        self._emit_selection_changed()
        self._emit_editor_state_changed()
        self._emit_channel_configs_changed()
        if clear_command_history:
            self._emit_command_stack_changed()

    def get_note(self, note_id: MidiNoteId) -> MidiNote | None:
        return self._notes_by_id.get(str(note_id).strip())

    def require_note(self, note_id: MidiNoteId) -> MidiNote:
        note = self.get_note(note_id)
        if note is None:
            raise KeyError(f"未知 note id: {note_id}")
        return note

    def get_channel_config(self, channel: int) -> MidiChannelConfig:
        normalized_channel = int(channel)
        if normalized_channel < 0 or normalized_channel > 15:
            raise ValueError(f"channel 超出范围，期望 0~15，实际为 {normalized_channel}")
        return self._channel_configs_by_channel[normalized_channel]

    def set_editor_state(self, editor_state: MidiEditorState) -> MidiEditorState:
        if not isinstance(editor_state, MidiEditorState):
            raise TypeError("editor_state 必须是 MidiEditorState")
        if self._editor_state == editor_state:
            return self._editor_state
        self._editor_state = editor_state
        self._emit_editor_state_changed()
        return self._editor_state

    def update_editor_state(self, **changes: object) -> MidiEditorState:
        return self.set_editor_state(self._editor_state.with_updates(**changes))

    def set_selected_note_ids(self, note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId]) -> tuple[MidiNote, ...]:
        normalized_ids = [note_id for note_id in _normalize_note_ids(note_ids) if note_id in self._notes_by_id]
        normalized_id_set = set(normalized_ids)
        if self._selected_note_ids == normalized_id_set:
            return self.selected_notes()
        self._selected_note_ids = set(normalized_ids)
        selected = self.selected_notes()
        self._emit_selection_changed(selected)
        return selected

    def select_note(self, note_id: MidiNoteId, *, additive: bool = False) -> tuple[MidiNote, ...]:
        note = self.require_note(note_id)
        previous_selection = frozenset(self._selected_note_ids)
        if additive:
            self._selected_note_ids.add(note.id)
        else:
            self._selected_note_ids = {note.id}
        selected = self.selected_notes()
        if self.selected_note_ids != previous_selection:
            self._emit_selection_changed(selected)
        return selected

    def toggle_note_selection(self, note_id: MidiNoteId) -> tuple[MidiNote, ...]:
        note = self.require_note(note_id)
        if note.id in self._selected_note_ids:
            self._selected_note_ids.remove(note.id)
        else:
            self._selected_note_ids.add(note.id)
        selected = self.selected_notes()
        self._emit_selection_changed(selected)
        return selected

    def clear_selection(self) -> None:
        if not self._selected_note_ids:
            return
        self._selected_note_ids.clear()
        self._emit_selection_changed(())

    def selected_notes(self) -> tuple[MidiNote, ...]:
        selected = [note for note in self._notes_by_id.values() if note.id in self._selected_note_ids]
        return _sorted_notes(selected)

    def add_note(self, note: MidiNote, *, select: bool = False, record_undo: bool = True) -> MidiNote:
        return self.add_notes((note,), select_new=select, record_undo=record_undo)[0]

    def add_notes(
        self,
        notes: list[MidiNote] | tuple[MidiNote, ...],
        *,
        select_new: bool = False,
        record_undo: bool = True,
    ) -> tuple[MidiNote, ...]:
        normalized_notes = tuple(notes)
        if not normalized_notes:
            return ()
        for note in normalized_notes:
            if not isinstance(note, MidiNote):
                raise TypeError("notes 只能包含 MidiNote")
        note_ids = tuple(note.id for note in normalized_notes)
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: AddNoteCommand.create(note_ids=note_ids, before=before, after=after),
            mutate=lambda: self._add_notes_impl(normalized_notes, select_new=select_new),
        )

    def remove_note(self, note_id: MidiNoteId, *, record_undo: bool = True) -> MidiNote:
        return self.remove_notes((note_id,), record_undo=record_undo)[0]

    def remove_notes(
        self,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        *,
        record_undo: bool = True,
    ) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        if not normalized_ids:
            return ()
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: DeleteNotesCommand.create(note_ids=normalized_ids, before=before, after=after),
            mutate=lambda: self._remove_notes_impl(normalized_ids),
        )

    def replace_note(self, note: MidiNote, *, record_undo: bool = True) -> MidiNote:
        if not isinstance(note, MidiNote):
            raise TypeError("note 必须是 MidiNote")
        previous_note = self.require_note(note.id)
        changed_fields = _normalized_fields(
            tuple(field for field in _NOTE_MUTABLE_FIELDS if getattr(previous_note, field) != getattr(note, field)),
            _NOTE_MUTABLE_FIELDS,
        )
        if not changed_fields:
            return previous_note
        command_factory = self._note_update_command_factory((note.id,), changed_fields)
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=command_factory,
            mutate=lambda: self._replace_note_impl(note),
        )

    def update_note(
        self,
        note_id: MidiNoteId,
        *,
        pitch: int | object = _MISSING,
        start_beat: float | object = _MISSING,
        duration_beats: float | object = _MISSING,
        velocity: int | object = _MISSING,
        channel: int | object = _MISSING,
        pan: int | None | object = _MISSING,
        record_undo: bool = True,
    ) -> MidiNote:
        updated = self.update_notes(
            (note_id,),
            pitch=pitch,
            start_beat=start_beat,
            duration_beats=duration_beats,
            velocity=velocity,
            channel=channel,
            pan=pan,
            record_undo=record_undo,
        )
        return updated[0]

    def update_notes(
        self,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        *,
        pitch: int | object = _MISSING,
        start_beat: float | object = _MISSING,
        duration_beats: float | object = _MISSING,
        velocity: int | object = _MISSING,
        channel: int | object = _MISSING,
        pan: int | None | object = _MISSING,
        record_undo: bool = True,
    ) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        if not normalized_ids:
            return ()
        changes = self._build_note_changes(
            pitch=pitch,
            start_beat=start_beat,
            duration_beats=duration_beats,
            velocity=velocity,
            channel=channel,
            pan=pan,
        )
        if not changes:
            return _sorted_notes([self.require_note(note_id) for note_id in normalized_ids])
        changed_fields = _normalized_fields(tuple(changes.keys()), _NOTE_MUTABLE_FIELDS)
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=self._note_update_command_factory(normalized_ids, changed_fields),
            mutate=lambda: self._update_notes_impl(normalized_ids, changes),
        )

    def move_notes(
        self,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        *,
        delta_beats: float = 0.0,
        delta_pitch: int = 0,
        record_undo: bool = True,
    ) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        if not normalized_ids:
            return ()
        normalized_delta_beats = float(delta_beats)
        normalized_delta_pitch = int(delta_pitch)
        if abs(normalized_delta_beats) <= 1e-9 and normalized_delta_pitch == 0:
            return _sorted_notes([self.require_note(note_id) for note_id in normalized_ids])
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: MoveNotesCommand.create(
                note_ids=normalized_ids,
                delta_beats=normalized_delta_beats,
                delta_pitch=normalized_delta_pitch,
                before=before,
                after=after,
            ),
            mutate=lambda: self._move_notes_impl(
                normalized_ids,
                delta_beats=normalized_delta_beats,
                delta_pitch=normalized_delta_pitch,
            ),
        )

    def resize_notes(
        self,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        *,
        duration_beats: float | None = None,
        delta_beats: float | None = None,
        record_undo: bool = True,
    ) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        if not normalized_ids:
            return ()
        if duration_beats is None and delta_beats is None:
            raise ValueError("duration_beats 与 delta_beats 至少需要提供一个")
        if duration_beats is not None and delta_beats is not None:
            raise ValueError("duration_beats 与 delta_beats 不可同时提供")
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: ResizeNotesCommand.create(
                note_ids=normalized_ids,
                before=before,
                after=after,
            ),
            mutate=lambda: self._resize_notes_impl(
                normalized_ids,
                duration_beats=duration_beats,
                delta_beats=delta_beats,
            ),
        )

    def set_channel_config(self, config: MidiChannelConfig, *, record_undo: bool = True) -> MidiChannelConfig:
        if not isinstance(config, MidiChannelConfig):
            raise TypeError("config 必须是 MidiChannelConfig")
        previous_config = self.get_channel_config(config.channel)
        changed_fields = _normalized_fields(
            tuple(field for field in _CHANNEL_MUTABLE_FIELDS if getattr(previous_config, field) != getattr(config, field)),
            _CHANNEL_MUTABLE_FIELDS,
        )
        if previous_config == config:
            return previous_config
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: UpdateChannelConfigCommand.create(
                channel=config.channel,
                fields=changed_fields,
                before=before,
                after=after,
            ),
            mutate=lambda: self._set_channel_config_impl(config),
        )

    def update_channel_config(
        self,
        channel: int,
        *,
        name: str | object = _MISSING,
        program: int | object = _MISSING,
        bank: int | object = _MISSING,
        pan: int | object = _MISSING,
        color: str | object = _MISSING,
        muted: bool | object = _MISSING,
        solo: bool | object = _MISSING,
        record_undo: bool = True,
    ) -> MidiChannelConfig:
        changes = self._build_channel_changes(
            name=name,
            program=program,
            bank=bank,
            pan=pan,
            color=color,
            muted=muted,
            solo=solo,
        )
        current = self.get_channel_config(channel)
        if not changes:
            return current
        changed_fields = _normalized_fields(tuple(changes.keys()), _CHANNEL_MUTABLE_FIELDS)
        return self._apply_mutation(
            record_undo=record_undo,
            command_factory=lambda before, after: UpdateChannelConfigCommand.create(
                channel=current.channel,
                fields=changed_fields,
                before=before,
                after=after,
            ),
            mutate=lambda: self._update_channel_config_impl(current.channel, changes),
        )

    def begin_command_group(
        self,
        factory: Callable[[MidiSessionEditSnapshot, MidiSessionEditSnapshot], MidiSessionCommand],
    ) -> None:
        if self._active_command_group is not None:
            raise RuntimeError("当前已存在活动中的命令分组")
        self._active_command_group = _PendingCommandGroup(before=self._capture_edit_snapshot(), factory=factory)

    def commit_command_group(self) -> MidiSessionCommand | None:
        group = self._active_command_group
        self._active_command_group = None
        if group is None:
            return None
        after = self._capture_edit_snapshot()
        if group.before == after:
            return None
        command = group.factory(group.before, after)
        self._push_command(command)
        return command

    def cancel_command_group(self, *, rollback: bool = False) -> None:
        group = self._active_command_group
        self._active_command_group = None
        if group is not None and rollback:
            self._restore_edit_snapshot(group.before)

    def clear_command_history(self) -> None:
        self._active_command_group = None
        self._command_stack.clear()
        self._emit_command_stack_changed()

    def undo(self) -> MidiSessionCommand | None:
        if self._active_command_group is not None:
            return None
        command = self._command_stack.undo(self)
        if command is not None:
            self._emit_command_stack_changed()
        return command

    def redo(self) -> MidiSessionCommand | None:
        if self._active_command_group is not None:
            return None
        command = self._command_stack.redo(self)
        if command is not None:
            self._emit_command_stack_changed()
        return command

    def notes_in_range(
        self,
        start_beat: float,
        end_beat: float,
        *,
        include_overlapping: bool = True,
        channels: list[int] | tuple[int, ...] | set[int] | frozenset[int] | None = None,
    ) -> tuple[MidiNote, ...]:
        normalized_start = float(start_beat)
        normalized_end = float(end_beat)
        if normalized_end < normalized_start:
            normalized_start, normalized_end = normalized_end, normalized_start
        channel_filter = _normalize_channel_filter(channels)

        matched: list[MidiNote] = []
        for note in self._notes_by_id.values():
            if channel_filter is not None and note.channel not in channel_filter:
                continue
            if include_overlapping:
                if note.overlaps_beat_range(normalized_start, normalized_end):
                    matched.append(note)
                continue
            if normalized_start <= note.start_beat <= normalized_end:
                matched.append(note)
        return _sorted_notes(matched)

    def hit_test(
        self,
        beat: float,
        pitch: int | float,
        *,
        beat_tolerance: float = 0.0,
        pitch_tolerance: int | float = 0,
        channels: list[int] | tuple[int, ...] | set[int] | frozenset[int] | None = None,
    ) -> MidiNote | None:
        normalized_beat = float(beat)
        normalized_pitch = float(pitch)
        normalized_beat_tolerance = max(0.0, float(beat_tolerance))
        normalized_pitch_tolerance = max(0.0, float(pitch_tolerance))
        channel_filter = _normalize_channel_filter(channels)

        candidates: list[tuple[float, float, float, str, MidiNote]] = []
        for note in self._notes_by_id.values():
            if channel_filter is not None and note.channel not in channel_filter:
                continue
            pitch_distance = abs(float(note.pitch) - normalized_pitch)
            if pitch_distance > normalized_pitch_tolerance:
                continue
            if not note.contains_beat(normalized_beat, tolerance=normalized_beat_tolerance):
                continue
            if note.start_beat <= normalized_beat <= note.end_beat:
                edge_distance = 0.0
            else:
                edge_distance = min(
                    abs(normalized_beat - note.start_beat),
                    abs(normalized_beat - note.end_beat),
                )
            candidates.append((pitch_distance, edge_distance, -note.start_beat, note.id, note))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return candidates[0][4]

    def select_notes_in_box(
        self,
        start_beat: float,
        end_beat: float,
        min_pitch: int | float,
        max_pitch: int | float,
        *,
        replace_selection: bool = True,
        include_overlapping: bool = True,
        channels: list[int] | tuple[int, ...] | set[int] | frozenset[int] | None = None,
    ) -> tuple[MidiNote, ...]:
        beat_start = float(start_beat)
        beat_end = float(end_beat)
        if beat_end < beat_start:
            beat_start, beat_end = beat_end, beat_start

        pitch_start = float(min_pitch)
        pitch_end = float(max_pitch)
        if pitch_end < pitch_start:
            pitch_start, pitch_end = pitch_end, pitch_start

        channel_filter = _normalize_channel_filter(channels)
        matched: list[MidiNote] = []
        for note in self._notes_by_id.values():
            if channel_filter is not None and note.channel not in channel_filter:
                continue
            if note.pitch < pitch_start or note.pitch > pitch_end:
                continue
            if include_overlapping:
                if note.overlaps_beat_range(beat_start, beat_end):
                    matched.append(note)
                continue
            if beat_start <= note.start_beat <= beat_end:
                matched.append(note)

        normalized_matches = _sorted_notes(matched)
        previous_selection = frozenset(self._selected_note_ids)
        if replace_selection:
            self._selected_note_ids = {note.id for note in normalized_matches}
        else:
            self._selected_note_ids.update(note.id for note in normalized_matches)
        if self.selected_note_ids != previous_selection:
            self._emit_selection_changed()
        return normalized_matches

    def _restore_edit_snapshot(self, snapshot: MidiSessionEditSnapshot) -> None:
        if not isinstance(snapshot, MidiSessionEditSnapshot):
            raise TypeError("snapshot 必须是 MidiSessionEditSnapshot")
        previous_notes = self.notes
        previous_channel_configs = self.channel_configs
        previous_selection = frozenset(self._selected_note_ids)

        self._notes_by_id = {note.id: note for note in snapshot.project_state.notes}
        self._channel_configs_by_channel = {config.channel: config for config in snapshot.project_state.channel_configs}
        self._selected_note_ids = {note_id for note_id in snapshot.selected_note_ids if note_id in self._notes_by_id}

        if self.notes != previous_notes:
            self._emit_notes_changed()
        if self.selected_note_ids != previous_selection:
            self._emit_selection_changed()
        if self.channel_configs != previous_channel_configs:
            self._emit_channel_configs_changed()

    def _capture_edit_snapshot(self) -> MidiSessionEditSnapshot:
        return MidiSessionEditSnapshot(
            project_state=self.project_state,
            selected_note_ids=frozenset(self._selected_note_ids),
        )

    def _push_command(self, command: MidiSessionCommand) -> None:
        self._command_stack.push_applied(command)
        self._emit_command_stack_changed()

    def _apply_mutation(
        self,
        *,
        record_undo: bool,
        command_factory: Callable[[MidiSessionEditSnapshot, MidiSessionEditSnapshot], MidiSessionCommand],
        mutate: Callable[[], _T],
    ) -> _T:
        if not record_undo or self._active_command_group is not None:
            return mutate()
        before = self._capture_edit_snapshot()
        result = mutate()
        after = self._capture_edit_snapshot()
        if before != after:
            self._push_command(command_factory(before, after))
        return result

    def _add_notes_impl(self, notes: tuple[MidiNote, ...], *, select_new: bool) -> tuple[MidiNote, ...]:
        seen_batch_ids: set[str] = set()
        for note in notes:
            if not isinstance(note, MidiNote):
                raise TypeError("notes 只能包含 MidiNote")
            if note.id in self._notes_by_id or note.id in seen_batch_ids:
                raise ValueError(f"存在重复的 note id: {note.id}")
            seen_batch_ids.add(note.id)
        for note in notes:
            self._notes_by_id[note.id] = note
        if select_new:
            self._selected_note_ids = {note.id for note in notes}
        added_notes = _sorted_notes(list(notes))
        self._emit_notes_changed()
        if select_new:
            self._emit_selection_changed(self.selected_notes())
        return added_notes

    def _remove_notes_impl(self, note_ids: tuple[str, ...]) -> tuple[MidiNote, ...]:
        notes_to_remove = [self.require_note(note_id) for note_id in note_ids]
        selection_changed = any(note.id in self._selected_note_ids for note in notes_to_remove)
        for note in notes_to_remove:
            del self._notes_by_id[note.id]
            self._selected_note_ids.discard(note.id)
        removed_notes = _sorted_notes(notes_to_remove)
        self._emit_notes_changed()
        if selection_changed:
            self._emit_selection_changed()
        return removed_notes

    def _replace_note_impl(self, note: MidiNote) -> MidiNote:
        self.require_note(note.id)
        self._notes_by_id[note.id] = note
        self._emit_notes_changed()
        return note

    def _update_notes_impl(self, note_ids: tuple[str, ...], changes: dict[str, object]) -> tuple[MidiNote, ...]:
        notes_to_update = [self.require_note(note_id) for note_id in note_ids]
        updated_notes = [note.with_updates(**changes) for note in notes_to_update]
        for updated_note in updated_notes:
            self._notes_by_id[updated_note.id] = updated_note
        normalized_updated_notes = _sorted_notes(updated_notes)
        self._emit_notes_changed()
        return normalized_updated_notes

    def _move_notes_impl(
        self,
        note_ids: tuple[str, ...],
        *,
        delta_beats: float,
        delta_pitch: int,
    ) -> tuple[MidiNote, ...]:
        notes_to_move = [self.require_note(note_id) for note_id in note_ids]
        moved_notes = [note.shifted(delta_beats=delta_beats, delta_pitch=delta_pitch) for note in notes_to_move]
        for moved_note in moved_notes:
            self._notes_by_id[moved_note.id] = moved_note
        normalized_moved_notes = _sorted_notes(moved_notes)
        self._emit_notes_changed()
        return normalized_moved_notes

    def _resize_notes_impl(
        self,
        note_ids: tuple[str, ...],
        *,
        duration_beats: float | None,
        delta_beats: float | None,
    ) -> tuple[MidiNote, ...]:
        notes_to_resize = [self.require_note(note_id) for note_id in note_ids]
        resized_notes: list[MidiNote] = []
        for note in notes_to_resize:
            target_duration = float(duration_beats) if duration_beats is not None else note.duration_beats + float(delta_beats)
            resized_notes.append(note.with_updates(duration_beats=target_duration))
        for resized_note in resized_notes:
            self._notes_by_id[resized_note.id] = resized_note
        normalized_resized_notes = _sorted_notes(resized_notes)
        self._emit_notes_changed()
        return normalized_resized_notes

    def _set_channel_config_impl(self, config: MidiChannelConfig) -> MidiChannelConfig:
        self._channel_configs_by_channel[config.channel] = config
        self._emit_channel_configs_changed()
        return config

    def _update_channel_config_impl(self, channel: int, changes: dict[str, object]) -> MidiChannelConfig:
        config = self.get_channel_config(channel)
        updated_config = config.with_updates(**changes)
        self._channel_configs_by_channel[updated_config.channel] = updated_config
        self._emit_channel_configs_changed()
        return updated_config

    def _build_note_changes(
        self,
        *,
        pitch: int | object = _MISSING,
        start_beat: float | object = _MISSING,
        duration_beats: float | object = _MISSING,
        velocity: int | object = _MISSING,
        channel: int | object = _MISSING,
        pan: int | None | object = _MISSING,
    ) -> dict[str, object]:
        changes: dict[str, object] = {}
        if pitch is not _MISSING:
            changes["pitch"] = pitch
        if start_beat is not _MISSING:
            changes["start_beat"] = start_beat
        if duration_beats is not _MISSING:
            changes["duration_beats"] = duration_beats
        if velocity is not _MISSING:
            changes["velocity"] = velocity
        if channel is not _MISSING:
            changes["channel"] = channel
        if pan is not _MISSING:
            changes["pan"] = pan
        return changes

    def _build_channel_changes(
        self,
        *,
        name: str | object = _MISSING,
        program: int | object = _MISSING,
        bank: int | object = _MISSING,
        pan: int | object = _MISSING,
        color: str | object = _MISSING,
        muted: bool | object = _MISSING,
        solo: bool | object = _MISSING,
    ) -> dict[str, object]:
        changes: dict[str, object] = {}
        if name is not _MISSING:
            changes["name"] = name
        if program is not _MISSING:
            changes["program"] = program
        if bank is not _MISSING:
            changes["bank"] = bank
        if pan is not _MISSING:
            changes["pan"] = pan
        if color is not _MISSING:
            changes["color"] = color
        if muted is not _MISSING:
            changes["muted"] = muted
        if solo is not _MISSING:
            changes["solo"] = solo
        return changes

    def _note_update_command_factory(
        self,
        note_ids: tuple[str, ...],
        fields: tuple[str, ...],
    ) -> Callable[[MidiSessionEditSnapshot, MidiSessionEditSnapshot], MidiSessionCommand]:
        normalized_fields = _normalized_fields(fields, _NOTE_MUTABLE_FIELDS)
        if normalized_fields == ("duration_beats",):
            return lambda before, after: ResizeNotesCommand.create(
                note_ids=note_ids,
                fields=normalized_fields,
                before=before,
                after=after,
            )
        return lambda before, after: UpdateNotePropertyCommand.create(
            note_ids=note_ids,
            fields=normalized_fields,
            before=before,
            after=after,
        )

    def _emit_notes_changed(self, notes: tuple[MidiNote, ...] | None = None) -> None:
        self.notes_changed.emit(self.notes if notes is None else tuple(notes))

    def _emit_selection_changed(self, notes: tuple[MidiNote, ...] | None = None) -> None:
        self.selection_changed.emit(self.selected_notes() if notes is None else tuple(notes))

    def _emit_editor_state_changed(self, editor_state: MidiEditorState | None = None) -> None:
        self.editor_state_changed.emit(self._editor_state if editor_state is None else editor_state)

    def _emit_channel_configs_changed(self, channel_configs: tuple[MidiChannelConfig, ...] | None = None) -> None:
        self.channel_configs_changed.emit(self.channel_configs if channel_configs is None else tuple(channel_configs))

    def _emit_command_stack_changed(self, state: CommandStackState | None = None) -> None:
        self.command_stack_changed.emit(self.command_stack_state if state is None else state)


__all__ = ["MidiSession"]
