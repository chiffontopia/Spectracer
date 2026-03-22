from __future__ import annotations

import json
from pathlib import Path

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
    assert result.timings_ms["total_ms"] > 0

    metadata = json.loads(result.cache_paths.metadata.read_text(encoding="utf-8"))
    assert metadata["schema"] == "spectracer-cache-v1"
    assert metadata["cache_key"] == result.cache_key
    assert Path(metadata["source_audio"]).name == sample_wav_path.name


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
