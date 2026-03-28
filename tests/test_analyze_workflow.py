from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import spectracer.app.analysis_workflow as analysis_workflow_module
from spectracer.app.analysis_workflow import (
    AnalyzeExecutionOptions,
    execute_analysis,
    execute_multi_channel_analysis,
)
from spectracer.core.models import AnalysisParams, ChannelMode


def test_execute_analysis_generates_cache(tmp_path: Path, sample_wav_path: Path) -> None:
    output_dir = tmp_path / "cache"

    params = AnalysisParams(
        fps=30,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )

    result = execute_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        options=AnalyzeExecutionOptions(save_preview=False),
    )

    assert result.cache_paths.root.exists()
    assert result.cache_paths.magnitude.exists()
    assert result.cache_paths.frame_times.exists()
    assert result.cache_paths.bin_frequencies.exists()
    assert result.cache_paths.metadata.exists()
    assert not result.cache_paths.preview.exists()

    assert result.num_frames > 0
    assert result.num_bins > 0
    assert result.sample_rate > 0
    assert isinstance(result.cqt_result.magnitude, np.memmap)
    assert result.timings_ms["total_ms"] > 0

    metadata = json.loads(result.cache_paths.metadata.read_text(encoding="utf-8"))
    assert metadata["schema"] == "spectracer-cache-v1"
    assert metadata["cache_key"] == result.cache_key
    assert Path(metadata["source_audio"]).name == sample_wav_path.name


def test_execute_analysis_reuses_existing_cache_without_loading_audio(
    tmp_path: Path,
    sample_wav_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "cache-reuse"
    params = AnalysisParams(
        fps=30,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )
    options = AnalyzeExecutionOptions(save_preview=False, save_playback_audio=True)

    first_result = execute_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        options=options,
    )
    assert first_result.playback_audio_path is not None
    assert first_result.playback_audio_path.exists()

    def _unexpected_load_audio(*args, **kwargs):
        raise AssertionError("缓存命中后不应再次加载音频")

    monkeypatch.setattr(analysis_workflow_module, "load_audio", _unexpected_load_audio)

    second_result = execute_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        options=options,
    )

    assert second_result.cache_key == first_result.cache_key
    assert isinstance(second_result.cqt_result.magnitude, np.memmap)
    assert second_result.timings_ms["cache_read_ms"] > 0.0
    assert second_result.timings_ms["compute_cqt_ms"] == pytest.approx(0.0)
    assert second_result.playback_audio_path == first_result.playback_audio_path


def test_execute_multi_channel_analysis_generates_results_for_multiple_modes(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    output_dir = tmp_path / "multi-cache"
    params = AnalysisParams(
        fps=24,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )

    result = execute_multi_channel_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        channel_modes=[ChannelMode.STEREO, ChannelMode.LEFT, ChannelMode.RIGHT],
        options=AnalyzeExecutionOptions(save_preview=False),
    )

    assert set(result.results_by_mode.keys()) == {
        ChannelMode.STEREO,
        ChannelMode.LEFT,
        ChannelMode.RIGHT,
    }
    assert result.total_ms > 0
    assert result.load_audio_ms > 0

    cache_keys = {analysis_result.cache_key for analysis_result in result.results_by_mode.values()}
    assert len(cache_keys) == 3

    for mode, analysis_result in result.results_by_mode.items():
        assert analysis_result.effective_params.channel_mode == mode
        assert analysis_result.cache_paths.metadata.exists()
        assert analysis_result.num_frames > 0
        assert isinstance(analysis_result.cqt_result.magnitude, np.memmap)
        assert analysis_result.num_bins > 0


def test_execute_multi_channel_analysis_reports_progress_and_saves_playback_audio(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    output_dir = tmp_path / "multi-cache-with-playback"
    params = AnalysisParams(
        fps=24,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )

    progress_events: list[tuple[int, int, str]] = []

    result = execute_multi_channel_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        channel_modes=[ChannelMode.STEREO, ChannelMode.LEFT],
        options=AnalyzeExecutionOptions(save_preview=False, save_playback_audio=True),
        progress_callback=lambda progress: progress_events.append(
            (progress.completed_steps, progress.total_steps, progress.message)
        ),
    )

    assert progress_events
    assert progress_events[0][0] == 0
    assert progress_events[-1][0] == progress_events[-1][1]

    for analysis_result in result.results_by_mode.values():
        assert analysis_result.playback_audio_path is not None
        assert analysis_result.playback_audio_path.exists()
        assert analysis_result.cache_paths.playback_audio.exists()


def test_execute_multi_channel_analysis_full_cache_hit_skips_incremental_mode_ready_callbacks(
    tmp_path: Path,
    sample_wav_path: Path,
) -> None:
    output_dir = tmp_path / "full-cache-hit"
    params = AnalysisParams(
        fps=24,
        bins_per_semitone=1.0,
        octave_min=1,
        octave_max=7,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )
    options = AnalyzeExecutionOptions(save_preview=False, save_playback_audio=True)
    channel_modes = [ChannelMode.STEREO, ChannelMode.LEFT]

    execute_multi_channel_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        channel_modes=channel_modes,
        options=options,
    )

    ready_events: list[ChannelMode] = []
    cached_result = execute_multi_channel_analysis(
        input_path=sample_wav_path,
        output_dir=output_dir,
        params=params,
        channel_modes=channel_modes,
        options=options,
        mode_result_callback=lambda mode, _result: ready_events.append(mode),
    )

    assert ready_events == []
    assert set(cached_result.results_by_mode.keys()) == set(channel_modes)
    assert cached_result.load_audio_ms == pytest.approx(0.0)
