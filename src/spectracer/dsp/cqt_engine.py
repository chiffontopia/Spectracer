from __future__ import annotations

import librosa
import numpy as np

from spectracer.core.models import AnalysisParams, CqtResult


def midi_to_frequency(midi: float, a4_hz: float = 440.0) -> float:
    return float(a4_hz * (2.0 ** ((midi - 69.0) / 12.0)))


def c_midi_for_octave(octave: int) -> int:
    # MIDI: C0 = 12, C1 = 24 ...
    return 12 * (octave + 1)


def _effective_bin_count(
    *,
    sample_rate: int,
    fmin: float,
    requested_n_bins: int,
    bins_per_octave: int,
) -> int:
    nyquist = sample_rate / 2.0
    candidate_freqs = librosa.cqt_frequencies(
        n_bins=requested_n_bins,
        fmin=fmin,
        bins_per_octave=bins_per_octave,
    )
    valid_n_bins = int(np.sum(candidate_freqs < (nyquist * 0.98)))
    return max(0, valid_n_bins)


def compute_cqt_complex(
    signal: np.ndarray,
    sample_rate: int,
    params: AnalysisParams,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """执行 CQT 并返回 complex 结果。

    返回值:
        - cqt_complex: shape (bins, frames)
        - frame_times: shape (frames,)
        - bin_frequencies: shape (bins,)
        - hop_length

    该接口用于在多声道模式下复用 CQT 计算结果（例如 mono/side 可以由 L/R 的 complex CQT 线性组合得到）。
    """

    params.validate()
    if signal.ndim != 1:
        raise ValueError("signal 必须是一维数组")

    hop_length = params.hop_length_for(sample_rate)
    bins_per_octave = params.bins_per_octave
    requested_n_bins = params.n_bins

    c_midi = c_midi_for_octave(params.octave_min)
    fmin = midi_to_frequency(c_midi, a4_hz=params.a4_hz)

    valid_n_bins = _effective_bin_count(
        sample_rate=sample_rate,
        fmin=fmin,
        requested_n_bins=requested_n_bins,
        bins_per_octave=bins_per_octave,
    )
    if valid_n_bins < 12:
        raise ValueError(
            "在当前采样率和八度范围下，可用频率分箱不足。请降低 octave_max 或提高采样率。"
        )

    n_bins = min(requested_n_bins, valid_n_bins)

    cqt_complex = librosa.cqt(
        y=signal,
        sr=sample_rate,
        hop_length=hop_length,
        fmin=fmin,
        n_bins=n_bins,
        bins_per_octave=bins_per_octave,
        tuning=0.0,
    )

    # librosa 返回的轴序为 (bins, frames)
    cqt_complex = np.ascontiguousarray(cqt_complex)
    frame_times = librosa.frames_to_time(
        np.arange(cqt_complex.shape[1]),
        sr=sample_rate,
        hop_length=hop_length,
    ).astype(np.float32)
    bin_frequencies = librosa.cqt_frequencies(
        n_bins=n_bins,
        fmin=fmin,
        bins_per_octave=bins_per_octave,
    ).astype(np.float32)

    return cqt_complex, frame_times, bin_frequencies, hop_length


def compute_cqt(signal: np.ndarray, sample_rate: int, params: AnalysisParams) -> CqtResult:
    """执行 CQT 分析并返回统一结果结构。"""

    cqt_complex, frame_times, bin_frequencies, hop_length = compute_cqt_complex(
        signal=signal,
        sample_rate=sample_rate,
        params=params,
    )
    magnitude = np.abs(cqt_complex).T.astype(np.float32)

    return CqtResult(
        magnitude=magnitude,
        frame_times=frame_times,
        bin_frequencies=bin_frequencies,
        hop_length=hop_length,
        sample_rate=int(sample_rate),
    )
