from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from spectracer.core.models import AnalysisParams, CqtResult


@dataclass(slots=True)
class CachePaths:
    root: Path
    magnitude: Path
    frame_times: Path
    bin_frequencies: Path
    metadata: Path
    preview: Path
    playback_audio: Path


class CacheStore:
    """分析结果缓存存储。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def ensure(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def paths_for(self, cache_key: str) -> CachePaths:
        root = self.base_dir / cache_key
        return CachePaths(
            root=root,
            magnitude=root / "magnitude.npy",
            frame_times=root / "frame_times.npy",
            bin_frequencies=root / "bin_frequencies.npy",
            metadata=root / "meta.json",
            preview=root / "preview.png",
            playback_audio=root / "playback.wav",
        )

    def build_cache_key(
        self,
        *,
        audio_path: str | Path,
        params: AnalysisParams,
        processing_fingerprint: str = "raw",
        audio_fingerprint: str | None = None,
    ) -> str:
        signature = {
            "schema": "spectracer-cache-v1",
            "audio_sha256": audio_fingerprint or file_fingerprint(audio_path),
            "audio_name": Path(audio_path).name,
            "analysis_params": params.to_dict(),
            "processing_fingerprint": processing_fingerprint,
        }
        serialized = json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()[:20]

    def save_analysis(
        self,
        *,
        cache_key: str,
        source_audio_path: str | Path,
        params: AnalysisParams,
        result: CqtResult,
        processing_fingerprint: str = "raw",
    ) -> CachePaths:
        self.ensure()
        paths = self.paths_for(cache_key)
        paths.root.mkdir(parents=True, exist_ok=True)

        np.save(paths.magnitude, result.magnitude)
        np.save(paths.frame_times, result.frame_times)
        np.save(paths.bin_frequencies, result.bin_frequencies)

        metadata = {
            "schema": "spectracer-cache-v1",
            "cache_key": cache_key,
            "created_at": datetime.now(UTC).isoformat(),
            "source_audio": str(Path(source_audio_path).resolve()),
            "analysis_params": params.to_dict(),
            "processing_fingerprint": processing_fingerprint,
            "shape": {
                "frames": result.num_frames,
                "bins": result.num_bins,
            },
            "sample_rate": result.sample_rate,
            "hop_length": result.hop_length,
            "files": {
                "magnitude": paths.magnitude.name,
                "frame_times": paths.frame_times.name,
                "bin_frequencies": paths.bin_frequencies.name,
                "preview": paths.preview.name,
                "playback_audio": paths.playback_audio.name,
            },
        }
        paths.metadata.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return paths


def file_fingerprint(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """计算文件 SHA256，用于缓存键。"""

    file_path = Path(path).expanduser().resolve()
    hasher = hashlib.sha256()
    with file_path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
