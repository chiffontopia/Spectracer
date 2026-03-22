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


@dataclass(slots=True)
class GridSettingsDialogResult:
    bpm: float
    numerator: int
    denominator: int
    offset_ms: float
    subdivisions_per_beat: int


class GridSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        initial_bpm: float,
        initial_numerator: int,
        initial_denominator: int,
        initial_offset_ms: float,
        initial_subdivisions_per_beat: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("网格设置")
        self.resize(420, 260)

        root_layout = QVBoxLayout(self)

        hint_label = QLabel(
            "这里用于编辑起始 BPM / 拍号、偏移量与子网格；更细的 Tempo / Meter 变化可在频谱上方的事件轨道中交互编辑。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        form_layout = QFormLayout()

        self.bpm_spin = QDoubleSpinBox(self)
        self.bpm_spin.setRange(1.0, 999.0)
        self.bpm_spin.setDecimals(3)
        self.bpm_spin.setSingleStep(0.5)
        self.bpm_spin.setSuffix(" BPM")
        self.bpm_spin.setValue(max(1.0, float(initial_bpm)))
        form_layout.addRow("BPM", self.bpm_spin)

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

        self.offset_spin = QDoubleSpinBox(self)
        self.offset_spin.setRange(-600000.0, 600000.0)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(10.0)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.setValue(float(initial_offset_ms))
        form_layout.addRow("偏移量", self.offset_spin)

        self.subdivision_combo = QComboBox(self)
        subdivision_values = [1, 2, 3, 4, 6, 8, 12, 16]
        if int(initial_subdivisions_per_beat) not in subdivision_values:
            subdivision_values.append(int(initial_subdivisions_per_beat))
            subdivision_values.sort()
        for value in subdivision_values:
            self.subdivision_combo.addItem(f"每拍 {value} 等分", value)
        self.subdivision_combo.setCurrentIndex(
            max(0, self.subdivision_combo.findData(int(initial_subdivisions_per_beat)))
        )
        form_layout.addRow("子网格", self.subdivision_combo)

        root_layout.addLayout(form_layout)

        detail_label = QLabel(
            "说明：内部时间坐标使用四分音符拍（quarter-note beat）；拍号分母只影响小节与主拍显示，不改变 BPM 的定义。"
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

    def selected_settings(self) -> GridSettingsDialogResult:
        return GridSettingsDialogResult(
            bpm=float(self.bpm_spin.value()),
            numerator=int(self.numerator_spin.value()),
            denominator=int(self.denominator_combo.currentData()),
            offset_ms=float(self.offset_spin.value()),
            subdivisions_per_beat=int(self.subdivision_combo.currentData()),
        )

    @classmethod
    def get_settings(
        cls,
        *,
        initial_bpm: float,
        initial_numerator: int,
        initial_denominator: int,
        initial_offset_ms: float,
        initial_subdivisions_per_beat: int,
        parent=None,
    ) -> GridSettingsDialogResult | None:
        dialog = cls(
            initial_bpm=initial_bpm,
            initial_numerator=initial_numerator,
            initial_denominator=initial_denominator,
            initial_offset_ms=initial_offset_ms,
            initial_subdivisions_per_beat=initial_subdivisions_per_beat,
            parent=parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_settings()
