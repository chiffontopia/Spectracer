from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


def load_audio(path: str | Path, target_sample_rate: int | None = None) -> tuple[np.ndarray, int]:
    """读取音频并返回 `(channels, samples)` 格式的 float32 数组。"""

    audio_path = Path(path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    # mono=False => 保留多声道；sr=None => 不重采样。
    signal, sample_rate = librosa.load(str(audio_path), sr=target_sample_rate, mono=False)

    if signal.ndim == 1:
        signal = signal[np.newaxis, :]

    return np.ascontiguousarray(signal, dtype=np.float32), int(sample_rate)
