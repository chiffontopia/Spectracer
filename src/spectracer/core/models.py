from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ChannelMode(str, Enum):
    """支持的声道模式。"""

    STEREO = "stereo"
    MONO = "mono"
    LEFT = "left"
    RIGHT = "right"
    SIDE = "l-r"

    @classmethod
    def choices(cls) -> list[str]:
        return [item.value for item in cls]

    @classmethod
    def ordered_modes(cls) -> list["ChannelMode"]:
        return [cls.STEREO, cls.MONO, cls.LEFT, cls.RIGHT, cls.SIDE]

    @property
    def display_name(self) -> str:
        labels = {
            ChannelMode.STEREO: "立体声",
            ChannelMode.MONO: "单声道",
            ChannelMode.LEFT: "仅 L",
            ChannelMode.RIGHT: "仅 R",
            ChannelMode.SIDE: "仅 L-R",
        }
        return labels[self]

    @classmethod
    def parse(cls, raw: str) -> "ChannelMode":
        normalized = raw.strip().lower()
        alias = {
            "lr": "l-r",
            "side": "l-r",
            "mid": "mono",
        }
        normalized = alias.get(normalized, normalized)
        try:
            return cls(normalized)
        except ValueError as exc:
            choices = ", ".join(cls.choices())
            raise ValueError(f"未知声道模式: {raw}，可选值: {choices}") from exc


@dataclass(slots=True)
class AnalysisParams:
    """频谱分析参数。"""

    fps: int = 50
    bins_per_semitone: float = 1.0
    octave_min: int = 1
    octave_max: int = 10
    a4_hz: float = 440.0
    sample_rate: int | None = 22050
    channel_mode: ChannelMode = ChannelMode.STEREO

    def validate(self) -> None:
        if not (1 <= self.fps <= 100):
            raise ValueError("fps 必须在 1~100 之间")
        if self.bins_per_semitone < 0.1:
            raise ValueError("bins_per_semitone 最小为 0.1")
        if self.octave_min < 0:
            raise ValueError("octave_min 不可小于 0")
        if self.octave_max < self.octave_min:
            raise ValueError("octave_max 必须大于等于 octave_min")
        if self.octave_max > 10:
            raise ValueError("octave_max 当前限制为 10")
        if not (400.0 <= self.a4_hz <= 480.0):
            raise ValueError("a4_hz 建议范围为 400.0~480.0")
        if self.sample_rate is not None and self.sample_rate < 8000:
            raise ValueError("sample_rate 不可低于 8000")

    @property
    def bins_per_octave(self) -> int:
        return max(12, int(round(12 * self.bins_per_semitone)))

    @property
    def octave_count(self) -> int:
        return self.octave_max - self.octave_min + 1

    @property
    def n_bins(self) -> int:
        return self.bins_per_octave * self.octave_count

    def hop_length_for(self, sample_rate: int) -> int:
        return max(1, int(round(sample_rate / self.fps)))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["channel_mode"] = self.channel_mode.value
        return data


@dataclass(slots=True)
class CqtResult:
    """CQT 分析输出。"""

    magnitude: Any  # shape: (frames, bins)
    frame_times: Any  # shape: (frames,)
    bin_frequencies: Any  # shape: (bins,)
    hop_length: int
    sample_rate: int

    def __post_init__(self) -> None:
        if getattr(self.magnitude, "ndim", -1) != 2:
            raise ValueError("magnitude 必须是二维数组 (frames, bins)")
        if getattr(self.frame_times, "ndim", -1) != 1:
            raise ValueError("frame_times 必须是一维数组")
        if getattr(self.bin_frequencies, "ndim", -1) != 1:
            raise ValueError("bin_frequencies 必须是一维数组")
        if self.magnitude.shape[0] != self.frame_times.shape[0]:
            raise ValueError("magnitude 的帧数与 frame_times 长度不一致")
        if self.magnitude.shape[1] != self.bin_frequencies.shape[0]:
            raise ValueError("magnitude 的分箱数与 bin_frequencies 长度不一致")

    @property
    def num_frames(self) -> int:
        return int(self.magnitude.shape[0])

    @property
    def num_bins(self) -> int:
        return int(self.magnitude.shape[1])

    @property
    def duration_seconds(self) -> float:
        if self.frame_times.size == 0:
            return 0.0
        return float(self.frame_times[-1])
