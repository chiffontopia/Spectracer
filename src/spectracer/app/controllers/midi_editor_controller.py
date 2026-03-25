from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import uuid4

from PyQt6.QtCore import QObject, QPoint, QRectF, Qt
from PyQt6.QtWidgets import QMenu, QWidget

from spectracer.midi.commands import DeleteNotesCommand
from spectracer.midi.editor_model import MidiEditorState, MidiEditorTool, MidiNote
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.ui.dialogs.midi_note_properties_dialog import MidiNotePropertiesDialog
from spectracer.ui.views.spectrogram_view import (
    MidiEditorContextMenuRequest,
    MidiEditorPointerEvent,
    SpectrogramView,
)


@dataclass(slots=True)
class _PlaceInteraction:
    anchor_beat: float
    pitch: int


@dataclass(slots=True)
class _SelectionBoxInteraction:
    anchor_beat: float
    anchor_pitch: int
    anchor_seconds: float
    anchor_plot_y: float
    additive: bool
    moved: bool = False


@dataclass(slots=True)
class _MoveSelectionInteraction:
    anchor_beat: float
    anchor_pitch: int
    note_ids: tuple[str, ...]
    original_notes: tuple[MidiNote, ...]
    moved: bool = False


@dataclass(slots=True)
class _ResizeSelectionInteraction:
    anchor_beat: float
    note_ids: tuple[str, ...]
    original_notes: tuple[MidiNote, ...]


@dataclass(slots=True)
class _EraseInteraction:
    erased_note_ids: set[str]


@dataclass(slots=True, frozen=True)
class _CopiedNotesClipboard:
    notes: tuple[MidiNote, ...]
    anchor_start_beat: float
    max_end_beat: float


