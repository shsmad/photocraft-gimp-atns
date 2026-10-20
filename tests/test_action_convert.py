"""Юнит-тесты конвертера Action JSON ↔ `.atn` (без PhotoCraft)."""

from __future__ import annotations

import importlib.util
import json
import sys

from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
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


convert = _load("convert_action")


def test_default_paths_keep_action_names() -> None:
    assert convert.default_atn_path(Path("actions/freqautosplit.action.json")) == Path("actions/freqautosplit.atn")
    assert convert.default_atn_path(Path("foo.json")) == Path("foo.atn")
    assert convert.default_json_path(Path("actions/freqautosplit.atn")) == Path(
        "actions/freqautosplit.action.json",
    )


def test_load_actions_accepts_journal_and_multi(tmp_path: Path) -> None:
    single = tmp_path / "single.action.json"
    single.write_text(json.dumps({"action": {"name": "A", "steps": []}}), encoding="utf-8")
    assert convert.load_actions(single) == [{"name": "A", "steps": []}]

    multi = tmp_path / "multi.json"
    payload = {"actions": [{"name": "A", "steps": []}, {"name": "B", "steps": []}]}
    multi.write_text(json.dumps(payload), encoding="utf-8")
    assert [action["name"] for action in convert.load_actions(multi)] == ["A", "B"]


def test_load_actions_rejects_missing_steps(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"action": {"name": "A"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        convert.load_actions(path)


def test_pick_action_requires_name_for_multiple() -> None:
    actions = [{"name": "A", "steps": []}, {"name": "B", "steps": []}]
    with pytest.raises(ValueError):
        convert.pick_action(actions, None)
    assert convert.pick_action(actions, "B")["name"] == "B"


def test_validate_atn() -> None:
    convert.validate_atn(convert.ATN_MAGIC + b"\x00" * 8)
    with pytest.raises(ValueError):
        convert.validate_atn(b"not-an-atn")


def test_ensure_writable_guards_existing(tmp_path: Path) -> None:
    path = tmp_path / "out.atn"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        convert.ensure_writable(path, force=False)
    convert.ensure_writable(path, force=True)
