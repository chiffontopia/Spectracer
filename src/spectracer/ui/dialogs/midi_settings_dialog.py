from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from spectracer.midi.gm import gm_program_label, is_drum_channel
from spectracer.midi.synth import iter_midi_output_names


@dataclass(slots=True)
class MidiSettingsDialogResult:
    output_name: str | None
    soundfont_path: str | None
    program: int
    channel: int


class MidiSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        initial_output_name: str | None,
        initial_soundfont_path: str | None,
        initial_program: int,
        initial_channel: int,
        current_status_message: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIDI 设置")
        self.resize(680, 320)

        root_layout = QVBoxLayout(self)

        current_status_label = QLabel(f"当前 MIDI 状态：{current_status_message}")
        current_status_label.setWordWrap(True)
        root_layout.addWidget(current_status_label)

        hint_label = QLabel(
            "优先级：指定输出端口 > 指定 SF2 > 自动（系统 MIDI 输出优先，失败时再尝试 FluidSynth / 自动发现的 SF2）。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        form_layout = QFormLayout()

        self.output_combo = QComboBox(self)
        self.output_combo.addItem("自动（系统 MIDI 输出优先，失败时再尝试 FluidSynth）", None)
        self._populate_output_names(initial_output_name)
        form_layout.addRow("输出端口", self.output_combo)

        self.soundfont_edit = QLineEdit(self)
        self.soundfont_edit.setReadOnly(True)
        self.soundfont_edit.setPlaceholderText("自动（使用搜索路径中的首个可用 SF2）")
        if initial_soundfont_path is not None:
            self.soundfont_edit.setText(initial_soundfont_path)
            self.soundfont_edit.setToolTip(initial_soundfont_path)

        self.soundfont_browse_button = QPushButton("选择...", self)
        self.soundfont_clear_button = QPushButton("清除", self)
        self.soundfont_browse_button.clicked.connect(self._browse_soundfont)
        self.soundfont_clear_button.clicked.connect(self._clear_soundfont)

        soundfont_row_widget = QWidget(self)
        soundfont_row_layout = QHBoxLayout(soundfont_row_widget)
        soundfont_row_layout.setContentsMargins(0, 0, 0, 0)
        soundfont_row_layout.addWidget(self.soundfont_edit, stretch=1)
        soundfont_row_layout.addWidget(self.soundfont_browse_button)
        soundfont_row_layout.addWidget(self.soundfont_clear_button)
        form_layout.addRow("指定 SF2", soundfont_row_widget)

        self.program_combo = QComboBox(self)
        self.program_field_label = QLabel(self)
        form_layout.addRow(self.program_field_label, self.program_combo)

        self.channel_spin = QSpinBox(self)
        self.channel_spin.setRange(1, 16)
        self.channel_spin.setValue(max(1, min(16, int(initial_channel) + 1)))
        self.channel_spin.setSuffix(" 号通道")
        self.channel_spin.valueChanged.connect(self._refresh_channel_specific_ui)
        form_layout.addRow("通道", self.channel_spin)

        root_layout.addLayout(form_layout)

        self.channel_hint_label = QLabel(self)
        self.channel_hint_label.setWordWrap(True)
        root_layout.addWidget(self.channel_hint_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self._set_program_combo_items(
            channel=max(0, min(15, int(initial_channel))),
            selected_program=max(0, min(127, int(initial_program))),
        )
        self._refresh_channel_specific_ui()

    def selected_settings(self) -> MidiSettingsDialogResult:
        return MidiSettingsDialogResult(
            output_name=self.output_combo.currentData(),
            soundfont_path=_normalize_soundfont_path(self.soundfont_edit.text()),
            program=int(self.program_combo.currentData()),
            channel=int(self.channel_spin.value()) - 1,
        )

    def _populate_output_names(self, initial_output_name: str | None) -> None:
        try:
            output_names = iter_midi_output_names()
            output_error: str | None = None
        except Exception as exc:  # noqa: BLE001
            output_names = []
            output_error = str(exc)

        normalized_initial = _normalize_output_name(initial_output_name)
        if normalized_initial is not None and normalized_initial not in output_names:
            self.output_combo.addItem(f"{normalized_initial}（当前不可用）", normalized_initial)

        for name in output_names:
            self.output_combo.addItem(name, name)

        if output_error and not output_names:
            self.output_combo.setToolTip(output_error)

        target_value = normalized_initial
        if target_value is None:
            self.output_combo.setCurrentIndex(0)
            return

        match_index = self.output_combo.findData(target_value)
        if match_index >= 0:
            self.output_combo.setCurrentIndex(match_index)
        else:
            self.output_combo.setCurrentIndex(0)

    def _browse_soundfont(self) -> None:
        current_path = _normalize_soundfont_path(self.soundfont_edit.text())
        start_directory = str(Path(current_path).expanduser().parent) if current_path is not None else ""
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 SoundFont",
            start_directory,
            "SoundFont (*.sf2)",
        )
        normalized_path = _normalize_soundfont_path(selected_path)
        if normalized_path is None:
            return
        self.soundfont_edit.setText(normalized_path)
        self.soundfont_edit.setToolTip(normalized_path)

    def _clear_soundfont(self) -> None:
        self.soundfont_edit.clear()
        self.soundfont_edit.setToolTip("")

    def _refresh_channel_specific_ui(self) -> None:
        selected_program = int(self.program_combo.currentData()) if self.program_combo.currentData() is not None else 0
        channel = max(0, min(15, int(self.channel_spin.value()) - 1))
        self._set_program_combo_items(channel=channel, selected_program=selected_program)

        if is_drum_channel(channel):
            self.program_field_label.setText("鼓组")
            self.channel_hint_label.setText(
                "10 号通道按 GM 规则视为打击乐通道：内部会自动切换到 Drum Bank 128，编号按鼓组 Program 解释。"
            )
        else:
            self.program_field_label.setText("乐器")
            self.channel_hint_label.setText(
                "非 10 号通道按普通 GM 乐器通道处理；若同时设置了输出端口和 SF2，则显式输出端口优先。"
            )

    def _set_program_combo_items(self, *, channel: int, selected_program: int) -> None:
        clamped_program = max(0, min(127, int(selected_program)))
        self.program_combo.blockSignals(True)
        self.program_combo.clear()
        for index in range(128):
            self.program_combo.addItem(gm_program_label(index, channel=channel), index)
        self.program_combo.setCurrentIndex(clamped_program)
        self.program_combo.blockSignals(False)

    @classmethod
    def get_settings(
        cls,
        *,
        initial_output_name: str | None,
        initial_soundfont_path: str | None,
        initial_program: int,
        initial_channel: int,
        current_status_message: str,
        parent=None,
    ) -> MidiSettingsDialogResult | None:
        dialog = cls(
            initial_output_name=initial_output_name,
            initial_soundfont_path=initial_soundfont_path,
            initial_program=initial_program,
            initial_channel=initial_channel,
            current_status_message=current_status_message,
            parent=parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_settings()


def _normalize_output_name(output_name: str | None) -> str | None:
    if output_name is None:
        return None
    normalized = str(output_name).strip()
    return normalized or None


def _normalize_soundfont_path(soundfont_path: str | None) -> str | None:
    if soundfont_path is None:
        return None
    normalized = str(soundfont_path).strip()
    return normalized or None