class MidiEditorController(QObject):
    def __init__(
        self,
        view: SpectrogramView,
        *,
        session: MidiSession,
        timeline: MidiGridTimeline,
        menu_host: QWidget | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(view, SpectrogramView):
            raise TypeError("view 必须是 SpectrogramView")
        if not isinstance(session, MidiSession):
            raise TypeError("session 必须是 MidiSession")
        if not isinstance(timeline, MidiGridTimeline):
            raise TypeError("timeline 必须是 MidiGridTimeline")
        self._view = view
        self._session = session
        self._timeline = timeline
        self._menu_host = menu_host or view
        self._editor_state = session.editor_state
        self._place_interaction: _PlaceInteraction | None = None
        self._selection_box_interaction: _SelectionBoxInteraction | None = None
        self._move_selection_interaction: _MoveSelectionInteraction | None = None
        self._resize_selection_interaction: _ResizeSelectionInteraction | None = None
        self._erase_interaction: _EraseInteraction | None = None
        self._active_context_menu: QMenu | None = None
        self._copied_notes: _CopiedNotesClipboard | None = None
        self._connect_signals()

    def close(self) -> None:
        self._cancel_interaction(clear_selection_rect=True, clear_preview_rect=True)
        self._clear_active_context_menu()
        for signal, slot in (
            (self._view.midi_editor_pointer_pressed, self.handle_pointer_press),
            (self._view.midi_editor_pointer_moved, self.handle_pointer_move),
            (self._view.midi_editor_pointer_released, self.handle_pointer_release),
            (self._view.midi_editor_context_menu_requested, self.handle_context_menu_request),
            (self._session.editor_state_changed, self._on_editor_state_changed),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                continue

    def set_timeline(self, timeline: MidiGridTimeline) -> None:
        if not isinstance(timeline, MidiGridTimeline):
            raise TypeError("timeline 必须是 MidiGridTimeline")
        self._timeline = timeline

    def handle_pointer_press(self, event: MidiEditorPointerEvent) -> None:
        if not self._editor_state.enabled:
            return
        self._clear_active_context_menu()
        self._cancel_interaction(clear_selection_rect=True, clear_preview_rect=True)
        tool = self._editor_state.tool
        if tool is MidiEditorTool.PLACE:
            self._start_place_interaction(event)
            return
        if tool is MidiEditorTool.SELECT:
            self._start_select_interaction(event)
            return
        if tool is MidiEditorTool.ERASE:
            self._start_erase_interaction(event)

    def handle_pointer_move(self, event: MidiEditorPointerEvent) -> None:
        if not self._editor_state.enabled:
            return
        if self._place_interaction is not None:
            self._update_place_preview(event)
            return
        if self._resize_selection_interaction is not None:
            self._update_resize_selection_preview(event)
            return
        if self._move_selection_interaction is not None:
            self._update_move_selection_preview(event)
            return
        if self._selection_box_interaction is not None:
            self._update_selection_box(event)
            return
        if self._erase_interaction is not None:
            self._erase_at_pointer(event)

    def handle_pointer_release(self, event: MidiEditorPointerEvent) -> None:
        if self._place_interaction is not None:
            self._finish_place_interaction(event)
            return
        if self._resize_selection_interaction is not None:
            self._finish_resize_selection_interaction(event)
            return
        if self._move_selection_interaction is not None:
            self._finish_move_selection_interaction(event)
            return
        if self._selection_box_interaction is not None:
            self._finish_selection_box_interaction(event)
            return
        if self._erase_interaction is not None:
            self._finish_erase_interaction()

    def handle_context_menu_request(self, request: MidiEditorContextMenuRequest) -> None:
        if not self._editor_state.enabled:
            return
        note = self._view.midi_note_at(request.pointer_event.seconds, request.pointer_event.plot_y)
        if note is not None:
            if note.id not in self._session.selected_note_ids:
                self._session.select_note(note.id)
            menu = self.build_selection_context_menu(parent=self._menu_host)
        else:
            menu = self.build_blank_context_menu(
                target_beat=self._absolute_beat_from_pointer(request.pointer_event, snap=False),
                parent=self._menu_host,
            )
        if menu is None:
            return
        self._clear_active_context_menu()
        self._active_context_menu = menu
        menu.aboutToHide.connect(self._clear_active_context_menu)
        menu.popup(QPoint(request.global_pos))

    def build_selection_context_menu(self, *, parent: QWidget | None = None) -> QMenu | None:
        selected_notes = self._session.selected_notes()
        if not selected_notes:
            return None
        menu = QMenu(parent or self._menu_host)
        copy_action = menu.addAction("复制")
        paste_action = menu.addAction("粘贴")
        paste_action.setEnabled(self._copied_notes is not None and bool(self._copied_notes.notes))
        menu.addSeparator()
        shift_up_action = menu.addAction("上移半音")
        shift_down_action = menu.addAction("下移半音")
        menu.addSeparator()
        properties_action = menu.addAction("属性…")
        menu.addSeparator()
        delete_action = menu.addAction("删除")

        copy_action.triggered.connect(lambda: self.copy_selected_notes())
        paste_action.triggered.connect(lambda: self.paste_copied_notes())
        shift_up_action.triggered.connect(lambda: self._move_selected_notes_by_semitone(+1))
        shift_down_action.triggered.connect(lambda: self._move_selected_notes_by_semitone(-1))
        properties_action.triggered.connect(lambda: self._edit_selected_note_properties(parent or self._menu_host))
        delete_action.triggered.connect(lambda: self.delete_selected_notes())
        return menu

    def build_blank_context_menu(self, *, target_beat: float, parent: QWidget | None = None) -> QMenu:
        menu = QMenu(parent or self._menu_host)
        paste_action = menu.addAction("在此粘贴")
        paste_action.setEnabled(self._copied_notes is not None and bool(self._copied_notes.notes))
        paste_action.triggered.connect(lambda _checked=False, beat=float(target_beat): self.paste_copied_notes(target_beat=beat))
        return menu

    def delete_selected_notes(self) -> tuple[MidiNote, ...]:
        selected_ids = tuple(self._session.selected_note_ids)
        if not selected_ids:
            return ()
        return self._session.remove_notes(selected_ids)

    def copy_selected_notes(self) -> tuple[MidiNote, ...]:
        selected_notes = self._session.selected_notes()
        if not selected_notes:
            return ()
        self._copied_notes = _CopiedNotesClipboard(
            notes=selected_notes,
            anchor_start_beat=min(note.start_beat for note in selected_notes),
            max_end_beat=max(note.end_beat for note in selected_notes),
        )
        return selected_notes

    def paste_copied_notes(self, *, target_beat: float | None = None) -> tuple[MidiNote, ...]:
        clipboard = self._copied_notes
        if clipboard is None or not clipboard.notes:
            return ()
        resolved_target_beat = self._default_paste_target_beat(clipboard) if target_beat is None else float(target_beat)
        resolved_target_beat = max(0.0, resolved_target_beat)
        if self._editor_state.snap_enabled:
            resolved_target_beat = self._editor_state.snap_resolution.quantize(resolved_target_beat)
        pasted_notes = tuple(
            note.with_updates(id=uuid4().hex, start_beat=resolved_target_beat + (note.start_beat - clipboard.anchor_start_beat))
            for note in clipboard.notes
        )
        return self._session.add_notes(pasted_notes, select_new=True)

    def _connect_signals(self) -> None:
        self._view.midi_editor_pointer_pressed.connect(self.handle_pointer_press)
        self._view.midi_editor_pointer_moved.connect(self.handle_pointer_move)
        self._view.midi_editor_pointer_released.connect(self.handle_pointer_release)
        self._view.midi_editor_context_menu_requested.connect(self.handle_context_menu_request)
        self._session.editor_state_changed.connect(self._on_editor_state_changed)

    def _on_editor_state_changed(self, editor_state: object) -> None:
        if not isinstance(editor_state, MidiEditorState):
            return
        self._editor_state = editor_state
        if not editor_state.enabled:
            self._cancel_interaction(clear_selection_rect=True, clear_preview_rect=True)
            self._clear_active_context_menu()

    def _cancel_interaction(self, *, clear_selection_rect: bool, clear_preview_rect: bool) -> None:
        self._place_interaction = None
        self._selection_box_interaction = None
        self._move_selection_interaction = None
        self._resize_selection_interaction = None
        self._erase_interaction = None
        if self._session is not None:
            self._session.commit_command_group()
        if clear_selection_rect:
            self._view.set_midi_selection_rect(None)
        if clear_preview_rect:
            self._view.set_midi_preview_rect(None)

    def _clear_active_context_menu(self) -> None:
        if self._active_context_menu is None:
            return
        self._active_context_menu.deleteLater()
        self._active_context_menu = None

    def _start_place_interaction(self, event: MidiEditorPointerEvent) -> None:
        anchor_beat = self._absolute_beat_from_pointer(event, snap=self._editor_state.snap_enabled)
        self._place_interaction = _PlaceInteraction(anchor_beat=anchor_beat, pitch=int(event.midi_pitch))
        self._view.set_midi_selection_rect(None)
        self._view.set_midi_preview_rect(self._build_place_preview_rect(anchor_beat, anchor_beat, int(event.midi_pitch)))

    def _update_place_preview(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._place_interaction
        if interaction is None:
            return
        current_beat = self._absolute_beat_from_pointer(event, snap=self._editor_state.snap_enabled)
        self._view.set_midi_preview_rect(self._build_place_preview_rect(interaction.anchor_beat, current_beat, interaction.pitch))

    def _finish_place_interaction(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._place_interaction
        self._place_interaction = None
        self._view.set_midi_preview_rect(None)
        if interaction is None:
            return
        current_beat = self._absolute_beat_from_pointer(event, snap=self._editor_state.snap_enabled)
        start_beat, duration_beats = self._resolve_place_note_range(interaction.anchor_beat, current_beat)
        note = MidiNote(
            pitch=interaction.pitch,
            start_beat=start_beat,
            duration_beats=duration_beats,
            channel=self._editor_state.active_channel,
        )
        self._session.add_note(note, select=True)

    def _start_select_interaction(self, event: MidiEditorPointerEvent) -> None:
        shift_pressed = self._has_shift_modifier(event)
        alt_pressed = self._has_alt_modifier(event)
        hit_note = self._view.midi_note_at(event.seconds, event.plot_y)
        if hit_note is not None:
            if shift_pressed and not alt_pressed:
                self._session.toggle_note_selection(hit_note.id)
                return
            if hit_note.id not in self._session.selected_note_ids:
                self._session.select_note(hit_note.id)
            selected_ids = tuple(self._session.selected_note_ids)
            original_notes = tuple(self._session.require_note(note_id) for note_id in selected_ids)
            if alt_pressed:
                self._resize_selection_interaction = _ResizeSelectionInteraction(
                    anchor_beat=self._absolute_beat_from_pointer(event, snap=False),
                    note_ids=selected_ids,
                    original_notes=original_notes,
                )
                return
            self._move_selection_interaction = _MoveSelectionInteraction(
                anchor_beat=self._absolute_beat_from_pointer(event, snap=False),
                anchor_pitch=int(event.midi_pitch),
                note_ids=selected_ids,
                original_notes=original_notes,
            )
            return
        self._selection_box_interaction = _SelectionBoxInteraction(
            anchor_beat=self._absolute_beat_from_pointer(event, snap=False),
            anchor_pitch=int(event.midi_pitch),
            anchor_seconds=float(event.seconds),
            anchor_plot_y=float(event.plot_y),
            additive=shift_pressed,
        )

    def _update_selection_box(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._selection_box_interaction
        if interaction is None or not self._editor_state.box_select_enabled:
            return
        interaction.moved = interaction.moved or self._pointer_drag_started(
            interaction.anchor_seconds,
            interaction.anchor_plot_y,
            event,
        )
        rect = QRectF(
            min(interaction.anchor_seconds, float(event.seconds)),
            min(interaction.anchor_plot_y, float(event.plot_y)),
            abs(float(event.seconds) - interaction.anchor_seconds),
            abs(float(event.plot_y) - interaction.anchor_plot_y),
        )
        self._view.set_midi_selection_rect(rect)

    def _finish_selection_box_interaction(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._selection_box_interaction
        self._selection_box_interaction = None
        self._view.set_midi_selection_rect(None)
        if interaction is None:
            return
        if interaction.moved and self._editor_state.box_select_enabled:
            self._session.select_notes_in_box(
                interaction.anchor_beat,
                self._absolute_beat_from_pointer(event, snap=False),
                interaction.anchor_pitch,
                int(event.midi_pitch),
                replace_selection=not interaction.additive,
                include_overlapping=True,
            )
            return
        if not interaction.additive:
            self._session.clear_selection()

    def _update_resize_selection_preview(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._resize_selection_interaction
        if interaction is None:
            return
        delta_beats = self._resize_delta_for_pointer(
            interaction.original_notes,
            anchor_beat=interaction.anchor_beat,
            event=event,
        )
        self._view.set_midi_preview_rect(self._build_resized_notes_preview_rect(interaction.original_notes, delta_beats))

    def _finish_resize_selection_interaction(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._resize_selection_interaction
        self._resize_selection_interaction = None
        self._view.set_midi_preview_rect(None)
        if interaction is None:
            return
        delta_beats = self._resize_delta_for_pointer(interaction.original_notes, anchor_beat=interaction.anchor_beat, event=event)
        if abs(delta_beats) <= 1e-9:
            return
        self._session.resize_notes(interaction.note_ids, delta_beats=delta_beats)

    def _update_move_selection_preview(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._move_selection_interaction
        if interaction is None:
            return
        delta_beats, delta_pitch = self._move_delta_for_pointer(
            interaction.original_notes,
            anchor_beat=interaction.anchor_beat,
            anchor_pitch=interaction.anchor_pitch,
            event=event,
        )
        interaction.moved = interaction.moved or (abs(delta_beats) > 1e-9 or delta_pitch != 0)
        self._view.set_midi_preview_rect(self._build_moved_notes_preview_rect(interaction.original_notes, delta_beats, delta_pitch))

    def _finish_move_selection_interaction(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._move_selection_interaction
        self._move_selection_interaction = None
        self._view.set_midi_preview_rect(None)
        if interaction is None:
            return
        delta_beats, delta_pitch = self._move_delta_for_pointer(
            interaction.original_notes,
            anchor_beat=interaction.anchor_beat,
            anchor_pitch=interaction.anchor_pitch,
            event=event,
        )
        if abs(delta_beats) <= 1e-9 and delta_pitch == 0:
            return
        self._session.move_notes(interaction.note_ids, delta_beats=delta_beats, delta_pitch=delta_pitch)

    def _start_erase_interaction(self, event: MidiEditorPointerEvent) -> None:
        interaction = _EraseInteraction(erased_note_ids=set())
        self._erase_interaction = interaction
        self._session.begin_command_group(
            lambda before, after, current=interaction: DeleteNotesCommand.create(
                note_ids=tuple(current.erased_note_ids),
                before=before,
                after=after,
            )
        )
        self._erase_at_pointer(event)

    def _erase_at_pointer(self, event: MidiEditorPointerEvent) -> None:
        interaction = self._erase_interaction
        if interaction is None:
            return
        hit_note = self._view.midi_note_at(event.seconds, event.plot_y)
        if hit_note is None or hit_note.id in interaction.erased_note_ids:
            return
        interaction.erased_note_ids.add(hit_note.id)
        self._session.remove_note(hit_note.id)

    def _finish_erase_interaction(self) -> None:
        interaction = self._erase_interaction
        self._erase_interaction = None
        if interaction is None:
            return
        self._session.commit_command_group()

    def _move_selected_notes_by_semitone(self, delta_pitch: int) -> None:
        selected_notes = self._session.selected_notes()
        if not selected_notes:
            return
        _, constrained_delta_pitch = self._constrain_move_delta(selected_notes, 0.0, int(delta_pitch))
        if constrained_delta_pitch == 0:
            return
        self._session.move_notes(tuple(note.id for note in selected_notes), delta_beats=0.0, delta_pitch=constrained_delta_pitch)

    def _edit_selected_note_properties(self, parent: QWidget | None) -> None:
        selected_notes = self._session.selected_notes()
        if not selected_notes:
            return
        dialog_result = MidiNotePropertiesDialog.get_properties(selected_notes=selected_notes, parent=parent)
        if dialog_result is None:
            return
        self._session.update_notes(tuple(note.id for note in selected_notes), velocity=dialog_result.velocity, pan=dialog_result.pan)

    def _absolute_beat_from_pointer(self, event: MidiEditorPointerEvent, *, snap: bool) -> float:
        beat = max(0.0, float(self._timeline.seconds_to_beat(float(event.seconds))))
        if not snap:
            return beat
        return self._editor_state.snap_resolution.quantize(beat)

    def _default_paste_target_beat(self, clipboard: _CopiedNotesClipboard) -> float:
        selected_notes = self._session.selected_notes()
        if selected_notes:
            return max(note.end_beat for note in selected_notes)
        return clipboard.max_end_beat

    def _resize_delta_for_pointer(
        self,
        original_notes: tuple[MidiNote, ...],
        *,
        anchor_beat: float,
        event: MidiEditorPointerEvent,
    ) -> float:
        current_beat = self._absolute_beat_from_pointer(event, snap=False)
        delta_beats = current_beat - float(anchor_beat)
        if self._editor_state.snap_enabled:
            delta_beats = self._quantize_delta(delta_beats)
        return self._constrain_resize_delta(original_notes, delta_beats)

    def _move_delta_for_pointer(
        self,
        original_notes: tuple[MidiNote, ...],
        *,
        anchor_beat: float,
        anchor_pitch: int,
        event: MidiEditorPointerEvent,
    ) -> tuple[float, int]:
        current_beat = self._absolute_beat_from_pointer(event, snap=False)
        delta_beats = current_beat - float(anchor_beat)
        if self._editor_state.snap_enabled:
            delta_beats = self._quantize_delta(delta_beats)
        delta_pitch = int(event.midi_pitch) - int(anchor_pitch)
        return self._constrain_move_delta(original_notes, delta_beats, delta_pitch)

    def _resolve_place_note_range(self, anchor_beat: float, current_beat: float) -> tuple[float, float]:
        minimum_length = self._minimum_note_length_beats()
        start_beat = min(float(anchor_beat), float(current_beat))
        end_beat = max(float(anchor_beat), float(current_beat))
        if end_beat - start_beat < minimum_length:
            if current_beat < anchor_beat:
                start_beat = max(0.0, float(anchor_beat) - minimum_length)
                end_beat = float(anchor_beat)
            else:
                start_beat = float(anchor_beat)
                end_beat = float(anchor_beat) + minimum_length
        return start_beat, max(minimum_length, end_beat - start_beat)

    def _build_place_preview_rect(self, anchor_beat: float, current_beat: float, pitch: int) -> QRectF | None:
        start_beat, duration_beats = self._resolve_place_note_range(anchor_beat, current_beat)
        return self._note_rect_for(MidiNote(pitch=pitch, start_beat=start_beat, duration_beats=duration_beats, channel=self._editor_state.active_channel))

    def _build_moved_notes_preview_rect(
        self,
        original_notes: Iterable[MidiNote],
        delta_beats: float,
        delta_pitch: int,
    ) -> QRectF | None:
        rects: list[QRectF] = []
        for note in original_notes:
            moved_note = note.shifted(delta_beats=delta_beats, delta_pitch=delta_pitch)
            rect = self._note_rect_for(moved_note)
            if rect is not None:
                rects.append(rect)
        return self._rect_union(rects)

    def _build_resized_notes_preview_rect(
        self,
        original_notes: Iterable[MidiNote],
        delta_beats: float,
    ) -> QRectF | None:
        rects: list[QRectF] = []
        for note in original_notes:
            resized_note = note.with_updates(duration_beats=note.duration_beats + delta_beats)
            rect = self._note_rect_for(resized_note)
            if rect is not None:
                rects.append(rect)
        return self._rect_union(rects)

    def _note_rect_for(self, note: MidiNote) -> QRectF | None:
        pitch_band = self._view.midi_pitch_band(note.pitch)
        if pitch_band is None:
            return None
        start_seconds = float(self._timeline.beat_to_seconds(note.start_beat))
        end_seconds = float(self._timeline.beat_to_seconds(note.end_beat))
        return QRectF(
            min(start_seconds, end_seconds),
            float(pitch_band[0]),
            max(1e-9, abs(end_seconds - start_seconds)),
            max(1.0, float(pitch_band[1]) - float(pitch_band[0])),
        )

    def _minimum_note_length_beats(self) -> float:
        return float(self._editor_state.snap_resolution.beat_length)

    def _quantize_delta(self, delta: float) -> float:
        normalized = float(delta)
        if normalized == 0.0:
            return 0.0
        sign = -1.0 if normalized < 0.0 else 1.0
        return sign * self._editor_state.snap_resolution.quantize(abs(normalized))

    def _constrain_move_delta(self, notes: Iterable[MidiNote], delta_beats: float, delta_pitch: int) -> tuple[float, int]:
        normalized_notes = tuple(notes)
        if not normalized_notes:
            return 0.0, 0
        min_start_beat = min(note.start_beat for note in normalized_notes)
        min_pitch = min(note.pitch for note in normalized_notes)
        max_pitch = max(note.pitch for note in normalized_notes)
        constrained_delta_beats = max(float(delta_beats), -float(min_start_beat))
        constrained_delta_pitch = int(delta_pitch)
        constrained_delta_pitch = max(constrained_delta_pitch, -int(min_pitch))
        constrained_delta_pitch = min(constrained_delta_pitch, 127 - int(max_pitch))
        return constrained_delta_beats, constrained_delta_pitch

    def _constrain_resize_delta(self, notes: Iterable[MidiNote], delta_beats: float) -> float:
        normalized_notes = tuple(notes)
        if not normalized_notes:
            return 0.0
        minimum_length = self._minimum_note_length_beats() if self._editor_state.snap_enabled else 1e-6
        lower_bound = max(min(note.duration_beats, minimum_length) - note.duration_beats for note in normalized_notes)
        return max(float(delta_beats), lower_bound)

    def _pointer_drag_started(
        self,
        anchor_seconds: float,
        anchor_plot_y: float,
        event: MidiEditorPointerEvent,
    ) -> bool:
        return abs(float(event.seconds) - float(anchor_seconds)) > 1e-3 or abs(float(event.plot_y) - float(anchor_plot_y)) > 0.35

    def _has_shift_modifier(self, event: MidiEditorPointerEvent) -> bool:
        return bool(event.modifiers & Qt.KeyboardModifier.ShiftModifier)

    def _has_alt_modifier(self, event: MidiEditorPointerEvent) -> bool:
        return bool(event.modifiers & Qt.KeyboardModifier.AltModifier)

    def _rect_union(self, rects: Iterable[QRectF]) -> QRectF | None:
        rect_list = [QRectF(rect) for rect in rects if rect is not None and not rect.isNull()]
        if not rect_list:
            return None
        merged = QRectF(rect_list[0])
        for rect in rect_list[1:]:
            merged = merged.united(rect)
        return merged


__all__ = ["MidiEditorController"]
