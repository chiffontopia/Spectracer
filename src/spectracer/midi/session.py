from __future__ import annotations

from typing import Final

from spectracer.midi.editor_model import (
    MidiChannelConfig,
    MidiEditorRuntimeState,
    MidiEditorState,
    MidiNote,
    MidiNoteId,
    MidiProjectState,
)

_MISSING: Final = object()


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


class MidiSession:
    def __init__(
        self,
        project_state: MidiProjectState | None = None,
        runtime_state: MidiEditorRuntimeState | None = None,
    ) -> None:
        self._notes_by_id: dict[str, MidiNote] = {}
        self._channel_configs_by_channel: dict[int, MidiChannelConfig] = {}
        self._selected_note_ids: set[str] = set()
        self._editor_state = MidiEditorState()
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

    def replace_state(self, project_state: MidiProjectState, runtime_state: MidiEditorRuntimeState | None = None) -> None:
        if not isinstance(project_state, MidiProjectState):
            raise TypeError("project_state 必须是 MidiProjectState")
        runtime = runtime_state if runtime_state is not None else MidiEditorRuntimeState()
        if not isinstance(runtime, MidiEditorRuntimeState):
            raise TypeError("runtime_state 必须是 MidiEditorRuntimeState")

        self._notes_by_id = {note.id: note for note in project_state.notes}
        self._channel_configs_by_channel = {config.channel: config for config in project_state.channel_configs}
        self._editor_state = runtime.editor_state
        self._selected_note_ids = {note_id for note_id in runtime.selected_note_ids if note_id in self._notes_by_id}

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
        self._editor_state = editor_state
        return self._editor_state

    def update_editor_state(self, **changes: object) -> MidiEditorState:
        self._editor_state = self._editor_state.with_updates(**changes)
        return self._editor_state

    def set_selected_note_ids(self, note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId]) -> tuple[MidiNote, ...]:
        normalized_ids = [note_id for note_id in _normalize_note_ids(note_ids) if note_id in self._notes_by_id]
        self._selected_note_ids = set(normalized_ids)
        return self.selected_notes()

    def select_note(self, note_id: MidiNoteId, *, additive: bool = False) -> tuple[MidiNote, ...]:
        note = self.require_note(note_id)
        if additive:
            self._selected_note_ids.add(note.id)
        else:
            self._selected_note_ids = {note.id}
        return self.selected_notes()

    def toggle_note_selection(self, note_id: MidiNoteId) -> tuple[MidiNote, ...]:
        note = self.require_note(note_id)
        if note.id in self._selected_note_ids:
            self._selected_note_ids.remove(note.id)
        else:
            self._selected_note_ids.add(note.id)
        return self.selected_notes()

    def clear_selection(self) -> None:
        self._selected_note_ids.clear()

    def selected_notes(self) -> tuple[MidiNote, ...]:
        selected = [note for note in self._notes_by_id.values() if note.id in self._selected_note_ids]
        return _sorted_notes(selected)

    def add_note(self, note: MidiNote, *, select: bool = False) -> MidiNote:
        if not isinstance(note, MidiNote):
            raise TypeError("note 必须是 MidiNote")
        if note.id in self._notes_by_id:
            raise ValueError(f"存在重复的 note id: {note.id}")
        self._notes_by_id[note.id] = note
        if select:
            self._selected_note_ids = {note.id}
        return note

    def add_notes(self, notes: list[MidiNote] | tuple[MidiNote, ...], *, select_new: bool = False) -> tuple[MidiNote, ...]:
        normalized_notes = tuple(notes)
        seen_batch_ids: set[str] = set()
        for note in normalized_notes:
            if not isinstance(note, MidiNote):
                raise TypeError("notes 只能包含 MidiNote")
            if note.id in self._notes_by_id or note.id in seen_batch_ids:
                raise ValueError(f"存在重复的 note id: {note.id}")
            seen_batch_ids.add(note.id)
        for note in normalized_notes:
            self._notes_by_id[note.id] = note
        if select_new:
            self._selected_note_ids = {note.id for note in normalized_notes}
        return _sorted_notes(list(normalized_notes))

    def remove_note(self, note_id: MidiNoteId) -> MidiNote:
        note = self.require_note(note_id)
        del self._notes_by_id[note.id]
        self._selected_note_ids.discard(note.id)
        return note

    def remove_notes(self, note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId]) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        notes_to_remove = [self.require_note(note_id) for note_id in normalized_ids]
        for note in notes_to_remove:
            del self._notes_by_id[note.id]
            self._selected_note_ids.discard(note.id)
        return _sorted_notes(notes_to_remove)

    def replace_note(self, note: MidiNote) -> MidiNote:
        if not isinstance(note, MidiNote):
            raise TypeError("note 必须是 MidiNote")
        self.require_note(note.id)
        self._notes_by_id[note.id] = note
        return note

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
    ) -> MidiNote:
        note = self.require_note(note_id)
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
        updated_note = note.with_updates(**changes)
        self._notes_by_id[note.id] = updated_note
        return updated_note

    def move_notes(
        self,
        note_ids: list[MidiNoteId] | tuple[MidiNoteId, ...] | set[MidiNoteId] | frozenset[MidiNoteId],
        *,
        delta_beats: float = 0.0,
        delta_pitch: int = 0,
    ) -> tuple[MidiNote, ...]:
        normalized_ids = _normalize_note_ids(note_ids)
        notes_to_move = [self.require_note(note_id) for note_id in normalized_ids]
        moved_notes = [note.shifted(delta_beats=delta_beats, delta_pitch=delta_pitch) for note in notes_to_move]
        for moved_note in moved_notes:
            self._notes_by_id[moved_note.id] = moved_note
        return _sorted_notes(moved_notes)

    def set_channel_config(self, config: MidiChannelConfig) -> MidiChannelConfig:
        if not isinstance(config, MidiChannelConfig):
            raise TypeError("config 必须是 MidiChannelConfig")
        self._channel_configs_by_channel[config.channel] = config
        return config

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
    ) -> MidiChannelConfig:
        config = self.get_channel_config(channel)
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
        updated_config = config.with_updates(**changes)
        self._channel_configs_by_channel[updated_config.channel] = updated_config
        return updated_config

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
        if replace_selection:
            self._selected_note_ids = {note.id for note in normalized_matches}
        else:
            self._selected_note_ids.update(note.id for note in normalized_matches)
        return normalized_matches


__all__ = ["MidiSession"]
