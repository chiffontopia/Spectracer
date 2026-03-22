from __future__ import annotations

from pathlib import Path


def export_notes_to_midi(output_path: str | Path) -> Path:
    """MIDI 导出占位函数。"""

    path = Path(output_path)
    raise NotImplementedError(f"尚未实现 MIDI 导出: {path}")
