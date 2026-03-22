from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def sample_wav_path() -> Path:
    path = ROOT / "tests" / "brotherjohn_120bpm_4bars_sinewave.wav"
    if not path.exists():
        pytest.skip(f"缺少测试音频: {path}")
    return path
