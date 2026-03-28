from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


@dataclass(slots=True)
class LoadedCacheEntry:
    cache_key: str
    paths: CachePaths
    result: CqtResult
    metadata: dict[str, Any]
    preview_path: Path | None
    playback_audio_path: Path | None


@dataclass(slots=True)
class CacheCleanupResult:
    removed_cache_keys: tuple[str, ...]
    removed_paths: tuple[Path, ...]
    freed_bytes: int

    @property
    def removed_count(self) -> int:
        return len(self.removed_cache_keys)


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

    def load_analysis(
        self,
        *,
        cache_key: str,
        require_preview: bool = False,
        require_playback_audio: bool = False,
    ) -> LoadedCacheEntry | None:
        paths = self.paths_for(cache_key)
        if not paths.metadata.exists():
            return None
        try:
            metadata = json.loads(paths.metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        resolved_paths = self._paths_from_metadata(cache_key=cache_key, metadata=metadata)
        if not self._is_cache_entry_complete(resolved_paths, require_preview=require_preview, require_playback_audio=require_playback_audio):
            return None
        result = self.load_cqt_result_from_paths(resolved_paths)
        return LoadedCacheEntry(
            cache_key=cache_key,
            paths=resolved_paths,
            result=result,
            metadata=metadata,
            preview_path=resolved_paths.preview if resolved_paths.preview.exists() else None,
            playback_audio_path=resolved_paths.playback_audio if resolved_paths.playback_audio.exists() else None,
        )

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

    def cleanup_unused(self, *, exclude_cache_keys: set[str] | None = None) -> CacheCleanupResult:
        excluded = set() if exclude_cache_keys is None else {str(cache_key) for cache_key in exclude_cache_keys}
        if not self.base_dir.exists():
            return CacheCleanupResult(removed_cache_keys=(), removed_paths=(), freed_bytes=0)

        removed_cache_keys: list[str] = []
        removed_paths: list[Path] = []
        freed_bytes = 0

        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            cache_key = child.name
            if cache_key in excluded:
                continue
            metadata_path = child / "meta.json"
            if not metadata_path.exists():
                continue
            freed_bytes += _directory_size_bytes(child)
            shutil.rmtree(child, ignore_errors=True)
            removed_cache_keys.append(cache_key)
            removed_paths.append(child)

        return CacheCleanupResult(
            removed_cache_keys=tuple(removed_cache_keys),
            removed_paths=tuple(removed_paths),
            freed_bytes=freed_bytes,
        )

    def _paths_from_metadata(self, *, cache_key: str, metadata: dict[str, Any]) -> CachePaths:
        root = self.base_dir / cache_key
        files = metadata.get("files") if isinstance(metadata.get("files"), dict) else {}
        return CachePaths(
            root=root,
            magnitude=root / str(files.get("magnitude", "magnitude.npy")),
            frame_times=root / str(files.get("frame_times", "frame_times.npy")),
            bin_frequencies=root / str(files.get("bin_frequencies", "bin_frequencies.npy")),
            metadata=root / str(files.get("metadata", "meta.json")),
            preview=root / str(files.get("preview", "preview.png")),
            playback_audio=root / str(files.get("playback_audio", "playback.wav")),
        )

    def _is_cache_entry_complete(
        self,
        paths: CachePaths,
        *,
        require_preview: bool,
        require_playback_audio: bool,
    ) -> bool:
        required_paths = [
            paths.root,
            paths.metadata,
            paths.magnitude,
            paths.frame_times,
            paths.bin_frequencies,
        ]
        if require_preview:
            required_paths.append(paths.preview)
        if require_playback_audio:
            required_paths.append(paths.playback_audio)
        return all(path.exists() for path in required_paths)

    def load_cqt_result_from_paths(self, paths: CachePaths) -> CqtResult:
        magnitude = np.load(paths.magnitude, mmap_mode="r")
        frame_times = np.load(paths.frame_times, mmap_mode="r")
        bin_frequencies = np.load(paths.bin_frequencies, mmap_mode="r")
        with paths.metadata.open("r", encoding="utf-8") as file:
            metadata = json.load(file)
        hop_length = int(metadata.get("hop_length", 0))
        sample_rate = int(metadata.get("sample_rate", 0))
        return CqtResult(
            magnitude=magnitude,
            frame_times=frame_times,
            bin_frequencies=bin_frequencies,
            hop_length=hop_length,
            sample_rate=sample_rate,
        )


def _directory_size_bytes(root: Path) -> int:
    total = 0
    for candidate in root.rglob("*"):
        if candidate.is_file():
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
    return total


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
