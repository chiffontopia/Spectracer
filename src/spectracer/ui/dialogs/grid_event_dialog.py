from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from spectracer.midi.grid import TempoTransition


@dataclass(slots=True)
class TempoEventDialogResult:
    beat_position: float
    bpm: float
    transition: TempoTransition


@dataclass(slots=True)
class TimeSignatureEventDialogResult:
    beat_position: float
    numerator: int
    denominator: int


class TempoEventDialog(QDialog):
    def __init__(
        self,
        *,
        initial_beat_position: float,
        initial_bpm: float,
        initial_transition: TempoTransition,
        lock_beat_position: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tempo 事件")
        self.resize(420, 240)

        root_layout = QVBoxLayout(self)

        hint_label = QLabel(
            "Tempo 事件使用 quarter-note beat 作为内部时间坐标；该事件的过渡方式定义“从当前事件到下一事件”之间的 BPM 变化。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        form_layout = QFormLayout()

        self.beat_spin = QDoubleSpinBox(self)
        self.beat_spin.setRange(0.0, 1_000_000.0)
        self.beat_spin.setDecimals(6)
        self.beat_spin.setSingleStep(0.25)
        self.beat_spin.setSuffix(" beat")
        self.beat_spin.setValue(max(0.0, float(initial_beat_position)))
        self.beat_spin.setEnabled(not lock_beat_position)
        form_layout.addRow("位置", self.beat_spin)

        self.bpm_spin = QDoubleSpinBox(self)
        self.bpm_spin.setRange(1.0, 999.0)
        self.bpm_spin.setDecimals(3)
        self.bpm_spin.setSingleStep(0.5)
        self.bpm_spin.setSuffix(" BPM")
        self.bpm_spin.setValue(max(1.0, float(initial_bpm)))
        form_layout.addRow("BPM", self.bpm_spin)

        self.transition_combo = QComboBox(self)
        self.transition_combo.addItem("阶跃变化", TempoTransition.STEP)
        self.transition_combo.addItem("线性变化（实验性）", TempoTransition.LINEAR)
        self.transition_combo.setCurrentIndex(max(0, self.transition_combo.findData(TempoTransition.parse(initial_transition))))
        form_layout.addRow("过渡方式", self.transition_combo)

        root_layout.addLayout(form_layout)

        detail_label = QLabel(
            "提示：起始事件（beat 0）的位置固定不可移动；最后一个 Tempo 事件的过渡方式会在下一事件出现后才生效。"
        )
        detail_label.setWordWrap(True)
        root_layout.addWidget(detail_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

    def selected_event(self) -> TempoEventDialogResult:
        return TempoEventDialogResult(
            beat_position=float(self.beat_spin.value()),
            bpm=float(self.bpm_spin.value()),
            transition=TempoTransition.parse(self.transition_combo.currentData()),
        )

    @classmethod
    def get_event(
        cls,
        *,
        initial_beat_position: float,
        initial_bpm: float,
        initial_transition: TempoTransition,
        lock_beat_position: bool = False,
        parent=None,
    ) -> TempoEventDialogResult | None:
        dialog = cls(
            initial_beat_position=initial_beat_position,
            initial_bpm=initial_bpm,
            initial_transition=initial_transition,
            lock_beat_position=lock_beat_position,
            parent=parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_event()


class TimeSignatureEventDialog(QDialog):
    def __init__(
        self,
        *,
        initial_beat_position: float,
        initial_numerator: int,
        initial_denominator: int,
        lock_beat_position: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("拍号事件")
        self.resize(420, 220)

        root_layout = QVBoxLayout(self)

        hint_label = QLabel(
            "拍号事件同样使用 quarter-note beat 作为位置坐标；推荐将拍号变化放在小节线附近，避免产生难以理解的小节编号。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        form_layout = QFormLayout()

        self.beat_spin = QDoubleSpinBox(self)
        self.beat_spin.setRange(0.0, 1_000_000.0)
        self.beat_spin.setDecimals(6)
        self.beat_spin.setSingleStep(0.25)
        self.beat_spin.setSuffix(" beat")
        self.beat_spin.setValue(max(0.0, float(initial_beat_position)))
        self.beat_spin.setEnabled(not lock_beat_position)
        form_layout.addRow("位置", self.beat_spin)

        self.numerator_spin = QSpinBox(self)
        self.numerator_spin.setRange(1, 32)
        self.numerator_spin.setValue(max(1, int(initial_numerator)))
        form_layout.addRow("拍号分子", self.numerator_spin)

        self.denominator_combo = QComboBox(self)
        denominator_values = [1, 2, 4, 8, 16, 32]
        if int(initial_denominator) not in denominator_values:
            denominator_values.append(int(initial_denominator))
            denominator_values.sort()
        for value in denominator_values:
            self.denominator_combo.addItem(str(value), value)
        self.denominator_combo.setCurrentIndex(max(0, self.denominator_combo.findData(int(initial_denominator))))
        form_layout.addRow("拍号分母", self.denominator_combo)

        root_layout.addLayout(form_layout)

        detail_label = QLabel("提示：起始拍号事件（beat 0）的位置固定不可移动。")
        detail_label.setWordWrap(True)
        root_layout.addWidget(detail_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

    def selected_event(self) -> TimeSignatureEventDialogResult:
        return TimeSignatureEventDialogResult(
            beat_position=float(self.beat_spin.value()),
            numerator=int(self.numerator_spin.value()),
            denominator=int(self.denominator_combo.currentData()),
        )

    @classmethod
    def get_event(
        cls,
        *,
        initial_beat_position: float,
        initial_numerator: int,
        initial_denominator: int,
        lock_beat_position: bool = False,
        parent=None,
    ) -> TimeSignatureEventDialogResult | None:
        dialog = cls(
            initial_beat_position=initial_beat_position,
            initial_numerator=initial_numerator,
            initial_denominator=initial_denominator,
            lock_beat_position=lock_beat_position,
            parent=parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_event()


__all__ = [
    "TempoEventDialog",
    "TempoEventDialogResult",
    "TimeSignatureEventDialog",
    "TimeSignatureEventDialogResult",
]
