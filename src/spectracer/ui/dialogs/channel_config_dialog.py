from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from spectracer.midi.editor_model import MidiChannelConfig
from spectracer.midi.gm import gm_program_label


@dataclass(slots=True)
class ChannelConfigDialogResult:
    channel: int
    name: str
    program: int
    bank: int
    pan: int
    color: str
    muted: bool
    solo: bool

    def to_channel_config(self) -> MidiChannelConfig:
        return MidiChannelConfig(
            channel=self.channel,
            name=self.name,
            program=self.program,
            bank=self.bank,
            pan=self.pan,
            color=self.color,
            muted=self.muted,
            solo=self.solo,
        )


class ChannelConfigDialog(QDialog):
    def __init__(self, *, initial_config: MidiChannelConfig, parent=None) -> None:
        super().__init__(parent)
        if not isinstance(initial_config, MidiChannelConfig):
            raise TypeError("initial_config 必须是 MidiChannelConfig")

        self._initial_config = initial_config
        self._resolved_config = initial_config

        self.setWindowTitle("通道配置")
        self.resize(480, 320)

        root_layout = QVBoxLayout(self)

        self.header_label = QLabel(self._build_header_text(initial_config), self)
        self.header_label.setWordWrap(True)
        root_layout.addWidget(self.header_label)

        hint_label = QLabel(
            "这里用于编辑当前通道的名称、音色、Bank、声像、颜色与静音/独奏状态。修改后会同步影响 MIDI 覆盖层颜色与后续回放。",
            self,
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        form_layout = QFormLayout()

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(initial_config.display_name)
        self.name_edit.setText(initial_config.name)
        form_layout.addRow("名称", self.name_edit)

        self.program_combo = QComboBox(self)
        self.program_combo.setMinimumContentsLength(24)
        self.program_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for program in range(128):
            self.program_combo.addItem(gm_program_label(program, channel=initial_config.channel), program)
        self.program_combo.setCurrentIndex(max(0, self.program_combo.findData(initial_config.program)))
        form_layout.addRow("Program", self.program_combo)

        self.bank_spin = QSpinBox(self)
        self.bank_spin.setRange(0, 16_383)
        self.bank_spin.setValue(int(initial_config.bank))
        self.bank_spin.setEnabled(not initial_config.is_drum)
        if initial_config.is_drum:
            self.bank_spin.setToolTip("GM 鼓组通道固定使用鼓组 Bank。")
        form_layout.addRow("Bank", self.bank_spin)

        self.pan_spin = QSpinBox(self)
        self.pan_spin.setRange(0, 127)
        self.pan_spin.setValue(int(initial_config.pan))
        self.pan_spin.setSuffix(" / 127")
        form_layout.addRow("声像", self.pan_spin)

        color_row = QHBoxLayout()
        self.color_edit = QLineEdit(self)
        self.color_edit.setPlaceholderText("留空时恢复默认通道颜色")
        self.color_edit.setText(initial_config.color)
        color_row.addWidget(self.color_edit, stretch=1)

        self.pick_color_button = QPushButton("选择颜色", self)
        color_row.addWidget(self.pick_color_button)

        self.color_preview = QLabel(self)
        self.color_preview.setFixedSize(40, 18)
        color_row.addWidget(self.color_preview)
        form_layout.addRow("颜色", color_row)

        flags_row = QHBoxLayout()
        self.muted_checkbox = QCheckBox("静音", self)
        self.muted_checkbox.setChecked(initial_config.muted)
        flags_row.addWidget(self.muted_checkbox)
        self.solo_checkbox = QCheckBox("独奏", self)
        self.solo_checkbox.setChecked(initial_config.solo)
        flags_row.addWidget(self.solo_checkbox)
        flags_row.addStretch(1)
        form_layout.addRow("状态", flags_row)

        root_layout.addLayout(form_layout)

        detail_label = QLabel(
            "说明：留空名称可恢复默认通道名；留空颜色可恢复默认配色。Drum 通道会自动使用鼓组 Program 标签与固定 Bank。",
            self,
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

        self.color_edit.textChanged.connect(self._refresh_color_preview)
        self.pick_color_button.clicked.connect(self._pick_color)

        self._refresh_color_preview()

    def selected_config(self) -> MidiChannelConfig:
        return self._resolved_config

    def build_config(self) -> MidiChannelConfig:
        color_text = self.color_edit.text().strip()
        if color_text:
            color = QColor(color_text)
            if not color.isValid():
                raise ValueError("颜色必须是合法的颜色名或十六进制值（如 #FF4081）")
            normalized_color = color.name(QColor.NameFormat.HexRgb)
        else:
            normalized_color = ""

        return MidiChannelConfig(
            channel=self._initial_config.channel,
            name=self.name_edit.text().strip(),
            program=int(self.program_combo.currentData()),
            bank=int(self.bank_spin.value()),
            pan=int(self.pan_spin.value()),
            color=normalized_color,
            muted=self.muted_checkbox.isChecked(),
            solo=self.solo_checkbox.isChecked(),
        )

    def accept(self) -> None:
        try:
            self._resolved_config = self.build_config()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "通道配置", f"配置无效：{exc}")
            return
        super().accept()

    def _pick_color(self) -> None:
        seeded_color = QColor(self.color_edit.text().strip() or self._initial_config.color)
        selected = QColorDialog.getColor(seeded_color, self, "选择通道颜色")
        if not selected.isValid():
            return
        self.color_edit.setText(selected.name(QColor.NameFormat.HexRgb))

    def _refresh_color_preview(self) -> None:
        raw_text = self.color_edit.text().strip()
        if not raw_text:
            resolved = self._initial_config.with_updates(color="").color
            self.color_preview.setToolTip("当前为空：将恢复默认通道颜色")
            self.color_preview.setStyleSheet(
                f"background-color: {resolved}; border: 1px solid rgba(255, 255, 255, 0.5); border-radius: 3px;"
            )
            return

        color = QColor(raw_text)
        if color.isValid():
            normalized = color.name(QColor.NameFormat.HexRgb)
            self.color_preview.setToolTip(normalized)
            self.color_preview.setStyleSheet(
                f"background-color: {normalized}; border: 1px solid rgba(255, 255, 255, 0.5); border-radius: 3px;"
            )
            return

        self.color_preview.setToolTip("颜色值无效")
        self.color_preview.setStyleSheet("background-color: transparent; border: 1px solid #E53935; border-radius: 3px;")

    @staticmethod
    def _build_header_text(config: MidiChannelConfig) -> str:
        channel_text = f"通道 {config.channel + 1:02d}"
        if config.is_drum:
            return f"{channel_text} · 鼓组通道"
        return f"{channel_text} · {config.display_name}"

    @classmethod
    def get_config(cls, *, initial_config: MidiChannelConfig, parent=None) -> MidiChannelConfig | None:
        dialog = cls(initial_config=initial_config, parent=parent)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_config()


__all__ = ["ChannelConfigDialog", "ChannelConfigDialogResult"]
