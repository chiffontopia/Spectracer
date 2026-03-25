from __future__ import annotations

import os

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectracer.app.controllers import midi_editor_controller as midi_editor_controller_module
from spectracer.app.controllers.midi_editor_controller import MidiEditorController
from spectracer.core.models import CqtResult
from spectracer.midi.editor_model import MidiEditorTool, MidiNote
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.ui.dialogs.midi_note_properties_dialog import MidiNotePropertiesDialogResult
from spectracer.ui.overlays.midi_note_overlay import midi_note_to_frequency
from spectracer.ui.views.spectrogram_view import MidiEditorContextMenuRequest, MidiEditorPointerEvent, SpectrogramView


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_midi_editor_controller_place_tool_supports_click_and_drag(qapp: QApplication) -> None:
    view, session, controller, timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.PLACE,
        active_channel=4,
        snap_resolution="1/32",
    )
    qapp.processEvents()

    click_event = _pointer_event(view, seconds=0.26, pitch=60)
    controller.handle_pointer_press(click_event)
    assert view.midi_preview_rect() is not None
    controller.handle_pointer_release(click_event)
    qapp.processEvents()

    assert len(session.notes) == 1
    created = session.notes[0]
    assert created.pitch == 60
    assert created.channel == 4
    assert created.start_beat == pytest.approx(0.5)
    assert created.duration_beats == pytest.approx(0.125)
    assert session.selected_note_ids == {created.id}
    assert view.midi_preview_rect() is None

    drag_press_event = _pointer_event(view, seconds=0.76, pitch=62)
    drag_move_event = _pointer_event(view, seconds=1.39, pitch=62)
    controller.handle_pointer_press(drag_press_event)
    controller.handle_pointer_move(drag_move_event)
    preview_rect = view.midi_preview_rect()
    assert preview_rect is not None
    assert preview_rect.width() > 0.0
    controller.handle_pointer_release(drag_move_event)
    qapp.processEvents()

    assert len(session.notes) == 2
    dragged_note = session.notes[1]
    assert dragged_note.pitch == 62
    assert dragged_note.start_beat == pytest.approx(1.5)
    assert dragged_note.duration_beats == pytest.approx(1.25)

    controller.close()
    view.close()


def test_midi_editor_controller_select_tool_supports_click_box_select_and_drag_move(qapp: QApplication) -> None:
    notes = (
        MidiNote(id="note-a", pitch=60, start_beat=1.0, duration_beats=0.5),
        MidiNote(id="note-b", pitch=64, start_beat=2.0, duration_beats=0.5),
        MidiNote(id="note-c", pitch=67, start_beat=4.0, duration_beats=0.5),
    )
    view, session, controller, _timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.SELECT,
        snap_resolution="1/16",
        notes=notes,
    )
    qapp.processEvents()

    note_a_press = _pointer_event_for_note_center(view, notes[0])
    controller.handle_pointer_press(note_a_press)
    controller.handle_pointer_release(note_a_press)
    qapp.processEvents()
    assert session.selected_note_ids == {"note-a"}

    box_start = _pointer_event(view, seconds=0.40, pitch=65)
    box_end = _pointer_event(view, seconds=1.30, pitch=59)
    controller.handle_pointer_press(box_start)
    controller.handle_pointer_move(box_end)
    selection_rect = view.midi_selection_rect()
    assert selection_rect is not None
    controller.handle_pointer_release(box_end)
    qapp.processEvents()
    assert session.selected_note_ids == {"note-a", "note-b"}
    assert view.midi_selection_rect() is None

    drag_press = _pointer_event_for_note_center(view, session.require_note("note-a"))
    drag_move = _pointer_event(view, seconds=drag_press.seconds + 0.26, pitch=62)
    controller.handle_pointer_press(drag_press)
    controller.handle_pointer_move(drag_move)
    assert view.midi_preview_rect() is not None
    controller.handle_pointer_release(drag_move)
    qapp.processEvents()

    moved_a = session.require_note("note-a")
    moved_b = session.require_note("note-b")
    unchanged_c = session.require_note("note-c")
    assert moved_a.start_beat == pytest.approx(1.5)
    assert moved_a.pitch == 62
    assert moved_b.start_beat == pytest.approx(2.5)
    assert moved_b.pitch == 66
    assert unchanged_c.start_beat == pytest.approx(4.0)
    assert unchanged_c.pitch == 67
    assert session.undo_count == 1
    assert session.redo_count == 0

    session.undo()
    assert session.require_note("note-a").start_beat == pytest.approx(1.0)
    assert session.require_note("note-b").start_beat == pytest.approx(2.0)
    session.redo()
    assert session.require_note("note-a").start_beat == pytest.approx(1.5)
    assert view.midi_preview_rect() is None

    controller.close()
    view.close()


