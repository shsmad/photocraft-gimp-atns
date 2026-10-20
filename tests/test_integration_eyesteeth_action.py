"""Integration-тест генератора Eyes & Teeth Action: нужен `PHOTOCRAFT_CLI`."""

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


maker = _load("make_eyesteeth_action")


def test_action_generates_and_roundtrips(tmp_path: Path) -> None:
    assert CLI
    cli = Path(CLI).expanduser()
    assert cli.is_file(), f"PHOTOCRAFT_CLI: файл не найден: {cli}"

    spec = asyncio.run(maker.make_action(cli, tmp_path))
    atn = tmp_path / "eyesteeth.atn"
    journal = tmp_path / "eyesteeth.action.json"
    assert atn.is_file()
    assert atn.stat().st_size > 0
    assert journal.is_file()

    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert payload["action"]["name"] == spec.name
    assert payload["action"]["dialogs"] == [2, 6, 10]
    assert len(payload["action"]["steps"]) == 18
    assert payload["saved"]["skipped"] == []
