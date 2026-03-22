from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProjectInfo:
    root: Path
    project_file: Path


class ProjectService:
    """管理 `.srproj` 项目目录。"""

    DEFAULT_SUBDIRS = (
        "audio",
        "analysis",
        "midi",
        "session",
        "history",
    )

    def create_empty_project(self, path: str | Path, *, overwrite: bool = False) -> ProjectInfo:
        root = normalize_project_path(path)
        root.mkdir(parents=True, exist_ok=True)

        for name in self.DEFAULT_SUBDIRS:
            (root / name).mkdir(parents=True, exist_ok=True)

        project_file = root / "project.json"
        if project_file.exists() and not overwrite:
            raise FileExistsError(f"project.json 已存在: {project_file}")

        payload = {
            "schema": "spectracer-project-v1",
            "name": root.stem,
            "version": "0.1.0",
            "audio": {
                "original": None,
                "processed": None,
            },
            "analysis": {
                "active_cache_key": None,
            },
        }
        project_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return ProjectInfo(root=root, project_file=project_file)


def normalize_project_path(path: str | Path) -> Path:
    raw = Path(path).expanduser().resolve()
    if raw.suffix.lower() != ".srproj":
        return raw.with_suffix(".srproj")
    return raw
