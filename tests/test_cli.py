from __future__ import annotations

from pathlib import Path

from spectracer.cli import main


def test_cli_init_project(tmp_path: Path) -> None:
    project_root = tmp_path / "demo_song"
    exit_code = main(["init-project", str(project_root)])
    assert exit_code == 0
    assert (tmp_path / "demo_song.srproj" / "project.json").exists()


def test_cli_analyze_missing_file_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wav"
    exit_code = main(["analyze", str(missing), "--output", str(tmp_path / "cache")])
    assert exit_code == 2
