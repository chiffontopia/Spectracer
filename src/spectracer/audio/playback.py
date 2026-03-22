from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer


@dataclass(slots=True)
class PlaybackState:
    is_playing: bool = False
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    sample_rate: int = 0
    source_path: str | None = None
    playback_rate: float = 1.0
    volume: float = 0.85


class PlaybackEngine(QObject):
    """基于 PyQt6 QMediaPlayer 的最小播放引擎。"""

    position_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    playback_state_changed = pyqtSignal(bool)
    media_loaded = pyqtSignal(str)
    playback_rate_changed = pyqtSignal(float)
    volume_changed = pyqtSignal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = PlaybackState()

        self._pending_position_seconds: float | None = None
        self._pending_autoplay = False
        self._loading_source = False

        self._finalize_timer = QTimer(self)
        self._finalize_timer.setSingleShot(True)
        self._finalize_timer.timeout.connect(self._finalize_pending_load)

        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(self.state.volume)

        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setPlaybackRate(self.state.playback_rate)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

    def set_playback_rate(self, rate: float) -> None:
        clamped = max(0.25, min(4.0, float(rate)))
        self.state.playback_rate = clamped
        self._player.setPlaybackRate(clamped)
        self.playback_rate_changed.emit(clamped)

    def load(
        self,
        path: str | Path,
        *,
        position_seconds: float = 0.0,
        autoplay: bool = False,
    ) -> None:
        audio_path = Path(path).expanduser().resolve()
        self.state.source_path = str(audio_path)
        self.state.position_seconds = max(0.0, float(position_seconds))
        self.state.duration_seconds = 0.0

        self._pending_position_seconds = self.state.position_seconds
        self._pending_autoplay = bool(autoplay)
        self._loading_source = True
        # 兜底：如果底层 backend 没有触发 BufferedMedia，也应在短时间后完成 seek + autoplay。
        self._finalize_timer.start(350)
        self._player.setPlaybackRate(self.state.playback_rate)
        self._player.setSource(QUrl.fromLocalFile(str(audio_path)))

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def toggle(self) -> None:
        if self.state.is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.state.position_seconds = seconds
        self._player.setPosition(int(seconds * 1000.0))

    def set_volume(self, volume: float) -> None:
        clamped = max(0.0, min(1.0, float(volume)))
        self.state.volume = clamped
        self._audio_output.setVolume(clamped)
        self.volume_changed.emit(clamped)

    def _on_position_changed(self, position_ms: int) -> None:
        if self._loading_source:
            return
        self.state.position_seconds = max(0.0, position_ms / 1000.0)
        self.position_changed.emit(self.state.position_seconds)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.state.duration_seconds = max(0.0, duration_ms / 1000.0)
        self.duration_changed.emit(self.state.duration_seconds)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.state.is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playback_state_changed.emit(self.state.is_playing)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status not in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            return

        # 只在加载阶段处理，避免 LoadedMedia / BufferedMedia 多次触发时重复打断播放。
        if not self._loading_source:
            return

        # LoadedMedia 阶段可能还不支持稳定 seek（尤其是切换播放源时），先尝试定位，但不结束 loading。
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            pending_position = self._pending_position_seconds
            if pending_position is not None:
                self._player.setPosition(int(max(0.0, pending_position) * 1000.0))
                self.state.position_seconds = max(0.0, float(pending_position))
                self.position_changed.emit(self.state.position_seconds)
            return

        # BufferedMedia：认为已经可以稳定定位并恢复播放。
        if status == QMediaPlayer.MediaStatus.BufferedMedia:
            self._finalize_pending_load()

    def _finalize_pending_load(self) -> None:
        if not self._loading_source:
            return

        pending_position = self._pending_position_seconds
        autoplay = self._pending_autoplay

        if pending_position is not None:
            self._player.setPosition(int(max(0.0, pending_position) * 1000.0))
            self.state.position_seconds = max(0.0, float(pending_position))
            self.position_changed.emit(self.state.position_seconds)

        self._pending_position_seconds = None
        self._pending_autoplay = False
        self._loading_source = False
        self._finalize_timer.stop()

        if autoplay:
            QTimer.singleShot(0, self._player.play)

        if self.state.source_path is not None:
            self.media_loaded.emit(self.state.source_path)
