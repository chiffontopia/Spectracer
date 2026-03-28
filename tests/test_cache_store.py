from __future__ import annotations

from pathlib import Path

import numpy as np

from spectracer.core.models import AnalysisParams, ChannelMode, CqtResult
from spectracer.project.cache_store import CacheStore


def _make_cqt_result() -> CqtResult:
    return CqtResult(
        magnitude=np.ones((4, 3), dtype=np.float32),
        frame_times=np.array([0.0, 0.1, 0.2, 0.3], dtype=np.float32),
        bin_frequencies=np.array([110.0, 220.0, 440.0], dtype=np.float32),
        hop_length=512,
        sample_rate=22050,
    )


def test_cache_store_cleanup_unused_preserves_excluded_cache_keys(tmp_path: Path, sample_wav_path: Path) -> None:
    cache_store = CacheStore(tmp_path / "cache-store")
    params = AnalysisParams(
        fps=24,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )
    cqt_result = _make_cqt_result()

    keep_key = cache_store.build_cache_key(
        audio_path=sample_wav_path,
        params=params,
        processing_fingerprint="keep",
        audio_fingerprint="fingerprint-keep",
    )
    remove_key = cache_store.build_cache_key(
        audio_path=sample_wav_path,
        params=params,
        processing_fingerprint="remove",
        audio_fingerprint="fingerprint-remove",
    )

    keep_paths = cache_store.save_analysis(
        cache_key=keep_key,
        source_audio_path=sample_wav_path,
        params=params,
        result=cqt_result,
        processing_fingerprint="keep",
    )
    remove_paths = cache_store.save_analysis(
        cache_key=remove_key,
        source_audio_path=sample_wav_path,
        params=params,
        result=cqt_result,
        processing_fingerprint="remove",
    )

    cleanup_result = cache_store.cleanup_unused(exclude_cache_keys={keep_key})

    assert keep_paths.root.exists()
    assert remove_paths.root.exists() is False
    assert cleanup_result.removed_cache_keys == (remove_key,)
    assert cleanup_result.removed_paths == (remove_paths.root,)
    assert cleanup_result.freed_bytes > 0
