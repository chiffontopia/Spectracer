from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from spectracer.core.models import ChannelMode

DEFAULT_ANALYZE_CONFIG_PATH = Path("config/analysis.default.toml")


@dataclass(slots=True)
class AnalyzeCliConfig:
    """analyze 子命令的可配置默认参数。"""

    channel_mode: ChannelMode = ChannelMode.STEREO
    fps: int = 50
    bins_per_semitone: float = 1.0
    octave_min: int = 1
    octave_max: int = 10
    a4_hz: float = 440.0
    sample_rate: int | None = None
    sensitivity: float = 1.0
    contrast: float = 1.0
    preview_enabled: bool = True
    processing_fingerprint: str = "raw"

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> "AnalyzeCliConfig":
        analysis = mapping.get("analysis", {})
        render = mapping.get("render", {})
        processing = mapping.get("processing", {})

        if not isinstance(analysis, dict):
            raise ValueError("[analysis] 配置必须是对象")
        if not isinstance(render, dict):
            raise ValueError("[render] 配置必须是对象")
        if not isinstance(processing, dict):
            raise ValueError("[processing] 配置必须是对象")

        default = cls()

        channel_mode_raw = analysis.get("channel_mode", default.channel_mode.value)
        channel_mode = ChannelMode.parse(str(channel_mode_raw))

        sample_rate = _parse_optional_sample_rate(analysis.get("sample_rate", default.sample_rate))

        return cls(
            channel_mode=channel_mode,
            fps=_as_int(analysis.get("fps", default.fps), field_name="analysis.fps"),
            bins_per_semitone=_as_float(
                analysis.get("bins_per_semitone", default.bins_per_semitone),
                field_name="analysis.bins_per_semitone",
            ),
            octave_min=_as_int(analysis.get("octave_min", default.octave_min), field_name="analysis.octave_min"),
            octave_max=_as_int(analysis.get("octave_max", default.octave_max), field_name="analysis.octave_max"),
            a4_hz=_as_float(analysis.get("a4_hz", default.a4_hz), field_name="analysis.a4_hz"),
            sample_rate=sample_rate,
            sensitivity=_as_float(render.get("sensitivity", default.sensitivity), field_name="render.sensitivity"),
            contrast=_as_float(render.get("contrast", default.contrast), field_name="render.contrast"),
            preview_enabled=bool(render.get("preview_enabled", default.preview_enabled)),
            processing_fingerprint=str(processing.get("fingerprint", default.processing_fingerprint)),
        )


def load_analyze_cli_config(path: str | Path) -> AnalyzeCliConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    content = config_path.read_text(encoding="utf-8")
    payload = tomllib.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("配置文件内容不是有效对象")

    return AnalyzeCliConfig.from_mapping(payload)


def load_runtime_analyze_config(explicit_path: str | Path | None) -> tuple[AnalyzeCliConfig, Path | None]:
    """加载运行时配置。

    优先级：
    1) `--config` 指定路径
    2) 仓库默认路径 `config/analysis.default.toml`（若存在）
    3) 代码内建默认值
    """

    if explicit_path is not None:
        path = Path(explicit_path).expanduser().resolve()
        return load_analyze_cli_config(path), path

    if DEFAULT_ANALYZE_CONFIG_PATH.exists():
        resolved = DEFAULT_ANALYZE_CONFIG_PATH.resolve()
        return load_analyze_cli_config(resolved), resolved

    return AnalyzeCliConfig(), None


def _as_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc


def _as_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc


def _parse_optional_sample_rate(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "0", "none", "null"}:
        return None

    parsed = _as_int(value, field_name="analysis.sample_rate")
    if parsed == 0:
        return None
    return parsed
