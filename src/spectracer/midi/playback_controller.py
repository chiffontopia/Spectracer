from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer

from spectracer.audio.playback import PlaybackEngine
from spectracer.midi.grid import MidiGridTimeline
from spectracer.midi.session import MidiSession
from spectracer.midi.synth import DEFAULT_GAIN, MidiSynth


@dataclass(slots=True, frozen=True)
class ScheduledMidiNote:
    note_id: str
    pitch: int
    velocity: int
    channel: int
    start_seconds: float
    end_seconds: float

    @property
    def is_zero_length(self) -> bool:
        return self.end_seconds <= self.start_seconds


class MidiPlaybackController(QObject):
    def __init__(
        self,
        playback_engine: PlaybackEngine,
        midi_synth: MidiSynth,
        *,
        session: MidiSession | None = None,
        timeline: MidiGridTimeline | None = None,
        poll_interval_ms: int = 15,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._playback_engine = playback_engine
        self._midi_synth = midi_synth
        self._session = session if session is not None else MidiSession()
        self._timeline = timeline if timeline is not None else MidiGridTimeline.constant()
        self._enabled = True
        self._midi_gain = DEFAULT_GAIN
        self._poll_interval_ms = max(5, int(poll_interval_ms))
        self._scheduled_notes: tuple[ScheduledMidiNote, ...] = ()
        self._active_notes: dict[str, ScheduledMidiNote] = {}
        self._last_processed_seconds: float | None = None
        self._playback_anchor_seconds = 0.0
        self._playback_anchor_monotonic: float | None = None
        self._playback_anchor_rate = float(self._playback_engine.state.playback_rate)
        self._seek_resync_threshold_seconds = 0.5

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self._on_poll_timer)

        self._playback_engine.position_changed.connect(self._on_position_changed)
        self._playback_engine.playback_state_changed.connect(self._on_playback_state_changed)
        self._playback_engine.playback_rate_changed.connect(self._on_playback_rate_changed)
        self._playback_engine.media_loaded.connect(self._on_media_loaded)

        self.refresh_schedule()
        self.apply_channel_configs()
        self.set_midi_gain(self._midi_gain)

    @property
    def session(self) -> MidiSession:
        return self._session

    @property
    def timeline(self) -> MidiGridTimeline:
        return self._timeline

    @property
    def midi_gain(self) -> float:
        return self._midi_gain

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def scheduled_notes(self) -> tuple[ScheduledMidiNote, ...]:
        return self._scheduled_notes

    @property
    def active_note_ids(self) -> frozenset[str]:
        return frozenset(self._active_notes)

    def set_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._enabled == normalized:
            return
        self._enabled = normalized
        if not normalized:
            self._poll_timer.stop()
            self._release_all_notes()
            return
        self.sync_to_playback_position(current_seconds=self._playback_engine.state.position_seconds)
        if self._playback_engine.state.is_playing:
            self._poll_timer.start()

    def set_session(self, session: MidiSession) -> None:
        self._session = session
        self.refresh_schedule()
        self.apply_channel_configs()
        self.sync_to_playback_position(current_seconds=self._playback_engine.state.position_seconds)

    def set_timeline(self, timeline: MidiGridTimeline) -> None:
        self._timeline = timeline
        self.refresh_schedule()
        self.sync_to_playback_position(current_seconds=self._playback_engine.state.position_seconds)

    def set_synth(self, midi_synth: MidiSynth) -> None:
        self._release_all_notes()
        self._midi_synth = midi_synth
        self.apply_channel_configs()
        self.set_midi_gain(self._midi_gain)
        self.sync_to_playback_position(current_seconds=self._playback_engine.state.position_seconds)

    def set_midi_gain(self, value: float) -> None:
        self._midi_gain = max(0.0, min(1.0, float(value)))
        self._midi_synth.set_master_gain(self._midi_gain)

    def apply_channel_configs(self) -> None:
        for config in self._session.channel_configs:
            self._midi_synth.apply_channel_config(config)

    def refresh_schedule(self) -> tuple[ScheduledMidiNote, ...]:
        scheduled: list[ScheduledMidiNote] = []
        for note in self._session.notes:
            start_seconds = float(self._timeline.beat_to_seconds(note.start_beat))
            end_seconds = float(self._timeline.beat_to_seconds(note.end_beat))
            scheduled.append(
                ScheduledMidiNote(
                    note_id=note.id,
                    pitch=note.pitch,
                    velocity=note.velocity,
                    channel=note.channel,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
        scheduled.sort(key=lambda item: (item.start_seconds, item.channel, item.pitch, item.note_id))
        self._scheduled_notes = tuple(scheduled)
        return self._scheduled_notes

    def sync_to_playback_position(self, *, current_seconds: float | None = None) -> None:
        if current_seconds is None:
            current_seconds = self._playback_engine.state.position_seconds
        normalized_current = max(0.0, float(current_seconds))

        if not self._enabled:
            self._last_processed_seconds = normalized_current
            self._release_all_notes()
            return

        if not self._midi_synth.is_available:
            self._last_processed_seconds = normalized_current
            self._release_all_notes()
            return

        if not self._playback_engine.state.is_playing:
            self._last_processed_seconds = normalized_current
            self._release_all_notes()
            return

        previous_seconds = self._last_processed_seconds
        if previous_seconds is None:
            self._resync_active_notes(normalized_current)
            self._last_processed_seconds = normalized_current
            return

        if normalized_current < previous_seconds or (normalized_current - previous_seconds) > self._seek_resync_threshold_seconds:
            self._resync_active_notes(normalized_current)
            self._last_processed_seconds = normalized_current
            return

        self._advance_forward(previous_seconds, normalized_current)
        self._last_processed_seconds = normalized_current

    def close(self) -> None:
        self._poll_timer.stop()
        self._release_all_notes()

    def _on_position_changed(self, seconds: float) -> None:
        self._set_playback_anchor(seconds)
        if not self._playback_engine.state.is_playing:
            self._last_processed_seconds = max(0.0, float(seconds))
            return
        self.sync_to_playback_position(current_seconds=seconds)

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        current_seconds = max(0.0, float(self._playback_engine.state.position_seconds))
        if not is_playing:
            self._poll_timer.stop()
            self._playback_anchor_monotonic = None
            self._last_processed_seconds = current_seconds
            self._release_all_notes()
            return

        self._set_playback_anchor(current_seconds)
        self._last_processed_seconds = None
        self.sync_to_playback_position(current_seconds=current_seconds)
        if self._enabled:
            self._poll_timer.start()

    def _on_playback_rate_changed(self, rate: float) -> None:
        current_seconds = self._estimate_playback_seconds()
        self._playback_anchor_rate = float(rate)
        if self._playback_engine.state.is_playing:
            self._set_playback_anchor(current_seconds)

    def _on_media_loaded(self, _source_path: str) -> None:
        self._last_processed_seconds = max(0.0, float(self._playback_engine.state.position_seconds))
        self._set_playback_anchor(self._last_processed_seconds)
        self._release_all_notes()

    def _on_poll_timer(self) -> None:
        if not self._enabled or not self._playback_engine.state.is_playing:
            self._poll_timer.stop()
            self._release_all_notes()
            return
        self.sync_to_playback_position(current_seconds=self._estimate_playback_seconds())

    def _set_playback_anchor(self, seconds: float) -> None:
        self._playback_anchor_seconds = max(0.0, float(seconds))
        self._playback_anchor_monotonic = time.perf_counter()
        self._playback_anchor_rate = float(self._playback_engine.state.playback_rate)

    def _estimate_playback_seconds(self) -> float:
        anchor_monotonic = self._playback_anchor_monotonic
        if anchor_monotonic is None:
            return max(0.0, float(self._playback_engine.state.position_seconds))
        elapsed = max(0.0, time.perf_counter() - anchor_monotonic)
        estimate = self._playback_anchor_seconds + (elapsed * self._playback_anchor_rate)
        duration = max(0.0, float(self._playback_engine.state.duration_seconds))
        if duration > 0.0:
            estimate = min(estimate, duration)
        return max(0.0, estimate)

    def _advance_forward(self, previous_seconds: float, current_seconds: float) -> None:
        playable_channels = self._playable_channels()

        for note_id, scheduled in list(self._active_notes.items()):
            if scheduled.channel not in playable_channels or scheduled.end_seconds <= current_seconds:
                self._send_note_off(scheduled)
                self._active_notes.pop(note_id, None)

        for scheduled in self._scheduled_notes:
            if scheduled.channel not in playable_channels:
                continue
            if scheduled.note_id in self._active_notes:
                continue
            if scheduled.start_seconds > current_seconds:
                break
            if scheduled.start_seconds <= previous_seconds:
                continue
            self._send_note_on(scheduled)
            if scheduled.end_seconds > current_seconds and not scheduled.is_zero_length:
                self._active_notes[scheduled.note_id] = scheduled
                continue
            self._send_note_off(scheduled)

    def _resync_active_notes(self, current_seconds: float) -> None:
        desired = self._desired_active_notes(current_seconds)

        for note_id, scheduled in list(self._active_notes.items()):
            if note_id in desired:
                continue
            self._send_note_off(scheduled)
            self._active_notes.pop(note_id, None)

        for note_id, scheduled in desired.items():
            if note_id in self._active_notes:
                continue
            self._send_note_on(scheduled)
            self._active_notes[note_id] = scheduled

    def _desired_active_notes(self, current_seconds: float) -> dict[str, ScheduledMidiNote]:
        playable_channels = self._playable_channels()
        desired: dict[str, ScheduledMidiNote] = {}
        for scheduled in self._scheduled_notes:
            if scheduled.channel not in playable_channels:
                continue
            if scheduled.start_seconds > current_seconds:
                break
            if scheduled.is_zero_length:
                continue
            if scheduled.start_seconds <= current_seconds < scheduled.end_seconds:
                desired[scheduled.note_id] = scheduled
        return desired

    def _playable_channels(self) -> set[int]:
        solo_channels = {config.channel for config in self._session.channel_configs if config.solo and not config.muted}
        if solo_channels:
            return solo_channels
        return {config.channel for config in self._session.channel_configs if not config.muted}

    def _send_note_on(self, scheduled: ScheduledMidiNote) -> None:
        try:
            self._midi_synth.note_on(scheduled.pitch, velocity=scheduled.velocity, channel=scheduled.channel)
        except Exception:  # noqa: BLE001
            return

    def _send_note_off(self, scheduled: ScheduledMidiNote) -> None:
        try:
            self._midi_synth.note_off(scheduled.pitch, channel=scheduled.channel)
        except Exception:  # noqa: BLE001
            return

    def _release_all_notes(self) -> None:
        if not self._active_notes:
            return
        for scheduled in list(self._active_notes.values()):
            self._send_note_off(scheduled)
        self._active_notes.clear()


__all__ = ["MidiPlaybackController", "ScheduledMidiNote"]
