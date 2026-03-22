from __future__ import annotations

from typing import Sequence

from matplotlib.colors import Colormap, LinearSegmentedColormap

ColorStop = tuple[float, str]


_DEFAULT_SPECTRACER_STOPS: list[ColorStop] = [
    (0.00, "#000000"),  # 无 / 极低音量
    (0.10, "#0000FF"),
    (0.45, "#8FFF99"),
    (0.65, "#FDFF14"),
    (0.90, "#F00000"),
    (1.00, "#FF0000"),
]


def default_spectracer_colormap_stops() -> list[ColorStop]:
    return list(_DEFAULT_SPECTRACER_STOPS)


def normalize_colormap_stops(stops: Sequence[ColorStop]) -> list[ColorStop]:
    """清洗并规范化颜色节点：

    - 位置 clamp 到 [0, 1]
    - 按位置升序排序
    - 合并重复位置（保留最后一个颜色）
    - 强制首尾位置为 0/1
    """

    cleaned: list[ColorStop] = []
    for raw_pos, raw_color in stops:
        try:
            pos = float(raw_pos)
        except (TypeError, ValueError):
            continue
        pos = max(0.0, min(1.0, pos))
        cleaned.append((pos, str(raw_color)))

    if len(cleaned) < 2:
        cleaned = default_spectracer_colormap_stops()

    cleaned.sort(key=lambda pair: pair[0])

    merged: list[ColorStop] = []
    for pos, color in cleaned:
        if merged and abs(pos - merged[-1][0]) < 1e-9:
            merged[-1] = (merged[-1][0], color)
        else:
            merged.append((pos, color))

    if merged[0][0] > 0.0:
        merged.insert(0, (0.0, merged[0][1]))
    else:
        merged[0] = (0.0, merged[0][1])

    if merged[-1][0] < 1.0:
        merged.append((1.0, merged[-1][1]))
    else:
        merged[-1] = (1.0, merged[-1][1])

    if len(merged) < 2:
        merged = [(0.0, "#000000"), (1.0, "#ff0000")]

    return merged


def make_linear_colormap(*, name: str, stops: Sequence[ColorStop]) -> LinearSegmentedColormap:
    normalized = normalize_colormap_stops(stops)
    return LinearSegmentedColormap.from_list(name, normalized)


def make_spectracer_colormap(stops: Sequence[ColorStop] | None = None) -> LinearSegmentedColormap:
    """构建 Spectracer 热图色盘：黑 -> 蓝 -> 绿 -> 红。"""

    return make_linear_colormap(
        name="spectracer",
        stops=default_spectracer_colormap_stops() if stops is None else stops,
    )
