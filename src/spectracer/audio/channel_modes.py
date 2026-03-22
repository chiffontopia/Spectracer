from __future__ import annotations

import numpy as np

from spectracer.core.models import ChannelMode


def apply_channel_mode(audio: np.ndarray, mode: ChannelMode) -> np.ndarray:
    """将多声道信号转换为用于分析的一维信号。"""

    if audio.ndim != 2:
        raise ValueError("audio 必须是二维数组 (channels, samples)")
    if audio.shape[0] < 1:
        raise ValueError("audio 至少需要 1 个声道")

    left = audio[0]
    right = audio[1] if audio.shape[0] > 1 else audio[0]

    if mode in (ChannelMode.STEREO, ChannelMode.MONO):
        mixed = np.mean(audio, axis=0)
    elif mode == ChannelMode.LEFT:
        mixed = left
    elif mode == ChannelMode.RIGHT:
        mixed = right
    elif mode == ChannelMode.SIDE:
        mixed = left - right
    else:  # pragma: no cover
        raise ValueError(f"未处理的声道模式: {mode}")

    return np.ascontiguousarray(mixed, dtype=np.float32)
