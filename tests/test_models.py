from __future__ import annotations

import pytest

from spectracer.core.harmonics import bin_index_from_plot_y, harmonic_bin_indices
from spectracer.core.models import AnalysisParams, ChannelMode


def test_channel_mode_parse_alias() -> None:
    assert ChannelMode.parse("lr") == ChannelMode.SIDE
    assert ChannelMode.parse("side") == ChannelMode.SIDE
    assert ChannelMode.parse("mid") == ChannelMode.MONO


def test_channel_mode_order_and_display_names() -> None:
    ordered = ChannelMode.ordered_modes()
    assert ordered == [
        ChannelMode.STEREO,
        ChannelMode.MONO,
        ChannelMode.LEFT,
        ChannelMode.RIGHT,
        ChannelMode.SIDE,
    ]
    assert ChannelMode.SIDE.display_name == "仅 L-R"


def test_analysis_params_derived_fields() -> None:
    params = AnalysisParams(
        fps=60,
        bins_per_semitone=2.0,
        octave_min=2,
        octave_max=6,
        a4_hz=440.0,
        sample_rate=22050,
        channel_mode=ChannelMode.MONO,
    )
    params.validate()

    assert params.bins_per_octave == 24
    assert params.octave_count == 5
    assert params.n_bins == 120
    assert params.hop_length_for(24000) == 400


def test_analysis_params_validate_error() -> None:
    params = AnalysisParams(fps=0)
    with pytest.raises(ValueError, match="fps"):
        params.validate()


def test_bin_index_mapping_uses_floor_for_single_pitch_block() -> None:
    assert bin_index_from_plot_y(0.00, 8) == 0
    assert bin_index_from_plot_y(0.99, 8) == 0
    assert bin_index_from_plot_y(1.00, 8) == 1
    assert bin_index_from_plot_y(7.99, 8) == 7


def test_harmonic_bin_indices_pick_nearest_bins() -> None:
    bin_frequencies = [100.0, 210.0, 320.0, 430.0, 540.0, 650.0]
    assert harmonic_bin_indices(bin_frequencies, 1, harmonic_count=6) == [1, 3, 5]
