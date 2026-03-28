from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from spectracer.core.config import AnalyzeCliConfig
from spectracer.core.models import AnalysisParams, ChannelMode


RECOMMENDED_SAMPLE_RATE = 48000

COMMON_SAMPLE_RATES: tuple[int | None, ...] = (
    None,
    22050,
    32000,
    44100,
    48000,
    88200,
    96000,
    192000,
)


@dataclass(slots=True)
class AnalysisDialogResult:
    config: AnalyzeCliConfig
    channel_modes: list[ChannelMode]


class AnalysisOptionsDialog(QDialog):
    """分析前参数确认窗口。"""

    def __init__(
        self,
        *,
        audio_path: Path,
        initial_config: AnalyzeCliConfig,
        initial_channel_modes: list[ChannelMode] | None = None,
        config_path: Path | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("分析选项")
        self.resize(480, 560)

        self._audio_path = audio_path
        self._initial_config = initial_config
        self._resolved_config = initial_config
        self._resolved_channel_modes = ChannelMode.ordered_modes()

        initial_modes = set(initial_channel_modes or ChannelMode.ordered_modes())
        self._mode_checkboxes: dict[ChannelMode, QCheckBox] = {}

        root_layout = QVBoxLayout(self)

        source_label = QLabel(f"文件：{audio_path.name}")
        source_label.setWordWrap(True)
        root_layout.addWidget(source_label)

        config_label_text = "默认配置来源：<内建默认>" if config_path is None else f"默认配置来源：{config_path}"
        config_label = QLabel(config_label_text)
        config_label.setWordWrap(True)
        root_layout.addWidget(config_label)

        hint_label = QLabel(
            "确认后将解析勾选的声道模式；默认显示声道会优先用于界面初始显示（其余模式可在后台继续解析）。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        channel_modes_group = QGroupBox("要解析的声道模式")
        channel_modes_layout = QVBoxLayout(channel_modes_group)
        for mode in ChannelMode.ordered_modes():
            checkbox = QCheckBox(mode.display_name, self)
            checkbox.setChecked(mode in initial_modes)
            channel_modes_layout.addWidget(checkbox)
            self._mode_checkboxes[mode] = checkbox
        root_layout.addWidget(channel_modes_group)

        analysis_group = QGroupBox("分析参数")
        analysis_form = QFormLayout(analysis_group)

        self.channel_mode_combo = QComboBox(self)
        for mode in ChannelMode.ordered_modes():
            self.channel_mode_combo.addItem(mode.display_name, mode.value)
        self.channel_mode_combo.setCurrentIndex(
            max(0, self.channel_mode_combo.findData(initial_config.channel_mode.value))
        )
        analysis_form.addRow("默认显示声道", self.channel_mode_combo)

        self.fps_spin = QSpinBox(self)
        self.fps_spin.setRange(1, 100)
        self.fps_spin.setValue(initial_config.fps)
        analysis_form.addRow("FPS", self.fps_spin)

        self.bins_spin = QDoubleSpinBox(self)
        self.bins_spin.setRange(0.1, 12.0)
        self.bins_spin.setDecimals(2)
        self.bins_spin.setSingleStep(0.25)
        self.bins_spin.setValue(initial_config.bins_per_semitone)
        analysis_form.addRow("每半音分块", self.bins_spin)

        self.octave_min_spin = QSpinBox(self)
        self.octave_min_spin.setRange(0, 10)
        self.octave_min_spin.setValue(initial_config.octave_min)
        analysis_form.addRow("最低八度", self.octave_min_spin)

        self.octave_max_spin = QSpinBox(self)
        self.octave_max_spin.setRange(0, 10)
        self.octave_max_spin.setValue(initial_config.octave_max)
        analysis_form.addRow("最高八度", self.octave_max_spin)

        self.a4_spin = QDoubleSpinBox(self)
        self.a4_spin.setRange(400.0, 480.0)
        self.a4_spin.setDecimals(2)
        self.a4_spin.setSingleStep(1.0)
        self.a4_spin.setValue(initial_config.a4_hz)
        analysis_form.addRow("A4 频率 (Hz)", self.a4_spin)

        self.sample_rate_combo = QComboBox(self)
        for sample_rate in COMMON_SAMPLE_RATES:
            label = self._sample_rate_label(sample_rate)
            self.sample_rate_combo.addItem(label, sample_rate)
        initial_sample_rate = initial_config.sample_rate
        if initial_sample_rate not in COMMON_SAMPLE_RATES:
            self.sample_rate_combo.addItem(self._sample_rate_label(initial_sample_rate), int(initial_sample_rate))
        self.sample_rate_combo.setCurrentIndex(
            max(0, self.sample_rate_combo.findData(initial_sample_rate))
        )
        self.sample_rate_combo.setToolTip("可直接选择常见采样率；当前 CQT 路径下 48kHz 通常有更好的解析性能。")
        analysis_form.addRow("重采样率", self.sample_rate_combo)

        root_layout.addWidget(analysis_group)

        render_group = QGroupBox("显示默认值")
        render_form = QFormLayout(render_group)

        self.sensitivity_spin = QDoubleSpinBox(self)
        self.sensitivity_spin.setRange(0.1, 4.0)
        self.sensitivity_spin.setDecimals(2)
        self.sensitivity_spin.setSingleStep(0.05)
        self.sensitivity_spin.setValue(initial_config.sensitivity)
        render_form.addRow("灵敏度", self.sensitivity_spin)

        self.contrast_spin = QDoubleSpinBox(self)
        self.contrast_spin.setRange(0.1, 4.0)
        self.contrast_spin.setDecimals(2)
        self.contrast_spin.setSingleStep(0.05)
        self.contrast_spin.setValue(initial_config.contrast)
        render_form.addRow("对比度", self.contrast_spin)

        self.processing_fingerprint_edit = QLineEdit(self)
        self.processing_fingerprint_edit.setText(initial_config.processing_fingerprint)
        render_form.addRow("处理指纹", self.processing_fingerprint_edit)

        root_layout.addWidget(render_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

    def selected_config(self) -> AnalyzeCliConfig:
        return self._resolved_config

    def selected_channel_modes(self) -> list[ChannelMode]:
        selected: list[ChannelMode] = []
        for mode in ChannelMode.ordered_modes():
            checkbox = self._mode_checkboxes.get(mode)
            if checkbox is None:
                continue
            if checkbox.isChecked():
                selected.append(mode)
        return selected

    def selected_options(self) -> AnalysisDialogResult:
        return AnalysisDialogResult(config=self._resolved_config, channel_modes=list(self._resolved_channel_modes))

    def build_config(self) -> AnalyzeCliConfig:
        config = AnalyzeCliConfig(
            channel_mode=ChannelMode.parse(str(self.channel_mode_combo.currentData())),
            fps=int(self.fps_spin.value()),
            bins_per_semitone=float(self.bins_spin.value()),
            octave_min=int(self.octave_min_spin.value()),
            octave_max=int(self.octave_max_spin.value()),
            a4_hz=float(self.a4_spin.value()),
            sample_rate=self._selected_sample_rate(),
            sensitivity=float(self.sensitivity_spin.value()),
            contrast=float(self.contrast_spin.value()),
            preview_enabled=self._initial_config.preview_enabled,
            processing_fingerprint=self.processing_fingerprint_edit.text().strip() or "raw",
        )

        params = AnalysisParams(
            fps=config.fps,
            bins_per_semitone=config.bins_per_semitone,
            octave_min=config.octave_min,
            octave_max=config.octave_max,
            a4_hz=config.a4_hz,
            sample_rate=config.sample_rate,
            channel_mode=config.channel_mode,
        )
        params.validate()
        return config

    def _sample_rate_label(self, sample_rate: int | None) -> str:
        if sample_rate is None:
            return "原采样率"
        if int(sample_rate) == RECOMMENDED_SAMPLE_RATE:
            return f"{int(sample_rate)} Hz（推荐）"
        return f"{int(sample_rate)} Hz"

    def _selected_sample_rate(self) -> int | None:
        value = self.sample_rate_combo.currentData()
        if value is None:
            return None
        return int(value)

    def accept(self) -> None:
        try:
            self._resolved_config = self.build_config()
            self._resolved_channel_modes = self.selected_channel_modes()
            if not self._resolved_channel_modes:
                raise ValueError("至少需要选择 1 个声道模式")
            if self._resolved_config.channel_mode not in self._resolved_channel_modes:
                raise ValueError("默认显示声道必须包含在要解析的声道模式中")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "分析选项", f"配置无效：{exc}")
            return
        super().accept()

    @classmethod
    def get_options(
        cls,
        *,
        audio_path: Path,
        initial_config: AnalyzeCliConfig,
        initial_channel_modes: list[ChannelMode] | None = None,
        config_path: Path | None,
        parent=None,
    ) -> AnalysisDialogResult | None:
        dialog = cls(
            audio_path=audio_path,
            initial_config=initial_config,
            initial_channel_modes=initial_channel_modes,
            config_path=config_path,
            parent=parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.selected_options()