def test_midi_editor_controller_supports_copy_paste_and_alt_drag_resize(qapp: QApplication) -> None:
    notes = (
        MidiNote(id="copy-a", pitch=60, start_beat=1.0, duration_beats=0.5, channel=1),
        MidiNote(id="copy-b", pitch=64, start_beat=2.0, duration_beats=0.25, channel=3),
    )
    view, session, controller, timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.SELECT,
        notes=notes,
        snap_resolution="1/16",
    )
    qapp.processEvents()

    session.set_selected_note_ids([note.id for note in notes])
    copied = controller.copy_selected_notes()
    assert tuple(note.id for note in copied) == ("copy-a", "copy-b")

    pasted = controller.paste_copied_notes()
    qapp.processEvents()

    assert len(pasted) == 2
    assert {note.id for note in pasted}.isdisjoint({"copy-a", "copy-b"})
    assert pasted[0].start_beat == pytest.approx(2.25)
    assert pasted[0].duration_beats == pytest.approx(0.5)
    assert pasted[0].channel == 1
    assert pasted[1].start_beat == pytest.approx(3.25)
    assert pasted[1].duration_beats == pytest.approx(0.25)
    assert pasted[1].channel == 3
    assert session.selected_note_ids == {note.id for note in pasted}
    assert session.undo_count == 1

    resize_press = _pointer_event_for_note_center(view, pasted[0], modifiers=Qt.KeyboardModifier.AltModifier)
    resize_target_beat = pasted[0].start_beat + (pasted[0].duration_beats * 0.5) + 0.5
    resize_move = _pointer_event(
        view,
        seconds=float(timeline.beat_to_seconds(resize_target_beat)),
        pitch=pasted[0].pitch,
        modifiers=Qt.KeyboardModifier.AltModifier,
    )
    controller.handle_pointer_press(resize_press)
    controller.handle_pointer_move(resize_move)
    assert view.midi_preview_rect() is not None
    controller.handle_pointer_release(resize_move)
    qapp.processEvents()

    resized_a = session.require_note(pasted[0].id)
    resized_b = session.require_note(pasted[1].id)
    assert resized_a.start_beat == pytest.approx(2.25)
    assert resized_a.duration_beats == pytest.approx(1.0)
    assert resized_b.start_beat == pytest.approx(3.25)
    assert resized_b.duration_beats == pytest.approx(0.75)
    assert session.undo_count == 2
    assert view.midi_preview_rect() is None

    controller.close()
    view.close()


