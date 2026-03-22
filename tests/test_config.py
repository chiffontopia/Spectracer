from __future__ import annotations

from pathlib import Path

from spectracer.core.config import AnalyzeCliConfig, load_analyze_cli_config
from spectracer.core.models import ChannelMode


def test_load_analyze_cli_config(tmp_path: Path) -> None:
    config_file = tmp_path / "analyze.toml"
    config_file.write_text(
        """
[analysis]
channel_mode = "l-r"
fps = 64
bins_per_semitone = 2.5
octave_min = 2
octave_max = 8
a4_hz = 442.0
sample_rate = 24000

[render]
sensitivity = 1.2
contrast = 1.5
preview_enabled = false

[processing]
fingerprint = "eq:preset=bright"
""".strip(),
        encoding="utf-8",
    )

    loaded = load_analyze_cli_config(config_file)

    assert loaded.channel_mode == ChannelMode.SIDE
    assert loaded.fps == 64
    assert loaded.bins_per_semitone == 2.5
    assert loaded.octave_min == 2
    assert loaded.octave_max == 8
    assert loaded.a4_hz == 442.0
    assert loaded.sample_rate == 24000
    assert loaded.sensitivity == 1.2
    assert loaded.contrast == 1.5
    assert loaded.preview_enabled is False
    assert loaded.processing_fingerprint == "eq:preset=bright"


def test_analyze_cli_config_defaults() -> None:
    default = AnalyzeCliConfig()
    assert default.channel_mode == ChannelMode.STEREO
    assert default.fps == 50
    assert default.sample_rate is None
    assert default.preview_enabled is True
