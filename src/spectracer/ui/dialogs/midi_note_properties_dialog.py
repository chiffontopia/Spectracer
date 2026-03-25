from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from spectracer.midi.editor_model import MidiNote


@dataclass(slots=True)
class MidiNotePropertiesDialogResult:
    velocity: int
    pan: int | None


class MidiNotePropertiesDialog(QDialog):
    def __init__(self, *, selected_notes: Sequence[MidiNote], parent=None) -> None:
        super().__init__(parent)
        normalized_notes = tuple(selected_notes)
        if not normalized_notes:
            raise ValueError("selected_notes 不可为空")
        if any(not isinstance(note, MidiNote) for note in normalized_notes):
            raise TypeError("selected_notes 必须全部是 MidiNote")

        self._selected_notes = normalized_notes
        self._resolved_properties = self._build_seed_result(normalized_notes)

        self.setWindowTitle("Note 属性")
        self.resize(420, 240)

        root_layout = QVBoxLayout(self)

        info_lines = [f"将统一编辑 {len(normalized_notes)} 个 note 的属性。"]
        if len({note.velocity for note in normalized_notes}) > 1:
            info_lines.append("当前 Velocity 不一致，已按平均值填入初始值。")
        if len({note.pan for note in normalized_notes}) > 1:
            info_lines.append("当前 note.pan 不一致，修改后会统一覆盖所选 note。")
        info_label = QLabel("\n".join(info_lines), self)
        info_label.setWordWrap(True)
        root_layout.addWidget(info_label)

        form_layout = QFormLayout()

        self.velocity_spin = QSpinBox(self)
        self.velocity_spin.setRange(0, 127)
        self.velocity_spin.setValue(self._resolved_properties.velocity)
        form_layout.addRow("力度", self.velocity_spin)

        self.pan_override_checkbox = QCheckBox("覆写 note.pan", self)
        self.pan_override_checkbox.setChecked(self._resolved_properties.pan is not None)
        form_layout.addRow("声像覆写", self.pan_override_checkbox)

        self.pan_spin = QSpinBox(self)
        self.pan_spin.setRange(0, 127)
        self.pan_spin.setValue(64 if self._resolved_properties.pan is None else int(self._resolved_properties.pan))
        self.pan_spin.setEnabled(self.pan_override_checkbox.isChecked())
        self.pan_spin.setSuffix(" / 127")
        form_layout.addRow("Pan", self.pan_spin)

        root_layout.addLayout(form_layout)

        hint_label = QLabel(
            "取消勾选“覆写 note.pan”可清除所选 note 的单独声像值，使其继续继承通道声像。",
            self,
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self.pan_override_checkbox.toggled.connect(self.pan_spin.setEnabled)

    def selected_properties(self) -> MidiNotePropertiesDialogResult:
        return self._resolved_properties

    def build_result(self) -> MidiNotePropertiesDialogResult:
        return MidiNotePropertiesDialogResult(
            velocity=int(self.velocity_spin.value()),
            pan=int(self.pan_spin.value()) if self.pan_override_checkbox.isChecked() else None,
        )

    def accept(self) -> None:
        try:
            self._resolved_properties = self.build_result()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Note 属性", f"配置无效：{exc}")
            return
        super().accept()

    @staticmethod
    def _build_seed_result(selected_notes: Sequence[MidiNote]) -> MidiNotePropertiesDialogResult:
        velocity = int(round(sum(note.velocity for note in selected_notes) / len(selected_notes)))
        pan_values = [int(note.pan) for note in selected_notes if note.pan is not None]
        pan = int(round(sum(pan_values) / len(pan_values))) if pan_values else None
        return MidiNotePropertiesDialogResult(velocity=velocity, pan=pan)

    @classmethod
    def get_properties(cls, *, selected_notes: Sequence[MidiNote], parent=None) -> MidiNotePropertiesDialogResult | None:
        dialog = cls(selected_notes=selected_notes, parent=parent)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_properties()


__all__ = ["MidiNotePropertiesDialog", "MidiNotePropertiesDialogResult"]
