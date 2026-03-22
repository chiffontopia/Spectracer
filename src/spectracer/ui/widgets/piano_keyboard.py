from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from spectracer.core.harmonics import bin_index_from_plot_y
from spectracer.core.pitch import frequency_to_midi, is_black_key, midi_to_note_name


class PianoKeyboardWidget(QWidget):
    """与热图垂直对齐的简化钢琴侧栏。"""

    note_triggered = pyqtSignal(int, float, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bin_frequencies: list[float] = []
        self._primary_bin: int | None = None
        self._harmonic_bins: set[int] = set()
        self._visible_y_min = 0.0
        self._visible_y_max = 0.0
        self.setMinimumWidth(96)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_bin_frequencies(self, frequencies: Any) -> None:
        self._bin_frequencies = [float(value) for value in frequencies]
        self._primary_bin = None
        self._harmonic_bins.clear()
        self._visible_y_min = 0.0
        self._visible_y_max = float(len(self._bin_frequencies))
        self.update()

    def set_visible_bin_range(self, y_min: float, y_max: float) -> None:
        if not self._bin_frequencies:
            return
        maximum = float(len(self._bin_frequencies))
        self._visible_y_min = max(0.0, min(float(y_min), maximum))
        self._visible_y_max = max(self._visible_y_min + 1.0, min(float(y_max), maximum))
        self.update()

    def set_active_bin(self, bin_index: int | None) -> None:
        self.set_highlight_bins(primary_bin=bin_index, harmonic_bins=[])

    def set_highlight_bins(self, primary_bin: int | None, harmonic_bins: Sequence[int]) -> None:
        self._primary_bin = primary_bin
        self._harmonic_bins = {int(bin_index) for bin_index in harmonic_bins if int(bin_index) != primary_bin}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), QColor("#202020"))

        if not self._bin_frequencies:
            painter.setPen(QColor("#BBBBBB"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No\nData")
            return

        visible_span = max(1.0, self._visible_y_max - self._visible_y_min)
        start_index = max(0, int(math.floor(self._visible_y_min)))
        end_index = min(len(self._bin_frequencies), int(math.ceil(self._visible_y_max)))

        label_font = QFont(self.font())
        label_font.setPointSize(8)
        painter.setFont(label_font)

        last_labeled_octave: str | None = None
        for index in range(start_index, end_index):
            rect = self._rect_for_bin(index, visible_span)
            if rect is None or rect.height() <= 0.0:
                continue

            frequency = self._bin_frequencies[index]
            midi = frequency_to_midi(frequency)
            note_name = midi_to_note_name(midi)
            black = is_black_key(midi)

            width = self.width() * (0.66 if black else 1.0)
            key_rect = QRectF(rect.x(), rect.y(), width, rect.height())
            base_color = QColor("#1A1A1A") if black else QColor("#F1F1F1")
            text_color = QColor("#EAEAEA") if black else QColor("#202020")

            painter.fillRect(key_rect, base_color)
            painter.setPen(QPen(QColor("#777777"), 1))
            painter.drawRect(key_rect)

            if index in self._harmonic_bins:
                painter.fillRect(rect, QColor(255, 140, 0, 75))
                painter.setPen(QPen(QColor("#FF9E3D"), 1))
                painter.drawRect(rect)

            if note_name.startswith("C") and last_labeled_octave != note_name and rect.height() >= 12.0:
                painter.setPen(text_color)
                painter.drawText(
                    rect.adjusted(6.0, 0.0, -6.0, 0.0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    note_name,
                )
                last_labeled_octave = note_name

            if self._primary_bin == index:
                painter.fillRect(rect, QColor(255, 214, 79, 120))
                painter.setPen(QPen(QColor("#FFD54F"), 2))
                painter.drawRect(rect)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        bin_index = self._bin_index_for_widget_y(float(event.position().y()))
        if bin_index is None:
            super().mousePressEvent(event)
            return

        frequency = float(self._bin_frequencies[bin_index])
        midi_note = int(round(frequency_to_midi(frequency)))
        note_name = midi_to_note_name(midi_note)
        self.set_active_bin(bin_index)
        self.note_triggered.emit(midi_note, frequency, note_name)
        event.accept()

    def _rect_for_bin(self, bin_index: int, visible_span: float) -> QRectF | None:
        bin_low = float(bin_index)
        bin_high = float(bin_index + 1)
        visible_low = max(bin_low, self._visible_y_min)
        visible_high = min(bin_high, self._visible_y_max)
        if visible_high <= visible_low:
            return None

        top = self.height() * (1.0 - ((visible_high - self._visible_y_min) / visible_span))
        bottom = self.height() * (1.0 - ((visible_low - self._visible_y_min) / visible_span))
        return QRectF(0.0, top, float(self.width()), max(1.0, bottom - top))

    def _bin_index_for_widget_y(self, y_position: float) -> int | None:
        if not self._bin_frequencies or self.height() <= 0:
            return None

        visible_span = max(1.0, self._visible_y_max - self._visible_y_min)
        y_ratio = max(0.0, min(1.0, float(y_position) / float(self.height())))
        plot_y = self._visible_y_max - (y_ratio * visible_span)

        try:
            return bin_index_from_plot_y(plot_y, len(self._bin_frequencies))
        except ValueError:
            return None
