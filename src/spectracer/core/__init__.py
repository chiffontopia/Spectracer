"""Core domain models and configuration types."""

from spectracer.core.analysis_results import (
    BeatAnchor,
    TempoAnalysisCandidate,
    TempoAnalysisResult,
    TempoSegment,
)
from spectracer.core.config import (
    DEFAULT_ANALYZE_CONFIG_PATH,
    AnalyzeCliConfig,
    load_analyze_cli_config,
    load_runtime_analyze_config,
)
from spectracer.core.harmonics import bin_index_from_plot_y, harmonic_bin_indices, nearest_bin_index
from spectracer.core.models import AnalysisParams, ChannelMode, CqtResult
from spectracer.core.pitch import (
    frequency_to_midi,
    frequency_to_note_name,
    is_black_key,
    midi_to_note_name,
)

__all__ = [
    "DEFAULT_ANALYZE_CONFIG_PATH",
    "AnalyzeCliConfig",
    "load_analyze_cli_config",
    "load_runtime_analyze_config",
    "bin_index_from_plot_y",
    "harmonic_bin_indices",
    "nearest_bin_index",
    "AnalysisParams",
    "ChannelMode",
    "CqtResult",
    "TempoAnalysisCandidate",
    "BeatAnchor",
    "TempoSegment",
    "TempoAnalysisResult",
    "frequency_to_midi",
    "frequency_to_note_name",
    "is_black_key",
    "midi_to_note_name",
]
