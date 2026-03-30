from __future__ import annotations

import time
from statistics import fmean, pstdev

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from spectracer.core.analysis_results import TempoAnalysisCandidate, TempoAnalysisResult
from spectracer.core.models import ChannelMode


def _format_seconds(value: float) -> str:
    return f"{float(value):.3f} s"


def _format_offset_ms(value: float) -> str:
    return f"{float(value):+.1f} ms"


class TempoAnalysisDialog(QDialog):
    smart_analysis_requested = pyqtSignal()
    apply_candidate_requested = pyqtSignal(object)
    jump_to_first_beat_requested = pyqtSignal(float)

    def __init__(
        self,
        *,
        current_bpm: float,
        current_offset_ms: float,
        current_channel_mode: ChannelMode | None,
        duration_seconds: float,
        default_tap_first_beat_seconds: float = 0.0,
        default_interval_start_seconds: float = 0.0,
        default_interval_end_seconds: float = 0.0,
        default_interval_beats: float = 4.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("节拍分析 / BPM 测算")
        self.resize(780, 560)

        self._duration_seconds = max(0.0, float(duration_seconds))
        self._current_bpm = max(1.0, float(current_bpm))
        self._current_offset_ms = float(current_offset_ms)
        self._current_channel_mode = current_channel_mode
        self._tap_timestamps: list[float] = []
        self._smart_result = TempoAnalysisResult()

        root_layout = QVBoxLayout(self)

        mode_label = current_channel_mode.display_name if current_channel_mode is not None else "未指定"
        summary_label = QLabel(
            f"当前网格：{self._current_bpm:.3f} BPM | offset {_format_offset_ms(self._current_offset_ms)} | 声道：{mode_label}"
        )
        summary_label.setWordWrap(True)
        root_layout.addWidget(summary_label)

        hint_label = QLabel(
            "手动页支持 Tap Tempo 与区间换算；智能页会给出只读候选。只有点击“应用到网格”时，才会改写当前根 BPM / offset。"
        )
        hint_label.setWordWrap(True)
        root_layout.addWidget(hint_label)

        self.tabs = QTabWidget(self)
        self.manual_tab = QWidget(self)
        self.smart_tab = QWidget(self)
        self.tabs.addTab(self.manual_tab, "手动测算")
        self.tabs.addTab(self.smart_tab, "智能分析")
        root_layout.addWidget(self.tabs, stretch=1)

        self._build_manual_tab(
            default_tap_first_beat_seconds=default_tap_first_beat_seconds,
            default_interval_start_seconds=default_interval_start_seconds,
            default_interval_end_seconds=default_interval_end_seconds,
            default_interval_beats=default_interval_beats,
        )
        self._build_smart_tab()

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        root_layout.addWidget(button_box)

        self._refresh_tap_display()
        self._refresh_interval_preview()
        self._update_smart_buttons()

    def _build_manual_tab(
        self,
        *,
        default_tap_first_beat_seconds: float,
        default_interval_start_seconds: float,
        default_interval_end_seconds: float,
        default_interval_beats: float,
    ) -> None:
        root_layout = QVBoxLayout(self.manual_tab)

        tap_group = QGroupBox("Tap Tempo", self.manual_tab)
        tap_layout = QVBoxLayout(tap_group)
        tap_form = QFormLayout()
        self.tap_first_beat_spin = self._create_time_spinbox(default_tap_first_beat_seconds)
        tap_form.addRow("首拍时间", self.tap_first_beat_spin)
        tap_layout.addLayout(tap_form)

        tap_button_row = QHBoxLayout()
        self.tap_button = QPushButton("敲击节拍", tap_group)
        self.tap_button.setDefault(True)
        self.tap_reset_button = QPushButton("重置", tap_group)
        self.tap_jump_button = QPushButton("跳到首拍", tap_group)
        self.tap_apply_button = QPushButton("应用 Tap 结果到网格", tap_group)
        tap_button_row.addWidget(self.tap_button)
        tap_button_row.addWidget(self.tap_reset_button)
        tap_button_row.addWidget(self.tap_jump_button)
        tap_button_row.addStretch(1)
        tap_button_row.addWidget(self.tap_apply_button)
        tap_layout.addLayout(tap_button_row)

        self.tap_result_label = QLabel("等待至少 2 次点击…", tap_group)
        self.tap_detail_label = QLabel("Tap 后会根据最近几次间隔计算平均 BPM，并将“首拍时间”作为 offset。", tap_group)
        self.tap_result_label.setWordWrap(True)
        self.tap_detail_label.setWordWrap(True)
        tap_layout.addWidget(self.tap_result_label)
        tap_layout.addWidget(self.tap_detail_label)
        root_layout.addWidget(tap_group)

        interval_group = QGroupBox("区间换算", self.manual_tab)
        interval_layout = QVBoxLayout(interval_group)
        interval_form = QFormLayout()
        self.interval_start_spin = self._create_time_spinbox(default_interval_start_seconds)
        self.interval_end_spin = self._create_time_spinbox(max(default_interval_start_seconds, default_interval_end_seconds))
        self.interval_beat_count_spin = QDoubleSpinBox(interval_group)
        self.interval_beat_count_spin.setRange(0.25, 2048.0)
        self.interval_beat_count_spin.setDecimals(3)
        self.interval_beat_count_spin.setSingleStep(0.5)
        self.interval_beat_count_spin.setSuffix(" 拍")
        self.interval_beat_count_spin.setValue(max(0.25, float(default_interval_beats)))
        interval_form.addRow("开始时间", self.interval_start_spin)
        interval_form.addRow("结束时间", self.interval_end_spin)
        interval_form.addRow("拍数", self.interval_beat_count_spin)
        interval_layout.addLayout(interval_form)

        interval_button_row = QHBoxLayout()
        self.interval_jump_button = QPushButton("跳到开始时间", interval_group)
        self.interval_apply_button = QPushButton("应用区间结果到网格", interval_group)
        interval_button_row.addWidget(self.interval_jump_button)
        interval_button_row.addStretch(1)
        interval_button_row.addWidget(self.interval_apply_button)
        interval_layout.addLayout(interval_button_row)

        self.interval_result_label = QLabel("等待有效的开始/结束时间…", interval_group)
        self.interval_detail_label = QLabel("默认将“开始时间”视为首拍时间，并据此计算 BPM 与 offset。", interval_group)
        self.interval_result_label.setWordWrap(True)
        self.interval_detail_label.setWordWrap(True)
        interval_layout.addWidget(self.interval_result_label)
        interval_layout.addWidget(self.interval_detail_label)
        root_layout.addWidget(interval_group)
        root_layout.addStretch(1)

        self.tap_button.clicked.connect(self.record_tap)
        self.tap_reset_button.clicked.connect(self.reset_taps)
        self.tap_jump_button.clicked.connect(lambda: self.jump_to_first_beat_requested.emit(float(self.tap_first_beat_spin.value())))
        self.tap_apply_button.clicked.connect(self._emit_tap_candidate)
        self.interval_jump_button.clicked.connect(
            lambda: self.jump_to_first_beat_requested.emit(float(self.interval_start_spin.value()))
        )
        self.interval_apply_button.clicked.connect(self._emit_interval_candidate)
        self.interval_start_spin.valueChanged.connect(self._refresh_interval_preview)
        self.interval_end_spin.valueChanged.connect(self._refresh_interval_preview)
        self.interval_beat_count_spin.valueChanged.connect(self._refresh_interval_preview)

    def _build_smart_tab(self) -> None:
        root_layout = QVBoxLayout(self.smart_tab)

        header_row = QHBoxLayout()
        self.smart_analyze_button = QPushButton("运行智能分析", self.smart_tab)
        self.smart_status_label = QLabel("尚未运行智能分析。", self.smart_tab)
        self.smart_status_label.setWordWrap(True)
        header_row.addWidget(self.smart_analyze_button)
        header_row.addWidget(self.smart_status_label, stretch=1)
        root_layout.addLayout(header_row)

        self.smart_candidates_table = QTableWidget(0, 6, self.smart_tab)
        self.smart_candidates_table.setHorizontalHeaderLabels(["#", "标签", "BPM", "首拍", "Offset", "置信度"])
        self.smart_candidates_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.smart_candidates_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.smart_candidates_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.smart_candidates_table.setAlternatingRowColors(True)
        self.smart_candidates_table.verticalHeader().setVisible(False)
        header = self.smart_candidates_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        root_layout.addWidget(self.smart_candidates_table, stretch=1)

        action_row = QHBoxLayout()
        self.smart_jump_button = QPushButton("跳到首拍", self.smart_tab)
        self.smart_apply_button = QPushButton("应用候选到网格", self.smart_tab)
        action_row.addStretch(1)
        action_row.addWidget(self.smart_jump_button)
        action_row.addWidget(self.smart_apply_button)
        root_layout.addLayout(action_row)

        self.smart_analyze_button.clicked.connect(lambda _checked=False: self.smart_analysis_requested.emit())
        self.smart_candidates_table.itemSelectionChanged.connect(self._update_smart_buttons)
        self.smart_candidates_table.cellDoubleClicked.connect(lambda _row, _column: self._emit_selected_smart_candidate())
        self.smart_jump_button.clicked.connect(self._emit_selected_smart_jump)
        self.smart_apply_button.clicked.connect(self._emit_selected_smart_candidate)

    def _create_time_spinbox(self, value: float) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox(self)
        spinbox.setRange(0.0, max(36000.0, self._duration_seconds if self._duration_seconds > 0.0 else 36000.0))
        spinbox.setDecimals(3)
        spinbox.setSingleStep(0.05)
        spinbox.setSuffix(" s")
        spinbox.setValue(max(0.0, float(value)))
        return spinbox

    def record_tap(self, checked: bool = False, *, timestamp_seconds: float | None = None) -> None:
        _ = checked
        now = time.monotonic() if timestamp_seconds is None else float(timestamp_seconds)
        if self._tap_timestamps and now - self._tap_timestamps[-1] > 3.0:
            self._tap_timestamps.clear()
        if self._tap_timestamps and now <= self._tap_timestamps[-1]:
            now = self._tap_timestamps[-1] + 1e-6
        self._tap_timestamps.append(now)
        self._tap_timestamps = self._tap_timestamps[-8:]
        self._refresh_tap_display()

    def reset_taps(self) -> None:
        self._tap_timestamps.clear()
        self._refresh_tap_display()

    def tap_candidate(self) -> TempoAnalysisCandidate | None:
        intervals = self._tap_intervals()
        if not intervals:
            return None
        mean_interval = fmean(intervals)
        if mean_interval <= 0.0:
            return None
        bpm = 60.0 / mean_interval
        stability = 1.0
        if len(intervals) >= 2:
            stability = max(0.0, min(1.0, 1.0 - (pstdev(intervals) / mean_interval)))
        confidence = max(0.25, min(1.0, 0.45 + (0.08 * len(intervals)) + (0.25 * stability)))
        first_beat_seconds = float(self.tap_first_beat_spin.value())
        return TempoAnalysisCandidate(
            bpm=bpm,
            first_beat_seconds=first_beat_seconds,
            offset_ms=first_beat_seconds * 1000.0,
            confidence=confidence,
            candidate_rank=1,
            label="Tap Tempo",
        )

    def interval_candidate(self) -> TempoAnalysisCandidate | None:
        start_seconds = float(self.interval_start_spin.value())
        end_seconds = float(self.interval_end_spin.value())
        beat_count = float(self.interval_beat_count_spin.value())
        duration = end_seconds - start_seconds
        if duration <= 0.0 or beat_count <= 0.0:
            return None
        bpm = 60.0 * beat_count / duration
        return TempoAnalysisCandidate(
            bpm=bpm,
            first_beat_seconds=start_seconds,
            offset_ms=start_seconds * 1000.0,
            confidence=1.0,
            candidate_rank=1,
            label="区间换算",
        )

    def set_smart_analysis_busy(self, busy: bool, *, message: str | None = None) -> None:
        self.smart_analyze_button.setEnabled(not busy)
        if busy:
            self.smart_analyze_button.setText("分析中…")
        elif self.smart_candidates_table.rowCount() > 0:
            self.smart_analyze_button.setText("刷新智能分析")
        else:
            self.smart_analyze_button.setText("运行智能分析")
        if message is not None:
            self.smart_status_label.setText(str(message))
        self._update_smart_buttons()

    def set_smart_analysis_result(self, result: TempoAnalysisResult, *, from_cache: bool) -> None:
        ordered_candidates = sorted(result.candidates, key=lambda candidate: candidate.candidate_rank)
        self._smart_result = TempoAnalysisResult(
            candidates=tuple(ordered_candidates),
            channel_mode=result.channel_mode,
            selected_candidate_rank=result.selected_candidate_rank,
            beat_anchors=result.beat_anchors,
            tempo_segments=result.tempo_segments,
            analysis_basis=result.analysis_basis,
            schema_version=result.schema_version,
            notes=result.notes,
        )
        self.smart_candidates_table.setRowCount(len(ordered_candidates))
        for row, candidate in enumerate(ordered_candidates):
            cells = (
                str(candidate.candidate_rank),
                candidate.label or "",
                f"{candidate.bpm:.3f}",
                _format_seconds(candidate.first_beat_seconds),
                _format_offset_ms(candidate.offset_ms),
                f"{candidate.confidence:.0%}",
            )
            for column, cell_text in enumerate(cells):
                item = QTableWidgetItem(cell_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.smart_candidates_table.setItem(row, column, item)

        if ordered_candidates:
            selected_rank = result.selected_candidate_rank or ordered_candidates[0].candidate_rank
            selected_row = 0
            for row, candidate in enumerate(ordered_candidates):
                if candidate.candidate_rank == selected_rank:
                    selected_row = row
                    break
            self.smart_candidates_table.selectRow(selected_row)
            primary = ordered_candidates[selected_row]
            source_label = "已加载缓存结果" if from_cache else "智能分析完成"
            self.smart_status_label.setText(
                f"{source_label}：共 {len(ordered_candidates)} 个候选，当前选中 {primary.bpm:.3f} BPM。"
            )
        else:
            self.smart_status_label.setText("智能分析未返回候选。")
        self.set_smart_analysis_busy(False)

    def selected_smart_candidate(self) -> TempoAnalysisCandidate | None:
        current_row = self.smart_candidates_table.currentRow()
        if current_row < 0:
            return None
        ordered_candidates = sorted(self._smart_result.candidates, key=lambda candidate: candidate.candidate_rank)
        if current_row >= len(ordered_candidates):
            return None
        return ordered_candidates[current_row]

    def _tap_intervals(self) -> list[float]:
        return [
            current - previous
            for previous, current in zip(self._tap_timestamps[:-1], self._tap_timestamps[1:])
            if current > previous
        ]

    def _refresh_tap_display(self) -> None:
        candidate = self.tap_candidate()
        tap_count = len(self._tap_timestamps)
        intervals = self._tap_intervals()
        if candidate is None:
            self.tap_result_label.setText(f"已记录 {tap_count} 次点击，至少再点击 1 次才能得到 BPM。")
            self.tap_detail_label.setText("Tap 后会根据最近几次间隔计算平均 BPM，并将“首拍时间”作为 offset。")
            self.tap_apply_button.setEnabled(False)
            return
        mean_interval = fmean(intervals)
        spread_text = "稳定度：100%"
        if len(intervals) >= 2:
            stability = max(0.0, min(1.0, 1.0 - (pstdev(intervals) / mean_interval)))
            spread_text = f"稳定度：{stability:.0%}"
        self.tap_result_label.setText(
            f"Tap 结果：{candidate.bpm:.3f} BPM | 首拍 {_format_seconds(candidate.first_beat_seconds)} | offset {_format_offset_ms(candidate.offset_ms)}"
        )
        self.tap_detail_label.setText(f"共 {tap_count} 次点击，使用最近 {len(intervals)} 个间隔平均。{spread_text}")
        self.tap_apply_button.setEnabled(True)

    def _refresh_interval_preview(self) -> None:
        candidate = self.interval_candidate()
        if candidate is None:
            self.interval_result_label.setText("请输入有效的开始/结束时间，且结束时间必须大于开始时间。")
            self.interval_detail_label.setText("默认将“开始时间”视为首拍时间，并据此计算 BPM 与 offset。")
            self.interval_apply_button.setEnabled(False)
            return
        beat_count = float(self.interval_beat_count_spin.value())
        span = float(self.interval_end_spin.value() - self.interval_start_spin.value())
        self.interval_result_label.setText(
            f"区间结果：{candidate.bpm:.3f} BPM | 首拍 {_format_seconds(candidate.first_beat_seconds)} | offset {_format_offset_ms(candidate.offset_ms)}"
        )
        self.interval_detail_label.setText(f"时长 {span:.3f} s / {beat_count:.3f} 拍。")
        self.interval_apply_button.setEnabled(True)

    def _emit_tap_candidate(self) -> None:
        candidate = self.tap_candidate()
        if candidate is None:
            return
        self.apply_candidate_requested.emit(candidate)

    def _emit_interval_candidate(self) -> None:
        candidate = self.interval_candidate()
        if candidate is None:
            return
        self.apply_candidate_requested.emit(candidate)

    def _emit_selected_smart_jump(self) -> None:
        candidate = self.selected_smart_candidate()
        if candidate is None:
            return
        self.jump_to_first_beat_requested.emit(float(candidate.first_beat_seconds))

    def _emit_selected_smart_candidate(self) -> None:
        candidate = self.selected_smart_candidate()
        if candidate is None:
            return
        self.apply_candidate_requested.emit(candidate)

    def _update_smart_buttons(self) -> None:
        has_selection = self.selected_smart_candidate() is not None
        smart_busy = not self.smart_analyze_button.isEnabled()
        self.smart_jump_button.setEnabled(has_selection and not smart_busy)
        self.smart_apply_button.setEnabled(has_selection and not smart_busy)


__all__ = ["TempoAnalysisDialog"]
