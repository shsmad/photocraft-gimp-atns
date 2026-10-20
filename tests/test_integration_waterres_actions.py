"""Integration-тест Watermark & Resize 1920 Actions: нужен `PHOTOCRAFT_CLI`."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys

from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CLI = os.environ.get("PHOTOCRAFT_CLI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not CLI, reason="PHOTOCRAFT_CLI не задан"),
]

sys.path.insert(0, str(REPO_ROOT / "src"))


def _load(name: str) -> ModuleType:
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"poc_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


maker = _load("make_waterres_actions")


def test_actions_generate_and_roundtrip(tmp_path: Path) -> None:
    assert CLI
    cli = Path(CLI).expanduser()
    assert cli.is_file(), f"PHOTOCRAFT_CLI: файл не найден: {cli}"

    specs = asyncio.run(maker.make_actions(cli, tmp_path))
    assert len(specs) == 2

    for spec in specs:
        atn = tmp_path / spec.atn_filename
        journal = tmp_path / spec.json_filename
        assert atn.is_file()
        assert atn.stat().st_size > 0
        payload = json.loads(journal.read_text(encoding="utf-8"))
        assert payload["action"]["name"] == spec.name
        assert len(payload["action"]["steps"]) == 9
        assert payload["saved"]["skipped"] == []
        ids = [step[0] for step in payload["action"]["steps"]]
        assert "image.imageSize" in ids
        assert "file.placeEmbedded" in ids
        assert "layer.translate" in ids
