from __future__ import annotations

from enum import Enum
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np

from spectracer.core.models import CqtResult
from spectracer.dsp.colormap import make_spectracer_colormap


class NormalizationMode(str, Enum):
    """热图归一化策略。"""

    DB_MAX = "db_max"
    DB_PERCENTILE = "db_percentile"

    @classmethod
    def parse(cls, raw: str | "NormalizationMode") -> "NormalizationMode":
        if isinstance(raw, NormalizationMode):
            return raw
        value = str(raw).strip().lower()
        for mode in NormalizationMode:
            if mode.value == value:
                return mode
        return NormalizationMode.DB_MAX


def normalize_cqt_for_display(
    result: CqtResult,
    *,
    sensitivity: float = 1.0,
    contrast: float = 1.0,
    mode: NormalizationMode | str = NormalizationMode.DB_MAX,
    ref_percentile: float = 99.5,
) -> np.ndarray:
    """将 CQT 幅度归一化为适合显示的 bins x frames 浮点图像。"""

    sensitivity = max(0.1, float(sensitivity))
    contrast = max(0.1, float(contrast))

    mode = NormalizationMode.parse(mode)

    magnitude = np.asarray(result.magnitude, dtype=np.float32)
    if magnitude.size == 0:
        return np.zeros((result.num_bins, result.num_frames), dtype=np.float32)

    max_magnitude = float(np.nanmax(magnitude))
    if not np.isfinite(max_magnitude) or max_magnitude <= 0.0:
        # 静音（例如双声道完全相同导致的 L-R=0），直接显示为黑。
        return np.zeros((result.num_bins, result.num_frames), dtype=np.float32)

    ref_value = max_magnitude
    if mode == NormalizationMode.DB_PERCENTILE:
        positive = magnitude[magnitude > 0.0]
        if positive.size == 0:
            return np.zeros((result.num_bins, result.num_frames), dtype=np.float32)
        q = max(0.0, min(100.0, float(ref_percentile)))
        ref_value = float(np.percentile(positive, q))
        if not np.isfinite(ref_value) or ref_value <= 0.0:
            ref_value = max_magnitude

    db = librosa.amplitude_to_db(magnitude.T, ref=ref_value, amin=1e-10, top_db=None)

    floor_db = -80.0 / contrast
    db = np.clip(db, floor_db, 0.0)
    normalized = (db - floor_db) / abs(floor_db)
    normalized = np.power(normalized, 1.0 / sensitivity)
    return normalized.astype(np.float32)


def save_cqt_heatmap(
    result: CqtResult,
    output_path: str | Path,
    *,
    sensitivity: float = 1.0,
    contrast: float = 1.0,
) -> Path:
    """保存 CQT 热图预览图。"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    normalized = normalize_cqt_for_display(
        result,
        sensitivity=sensitivity,
        contrast=contrast,
    )

    left = 0.0
    right = float(result.frame_times[-1]) if result.frame_times.size > 0 else 1.0
    if right <= left:
        right = left + 1e-3

    bottom = float(result.bin_frequencies[0])
    top = float(result.bin_frequencies[-1])

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    image = ax.imshow(
        normalized,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        cmap=make_spectracer_colormap(),
        extent=[left, right, bottom, top],
    )

    ax.set_title("Spectracer CQT Heatmap Preview")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_yscale("log")

    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Relative Intensity")

    fig.tight_layout()
    fig.savefig(output)
    plt.close(fig)

    return output
