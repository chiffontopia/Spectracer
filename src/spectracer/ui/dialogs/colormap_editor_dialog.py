from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import count
from pathlib import Path
import re
from typing import Sequence

import numpy as np
from matplotlib import colormaps
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from spectracer.dsp.colormap import ColorStop, default_spectracer_colormap_stops, make_linear_colormap, normalize_colormap_stops


@dataclass(slots=True)
class _StopItem:
    id: int
    pos: float
    color: str


class ColormapEditorDialog(QDialog):
    """热图色盘编辑器。

    - 支持编辑颜色节点（位置 + 颜色）
    - 至少包含 2 个节点，并强制包含 0/1 两端点
    - 支持从 Matplotlib 预设色盘快速生成节点
    """

    _id_counter = count(1)
    _preset_dir = Path("colormap_presets")

    def __init__(self, *, initial_stops: Sequence[ColorStop] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("热图色盘")
        self.resize(520, 420)

        self._items: list[_StopItem] = []
        self._current_id: int | None = None

        root_layout = QVBoxLayout(self)

        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("预设："))
        self.preset_combo = QComboBox(self)
        preset_layout.addWidget(self.preset_combo, stretch=1)
        self.preset_apply_button = QPushButton("应用预设", self)
        preset_layout.addWidget(self.preset_apply_button)

        self.import_csv_button = QPushButton("导入CSV", self)
        self.export_csv_button = QPushButton("导出CSV", self)
        preset_layout.addWidget(self.import_csv_button)
        preset_layout.addWidget(self.export_csv_button)
        root_layout.addLayout(preset_layout)

        self.preview_label = QLabel(self)
        self.preview_label.setFixedHeight(28)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self.preview_label)

        body_layout = QHBoxLayout()

        self.stop_list = QListWidget(self)
        self.stop_list.setMinimumWidth(220)
        body_layout.addWidget(self.stop_list, stretch=1)

        editor_layout = QVBoxLayout()

        form = QFormLayout()
        self.position_spin = QDoubleSpinBox(self)
        self.position_spin.setRange(0.0, 1.0)
        self.position_spin.setDecimals(3)
        self.position_spin.setSingleStep(0.01)
        form.addRow("位置 (0~1)", self.position_spin)

        self.color_button = QPushButton("选择颜色", self)
        form.addRow("颜色", self.color_button)

        editor_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.add_stop_button = QPushButton("添加节点", self)
        self.remove_stop_button = QPushButton("删除节点", self)
        self.reset_button = QPushButton("重置默认", self)
        button_row.addWidget(self.add_stop_button)
        button_row.addWidget(self.remove_stop_button)
        button_row.addWidget(self.reset_button)
        editor_layout.addLayout(button_row)

        editor_layout.addStretch(1)
        body_layout.addLayout(editor_layout, stretch=1)

        root_layout.addLayout(body_layout)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self.stop_list.currentItemChanged.connect(self._on_stop_selected)
        self.position_spin.valueChanged.connect(self._on_position_changed)
        self.color_button.clicked.connect(self._on_pick_color)
        self.add_stop_button.clicked.connect(self._on_add_stop)
        self.remove_stop_button.clicked.connect(self._on_remove_stop)
        self.reset_button.clicked.connect(self._on_reset_default)
        self.preset_apply_button.clicked.connect(self._on_apply_preset)
        self.import_csv_button.clicked.connect(self._on_import_csv)
        self.export_csv_button.clicked.connect(self._on_export_csv)

        self._populate_preset_combo()
        self._load_initial(initial_stops)

    def stops(self) -> list[ColorStop]:
        ordered = sorted(self._items, key=lambda item: item.pos)
        raw = [(item.pos, item.color) for item in ordered]
        return normalize_colormap_stops(raw)

    def _populate_preset_combo(self, *, select_data: str | None = None) -> None:
        builtin_presets: list[tuple[str, str]] = [
            ("Spectracer (默认)", "builtin:spectracer"),
            ("Viridis", "builtin:viridis"),
            ("Inferno", "builtin:inferno"),
            ("Magma", "builtin:magma"),
            ("Plasma", "builtin:plasma"),
            ("Turbo", "builtin:turbo"),
            ("Gray", "builtin:gray"),
        ]

        current_data = select_data
        if current_data is None:
            current_value = self.preset_combo.currentData()
            current_data = None if current_value is None else str(current_value)

        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for label, data in builtin_presets:
                self.preset_combo.addItem(label, data)

            for preset_name, preset_path in self._load_persisted_presets():
                self.preset_combo.addItem(f"{preset_name}（自定义）", f"custom:{preset_path.as_posix()}")

            if current_data is not None:
                index = self.preset_combo.findData(current_data)
                if index >= 0:
                    self.preset_combo.setCurrentIndex(index)
        finally:
            self.preset_combo.blockSignals(False)

    def _load_persisted_presets(self) -> list[tuple[str, Path]]:
        self._preset_dir.mkdir(parents=True, exist_ok=True)
        presets: list[tuple[str, Path]] = []
        for path in sorted(self._preset_dir.glob("*.csv")):
            try:
                preset_name, _ = self._read_preset_csv(path)
            except Exception:
                continue
            if not preset_name:
                preset_name = path.stem
            presets.append((preset_name, path.resolve()))
        return presets

    @staticmethod
    def _sanitize_preset_filename(name: str) -> str:
        sanitized = re.sub(r'[<>:"/\\|?*]+', "_", str(name).strip())
        sanitized = sanitized.strip(" .")
        return sanitized or "preset"

    def _prompt_preset_name(self, *, default_name: str) -> str | None:
        candidate = str(default_name).strip() or "preset"
        while True:
            name, accepted = QInputDialog.getText(self, "导入色盘预设", "预设名称：", text=candidate)
            if not accepted:
                return None

            normalized = str(name).strip()
            if normalized:
                return normalized

            QMessageBox.warning(self, "热图色盘", "预设名称不能为空")

    @staticmethod
    def _current_preset_display_name(combo: QComboBox) -> str:
        text = combo.currentText().strip()
        if text.endswith("（自定义）"):
            return text[: -len("（自定义）")].strip()
        return text or "custom"

    def _load_initial(self, initial_stops: Sequence[ColorStop] | None) -> None:
        stops = default_spectracer_colormap_stops() if initial_stops is None else list(initial_stops)
        stops = normalize_colormap_stops(stops)
        self._items = [
            _StopItem(id=next(self._id_counter), pos=float(pos), color=str(color))
            for pos, color in stops
        ]
        self._refresh_list(select_id=self._items[0].id if self._items else None)

    def _refresh_list(self, *, select_id: int | None) -> None:
        self.stop_list.blockSignals(True)
        try:
            self.stop_list.clear()
            ordered = sorted(self._items, key=lambda item: item.pos)
            for item in ordered:
                text = f"{item.pos:0.3f}  {item.color}"
                list_item = QListWidgetItem(text)
                list_item.setData(Qt.ItemDataRole.UserRole, item.id)
                self.stop_list.addItem(list_item)

            if select_id is not None:
                for row in range(self.stop_list.count()):
                    it = self.stop_list.item(row)
                    if it is not None and it.data(Qt.ItemDataRole.UserRole) == select_id:
                        self.stop_list.setCurrentRow(row)
                        break
        finally:
            self.stop_list.blockSignals(False)

        self._update_editor_from_selection()
        self._update_preview()

    def _selected_id(self) -> int | None:
        item = self.stop_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _find_item(self, stop_id: int) -> _StopItem | None:
        for item in self._items:
            if item.id == stop_id:
                return item
        return None

    def _is_endpoint(self, item: _StopItem) -> bool:
        return abs(item.pos - 0.0) < 1e-9 or abs(item.pos - 1.0) < 1e-9

    def _update_editor_from_selection(self) -> None:
        stop_id = self._selected_id()
        self._current_id = stop_id
        item = self._find_item(stop_id) if stop_id is not None else None
        if item is None:
            self.position_spin.setEnabled(False)
            self.color_button.setEnabled(False)
            self.remove_stop_button.setEnabled(False)
            return

        self.position_spin.blockSignals(True)
        try:
            self.position_spin.setValue(float(item.pos))
        finally:
            self.position_spin.blockSignals(False)

        endpoint = self._is_endpoint(item)
        self.position_spin.setEnabled(not endpoint)
        self.remove_stop_button.setEnabled(not endpoint)
        self.color_button.setEnabled(True)
        self._apply_color_button_style(item.color)

    def _apply_color_button_style(self, hex_color: str) -> None:
        self.color_button.setStyleSheet(f"background-color: {hex_color}; color: white; font-weight: bold;")

    def _update_preview(self) -> None:
        try:
            stops = [(item.pos, item.color) for item in sorted(self._items, key=lambda it: it.pos)]
            stops = normalize_colormap_stops(stops)
            cmap = make_linear_colormap(name="custom", stops=stops)
            lut = (cmap(np.linspace(0.0, 1.0, 256)) * 255).astype(np.uint8)
        except Exception as exc:  # noqa: BLE001
            self.preview_label.setText(f"预览生成失败：{exc}")
            return

        height = 24
        img = QImage(256, height, QImage.Format.Format_RGBA8888)
        for x in range(256):
            r, g, b, a = (int(v) for v in lut[x])
            color = QColor(r, g, b, a)
            for y in range(height):
                img.setPixelColor(x, y, color)
        self.preview_label.setPixmap(QPixmap.fromImage(img))

    def _on_stop_selected(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._update_editor_from_selection()

    def _on_position_changed(self, value: float) -> None:
        if self._current_id is None:
            return
        item = self._find_item(self._current_id)
        if item is None or self._is_endpoint(item):
            return

        value = max(0.0, min(1.0, float(value)))

        # 约束：保持严格递增，避免节点互相穿越。
        ordered = sorted(self._items, key=lambda it: it.pos)
        index = next((i for i, it in enumerate(ordered) if it.id == item.id), None)
        if index is None:
            return

        lower = 0.0
        upper = 1.0
        if index > 0:
            lower = ordered[index - 1].pos + 1e-3
        if index < len(ordered) - 1:
            upper = ordered[index + 1].pos - 1e-3

        item.pos = max(lower, min(upper, value))
        self._refresh_list(select_id=item.id)

    def _on_pick_color(self) -> None:
        if self._current_id is None:
            return
        item = self._find_item(self._current_id)
        if item is None:
            return

        current = QColor(item.color)
        picked = QColorDialog.getColor(current, self, "选择颜色")
        if not picked.isValid():
            return

        item.color = picked.name(QColor.NameFormat.HexRgb)
        self._refresh_list(select_id=item.id)

    def _on_add_stop(self) -> None:
        ordered = sorted(self._items, key=lambda it: it.pos)
        if not ordered:
            self._load_initial(None)
            return

        selected_id = self._current_id
        selected = self._find_item(selected_id) if selected_id is not None else None
        if selected is None:
            selected = ordered[0]

        ordered = sorted(self._items, key=lambda it: it.pos)
        index = next((i for i, it in enumerate(ordered) if it.id == selected.id), 0)
        next_item = ordered[min(index + 1, len(ordered) - 1)]
        if next_item.id == selected.id and index > 0:
            next_item = ordered[index - 1]

        pos = (selected.pos + next_item.pos) * 0.5
        pos = max(1e-3, min(1.0 - 1e-3, pos))
        color = selected.color

        new_item = _StopItem(id=next(self._id_counter), pos=float(pos), color=str(color))
        self._items.append(new_item)
        self._refresh_list(select_id=new_item.id)

    def _on_remove_stop(self) -> None:
        if self._current_id is None:
            return
        item = self._find_item(self._current_id)
        if item is None or self._is_endpoint(item):
            return

        self._items = [it for it in self._items if it.id != item.id]
        ordered = sorted(self._items, key=lambda it: it.pos)
        select_id = ordered[0].id if ordered else None
        self._refresh_list(select_id=select_id)

    def _on_reset_default(self) -> None:
        self._load_initial(None)

    def _on_apply_preset(self) -> None:
        data = str(self.preset_combo.currentData() or "builtin:spectracer")
        if data == "builtin:spectracer":
            self._load_initial(None)
            return

        if data.startswith("custom:"):
            try:
                _preset_name, stops = self._read_preset_csv(Path(data.removeprefix("custom:")))
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "热图色盘", f"无法加载自定义预设：{exc}")
                self._populate_preset_combo(select_data="builtin:spectracer")
                return
            self._load_initial(stops)
            return

        name = data.removeprefix("builtin:")

        try:
            cmap = colormaps[name]
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "热图色盘", f"无法加载预设色盘 {name}: {exc}")
            return

        # 采样 6 个节点，便于用户继续微调。
        sample_count = 6
        stops: list[ColorStop] = []
        for idx in range(sample_count):
            pos = idx / float(sample_count - 1)
            rgba = cmap(pos)
            r, g, b = (int(round(channel * 255.0)) for channel in rgba[:3])
            stops.append((pos, f"#{r:02x}{g:02x}{b:02x}"))

        self._load_initial(stops)

    def _on_export_csv(self) -> None:
        stops = self.stops()
        suggested = str(Path("spectracer_colormap.csv").resolve())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出色盘 CSV",
            suggested,
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path = f"{path}.csv"

        try:
            self._write_preset_csv(
                path=path,
                preset_name=self._current_preset_display_name(self.preset_combo),
                stops=stops,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "热图色盘", f"导出失败：{exc}")

    def _on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入色盘 CSV",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return

        try:
            _source_name, stops = self._read_preset_csv(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "热图色盘", f"导入失败：{exc}")
            return

        preset_name = self._prompt_preset_name(default_name=Path(path).stem)
        if preset_name is None:
            return

        existing_presets = {name: preset_path for name, preset_path in self._load_persisted_presets()}
        target_path = existing_presets.get(preset_name)
        if target_path is None:
            safe_name = self._sanitize_preset_filename(preset_name)
            target_path = (self._preset_dir / f"{safe_name}.csv").resolve()

        if target_path.exists():
            answer = QMessageBox.question(
                self,
                "覆盖预设",
                f"预设“{preset_name}”已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            self._write_preset_csv(path=target_path, preset_name=preset_name, stops=stops)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "热图色盘", f"保存预设失败：{exc}")
            return

        self._populate_preset_combo(select_data=f"custom:{target_path.as_posix()}")
        self._load_initial(stops)

    @staticmethod
    def _read_preset_csv(path: str | Path) -> tuple[str | None, list[ColorStop]]:
        csv_path = Path(path).expanduser().resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"文件不存在: {csv_path}")

        preset_name: str | None = None
        stops: list[ColorStop] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row or len(row) < 2:
                    continue

                pos_raw = str(row[0]).strip()
                color_raw = str(row[1]).strip()
                if not pos_raw or not color_raw:
                    continue

                if pos_raw.lower() in {"preset_name", "name"} and preset_name is None:
                    preset_name = color_raw
                    continue

                try:
                    pos = float(pos_raw)
                except (TypeError, ValueError):
                    # 可能是表头（例如 pos,color）
                    continue

                color = color_raw
                if color.lower().startswith("0x"):
                    color = f"#{color[2:]}"
                if not color.startswith("#"):
                    color = f"#{color}"

                qcolor = QColor(color)
                if not qcolor.isValid():
                    raise ValueError(f"颜色无效: {color_raw}")

                stops.append((pos, qcolor.name(QColor.NameFormat.HexRgb)))

        if len(stops) < 2:
            raise ValueError("CSV 至少需要 2 个有效节点（pos,color）")

        return preset_name, normalize_colormap_stops(stops)

    @staticmethod
    def _write_preset_csv(path: str | Path, *, preset_name: str, stops: Sequence[ColorStop]) -> None:
        output_path = Path(path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        normalized = normalize_colormap_stops(stops)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["preset_name", str(preset_name).strip() or output_path.stem])
            writer.writerow(["pos", "color"])
            for pos, color in normalized:
                writer.writerow([f"{float(pos):.6f}", str(color)])

    def accept(self) -> None:
        stops = self.stops()
        if len(stops) < 2:
            QMessageBox.warning(self, "热图色盘", "至少需要 2 个颜色节点")
            return
        super().accept()

    @classmethod
    def get_stops(
        cls,
        *,
        initial_stops: Sequence[ColorStop] | None = None,
        parent=None,
    ) -> list[ColorStop] | None:
        dialog = cls(initial_stops=initial_stops, parent=parent)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog.stops()
