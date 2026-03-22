from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def bin_index_from_plot_y(plot_y: float, num_bins: int) -> int:
    """将热图坐标系中的 y 值映射到离散频率分箱索引。

    频谱图中每个音高块覆盖 [bin_index, bin_index + 1) 的区域，
    因此这里使用 floor 而不是 round，保证高亮区域能恰好覆盖单个块。
    """

    if num_bins <= 0:
        raise ValueError("num_bins 必须大于 0")

    clamped = min(max(float(plot_y), 0.0), max(0.0, float(num_bins) - 1e-9))
    return int(math.floor(clamped))


def nearest_bin_index(bin_frequencies: Sequence[float], target_frequency_hz: float) -> int:
    frequencies = np.asarray(bin_frequencies, dtype=np.float64)
    if frequencies.ndim != 1 or frequencies.size == 0:
        raise ValueError("bin_frequencies 必须是一维且非空")
    if target_frequency_hz <= 0:
        raise ValueError("target_frequency_hz 必须大于 0")

    index = int(np.searchsorted(frequencies, target_frequency_hz, side="left"))
    if index <= 0:
        return 0
    if index >= frequencies.size:
        return int(frequencies.size - 1)

    left = float(frequencies[index - 1])
    right = float(frequencies[index])
    if abs(target_frequency_hz - left) <= abs(right - target_frequency_hz):
        return index - 1
    return index


def harmonic_bin_indices(
    bin_frequencies: Sequence[float],
    base_bin_index: int,
    *,
    harmonic_count: int = 6,
) -> list[int]:
    """根据基频分箱索引，返回包含基频在内的倍音分箱索引列表。"""

    frequencies = np.asarray(bin_frequencies, dtype=np.float64)
    if frequencies.ndim != 1 or frequencies.size == 0:
        raise ValueError("bin_frequencies 必须是一维且非空")
    if not (0 <= base_bin_index < frequencies.size):
        raise ValueError("base_bin_index 超出范围")

    harmonic_count = max(1, int(harmonic_count))
    base_frequency = float(frequencies[base_bin_index])
    max_frequency = float(frequencies[-1])

    indices: list[int] = []
    seen: set[int] = set()

    for harmonic_number in range(1, harmonic_count + 1):
        target_frequency = base_frequency * harmonic_number
        if target_frequency > max_frequency * 1.001:
            break

        index = nearest_bin_index(frequencies, target_frequency)
        if index in seen:
            continue
        seen.add(index)
        indices.append(index)

    if base_bin_index not in seen:
        indices.insert(0, base_bin_index)

    return indices
