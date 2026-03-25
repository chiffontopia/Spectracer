from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from spectracer.audio.playback import PlaybackState
from spectracer.midi.editor_model import MidiChannelConfig, MidiNote, MidiProjectState
from spectracer.midi.playback_controller import MidiPlaybackController
from spectracer.midi.session import MidiSession
from spectracer.midi.synth import DEFAULT_GAIN


class _FakePlaybackEngine(QObject):
    position_changed = pyqtSignal(float)
    playback_state_changed = pyqtSignal(bool)
    playback_rate_changed = pyqtSignal(float)
    media_loaded = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.state = PlaybackState(duration_seconds=4.0)

    def set_position(self, seconds: float) -> None:
        self.state.position_seconds = float(seconds)
        self.position_changed.emit(self.state.position_seconds)

    def set_playing(self, playing: bool) -> None:
        self.state.is_playing = bool(playing)
        self.playback_state_changed.emit(self.state.is_playing)


class _FakeSynth:
    def __init__(self) -> None:
        self.is_available = True
        self.master_gain_calls: list[float] = []
        self.applied_channel_configs: list[MidiChannelConfig] = []
        self.note_on_calls: list[tuple[int, int, int]] = []
        self.note_off_calls: list[tuple[int, int]] = []

    def set_master_gain(self, value: float) -> None:
        self.master_gain_calls.append(float(value))

    def apply_channel_config(self, config: MidiChannelConfig) -> None:
        self.applied_channel_configs.append(config)

    def note_on(self, note: int, velocity: int = 100, *, channel: int | None = None) -> None:
        assert channel is not None
        self.note_on_calls.append((int(channel), int(note), int(velocity)))

    def note_off(self, note: int, *, channel: int | None = None) -> None:
        assert channel is not None
        self.note_off_calls.append((int(channel), int(note)))


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_midi_playback_controller_advances_notes_between_time_slices(qapp: QApplication) -> None:
    _ = qapp
    engine = _FakePlaybackEngine()
    synth = _FakeSynth()
    session = MidiSession(
        MidiProjectState(
            notes=(
                MidiNote(id="n1", pitch=60, start_beat=0.0, duration_beats=1.0, velocity=96, channel=0),
                MidiNote(id="n2", pitch=64, start_beat=1.0, duration_beats=1.0, velocity=88, channel=1),
            )
        )
    )

    controller = MidiPlaybackController(engine, synth, session=session, poll_interval_ms=10)

    engine.set_playing(True)
    controller.sync_to_playback_position(current_seconds=0.0)
    controller.sync_to_playback_position(current_seconds=0.6)
    controller.sync_to_playback_position(current_seconds=1.1)

    assert synth.note_on_calls == [
        (0, 60, 96),
        (1, 64, 88),
    ]
    assert synth.note_off_calls == [
        (0, 60),
        (1, 64),
    ]
    assert controller.active_note_ids == frozenset()


def test_midi_playback_controller_respects_solo_channels_and_forwards_gain(qapp: QApplication) -> None:
    _ = qapp
    engine = _FakePlaybackEngine()
    synth = _FakeSynth()
    session = MidiSession(
        MidiProjectState(
            notes=(
                MidiNote(id="a", pitch=60, start_beat=0.0, duration_beats=2.0, velocity=90, channel=0),
                MidiNote(id="b", pitch=67, start_beat=0.0, duration_beats=2.0, velocity=90, channel=1),
            ),
            channel_configs=(
                MidiChannelConfig(channel=0),
                MidiChannelConfig(channel=1, solo=True, program=40, pan=32),
            ),
        )
    )

    controller = MidiPlaybackController(engine, synth, session=session, poll_interval_ms=10)
    controller.set_midi_gain(0.4)
    engine.set_playing(True)
    controller.sync_to_playback_position(current_seconds=0.0)

    assert synth.master_gain_calls[0] == pytest.approx(DEFAULT_GAIN)
    assert synth.master_gain_calls[-1] == pytest.approx(0.4)
    assert any(config.channel == 1 and config.solo for config in synth.applied_channel_configs)
    assert synth.note_on_calls == [(1, 67, 90)]
    assert controller.active_note_ids == frozenset({"b"})


def test_midi_playback_controller_reacts_to_live_session_updates(qapp: QApplication) -> None:
    _ = qapp
    engine = _FakePlaybackEngine()
    synth = _FakeSynth()
    session = MidiSession()

    controller = MidiPlaybackController(engine, synth, session=session, poll_interval_ms=10)

    engine.set_playing(True)
    session.add_note(MidiNote(id="live", pitch=72, start_beat=0.0, duration_beats=2.0, velocity=84, channel=2))

    assert controller.scheduled_notes[0].note_id == "live"
    assert synth.note_on_calls == [(2, 72, 84)]
    assert controller.active_note_ids == frozenset({"live"})

    session.update_channel_config(2, muted=True)

    assert any(config.channel == 2 and config.muted for config in synth.applied_channel_configs)
    assert synth.note_off_calls[-1] == (2, 72)
    assert controller.active_note_ids == frozenset()
