from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class EqBand:
    frequency_hz: float
    gain_db: float
    q: float = 1.0


@dataclass(slots=True)
class ProcessingSettings:
    eq_enabled: bool = False
    eq_bands: list[EqBand] = field(default_factory=list)
    denoise_amount: float = 0.0
    dereverb_amount: float = 0.0


def apply_processing(
    audio: np.ndarray,
    sample_rate: int,
    settings: ProcessingSettings | None = None,
) -> np.ndarray:
    """后处理占位实现。

    v0.2 计划中将扩展为可插拔处理链：
    - EQ
    - 去噪
    - 去混响
    - 立体声增强 / 分离
    """

    _ = sample_rate
    _ = settings
    return np.ascontiguousarray(audio, dtype=np.float32)