def test_midi_editor_controller_context_menu_supports_copy_and_paste_actions(qapp: QApplication) -> None:
    note = MidiNote(id="menu-copy", pitch=67, start_beat=2.0, duration_beats=0.5, channel=2)
    view, session, controller, _timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.SELECT,
        notes=(note,),
    )
    qapp.processEvents()

    session.select_note(note.id)
    menu = controller.build_selection_context_menu(parent=view)
    assert menu is not None
    action_texts = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert action_texts == ["复制", "粘贴", "上移半音", "下移半音", "属性…", "删除"]

    copy_action = next(action for action in menu.actions() if action.text() == "复制")
    paste_action = next(action for action in menu.actions() if action.text() == "粘贴")
    assert paste_action.isEnabled() is False

    copy_action.trigger()
    qapp.processEvents()

    menu.deleteLater()
    menu = controller.build_selection_context_menu(parent=view)
    assert menu is not None
    paste_action = next(action for action in menu.actions() if action.text() == "粘贴")
    assert paste_action.isEnabled() is True

    paste_action.trigger()
    qapp.processEvents()

    assert len(session.notes) == 2
    pasted_note = next(candidate for candidate in session.notes if candidate.id != note.id)
    assert pasted_note.pitch == 67
    assert pasted_note.start_beat == pytest.approx(2.5)
    assert pasted_note.duration_beats == pytest.approx(0.5)
    assert pasted_note.channel == 2
    assert session.selected_note_ids == {pasted_note.id}

    controller.close()
    menu.deleteLater()
    view.close()


def test_midi_editor_controller_blank_context_menu_supports_paste_at_clicked_beat(qapp: QApplication) -> None:
    note = MidiNote(id="blank-copy", pitch=60, start_beat=1.0, duration_beats=0.5, channel=3)
    view, session, controller, timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.SELECT,
        notes=(note,),
    )
    qapp.processEvents()

    target_beat = 3.0
    blank_menu = controller.build_blank_context_menu(target_beat=target_beat, parent=view)
    blank_action_texts = [action.text() for action in blank_menu.actions() if not action.isSeparator()]
    assert blank_action_texts == ["在此粘贴"]
    paste_action = next(action for action in blank_menu.actions() if action.text() == "在此粘贴")
    assert paste_action.isEnabled() is False
    blank_menu.deleteLater()

    session.select_note(note.id)
    controller.copy_selected_notes()

    blank_request = MidiEditorContextMenuRequest(
        pointer_event=_pointer_event(
            view,
            seconds=float(timeline.beat_to_seconds(target_beat)),
            pitch=72,
        ),
        global_pos=QPoint(12, 12),
    )
    controller.handle_context_menu_request(blank_request)
    qapp.processEvents()

    blank_menu = controller._active_context_menu
    assert blank_menu is not None
    assert [action.text() for action in blank_menu.actions() if not action.isSeparator()] == ["在此粘贴"]
    paste_action = next(action for action in blank_menu.actions() if action.text() == "在此粘贴")
    assert paste_action.isEnabled() is True
    paste_action.trigger()
    qapp.processEvents()

    pasted_note = next(candidate for candidate in session.notes if candidate.id != note.id)
    assert pasted_note.start_beat == pytest.approx(target_beat)
    assert pasted_note.duration_beats == pytest.approx(0.5)
    assert pasted_note.pitch == 60
    assert pasted_note.channel == 3
    assert session.selected_note_ids == {pasted_note.id}

    controller.close()
    view.close()


