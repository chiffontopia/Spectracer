from __future__ import annotations

import threading
import warnings
from pathlib import Path

import numpy as np

from spectracer.core.models import CqtResult
from spectracer.dsp.visualization import save_cqt_heatmap


def _make_cqt_result() -> CqtResult:
    frame_times = np.linspace(0.0, 1.0, 8, dtype=np.float32)
    bin_frequencies = np.array([110.0, 220.0, 440.0, 880.0], dtype=np.float32)
    magnitude = np.linspace(0.1, 1.0, frame_times.size * bin_frequencies.size, dtype=np.float32).reshape(
        frame_times.size,
        bin_frequencies.size,
    )
    return CqtResult(
        magnitude=magnitude,
        frame_times=frame_times,
        bin_frequencies=bin_frequencies,
        hop_length=512,
        sample_rate=22050,
    )


def test_save_cqt_heatmap_from_background_thread_avoids_matplotlib_gui_warning(tmp_path: Path) -> None:
    result = _make_cqt_result()
    output_path = tmp_path / "preview.png"
    warning_messages: list[str] = []
    errors: list[BaseException] = []

    def _render_preview() -> None:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                save_cqt_heatmap(result, output_path)
            warning_messages.extend(str(item.message) for item in caught)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_render_preview, name="preview-render-thread")
    thread.start()
    thread.join(timeout=10.0)

    assert thread.is_alive() is False
    assert errors == []
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert not any("Starting a Matplotlib GUI outside of the main thread" in message for message in warning_messages)