def test_midi_editor_controller_erase_tool_and_context_menu_actions_work(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = (
        MidiNote(id="erase-a", pitch=60, start_beat=1.0, duration_beats=0.5),
        MidiNote(id="erase-b", pitch=62, start_beat=2.0, duration_beats=0.5),
        MidiNote(id="erase-c", pitch=65, start_beat=3.0, duration_beats=0.5),
    )
    monkeypatch.setattr(
        midi_editor_controller_module.MidiNotePropertiesDialog,
        "get_properties",
        lambda *, selected_notes, parent=None: MidiNotePropertiesDialogResult(velocity=111, pan=24),
    )
    view, session, controller, _timeline = _make_view_session_controller(
        editor_tool=MidiEditorTool.ERASE,
        notes=notes,
    )
    qapp.processEvents()

    erase_press = _pointer_event_for_note_center(view, notes[0])
    erase_move = _pointer_event_for_note_center(view, notes[1])
    controller.handle_pointer_press(erase_press)
    controller.handle_pointer_move(erase_move)
    controller.handle_pointer_release(erase_move)
    qapp.processEvents()
    assert {note.id for note in session.notes} == {"erase-c"}
    assert session.undo_count == 1

    session.undo()
    assert {note.id for note in session.notes} == {"erase-a", "erase-b", "erase-c"}
    session.redo()
    assert {note.id for note in session.notes} == {"erase-c"}

    session.update_editor_state(tool=MidiEditorTool.SELECT)
    qapp.processEvents()
    request = MidiEditorContextMenuRequest(
        pointer_event=_pointer_event_for_note_center(view, session.require_note("erase-c")),
        global_pos=QPoint(8, 8),
    )
    controller.handle_context_menu_request(request)
    qapp.processEvents()
    assert session.selected_note_ids == {"erase-c"}

    menu = controller.build_selection_context_menu(parent=view)
    assert menu is not None
    action_texts = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert action_texts == ["复制", "粘贴", "上移半音", "下移半音", "属性…", "删除"]

    next(action for action in menu.actions() if action.text() == "上移半音").trigger()
    qapp.processEvents()
    assert session.require_note("erase-c").pitch == 66

    next(action for action in menu.actions() if action.text() == "属性…").trigger()
    qapp.processEvents()
    assert session.require_note("erase-c").velocity == 111
    assert session.require_note("erase-c").pan == 24

    next(action for action in menu.actions() if action.text() == "删除").trigger()
    qapp.processEvents()
    assert session.notes == ()

    controller.close()
    menu.deleteLater()
    view.close()


def _make_view_session_controller(
    *,
    editor_tool: MidiEditorTool,
    notes: tuple[MidiNote, ...] = (),
    active_channel: int = 0,
    snap_resolution: str = "1/16",
) -> tuple[SpectrogramView, MidiSession, MidiEditorController, MidiGridTimeline]:
    timeline = MidiGridTimeline.constant()
    session = MidiSession()
    session.set_editor_state(
        session.editor_state.with_updates(
            enabled=True,
            tool=editor_tool,
            active_channel=active_channel,
            snap_enabled=True,
            snap_resolution=snap_resolution,
        )
    )
    if notes:
        session.add_notes(notes, record_undo=False)
    view = SpectrogramView()
    view.resize(1280, 820)
    view.set_grid_timeline(timeline)
    view.set_midi_session(session)
    view.set_cqt_result(_make_test_cqt_result())
    view.show()
    controller = MidiEditorController(view, session=session, timeline=timeline, menu_host=view)
    return view, session, controller, timeline


def _pointer_event(view: SpectrogramView, *, seconds: float, pitch: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> MidiEditorPointerEvent:
    plot_y = view.pitch_to_plot_y(pitch)
    assert plot_y is not None
    return MidiEditorPointerEvent(seconds=float(seconds), plot_y=float(plot_y), midi_pitch=int(pitch), modifiers=modifiers)


def _pointer_event_for_note_center(
    view: SpectrogramView,
    note: MidiNote,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> MidiEditorPointerEvent:
    rect = view.midi_note_rect(note.id)
    assert rect is not None
    return MidiEditorPointerEvent(
        seconds=float(rect.center().x()),
        plot_y=float(rect.center().y()),
        midi_pitch=int(note.pitch),
        modifiers=modifiers,
    )


def _make_test_cqt_result() -> CqtResult:
    midi_pitches = np.arange(36, 85, dtype=np.float64)
    bin_frequencies = np.array([midi_note_to_frequency(pitch) for pitch in midi_pitches], dtype=np.float64)
    frame_times = np.linspace(0.0, 2.0, 9, dtype=np.float64)
    magnitude = np.zeros((frame_times.size, bin_frequencies.size), dtype=np.float32)
    return CqtResult(
        magnitude=magnitude,
        frame_times=frame_times,
        bin_frequencies=bin_frequencies,
        hop_length=512,
        sample_rate=22050,
    )
