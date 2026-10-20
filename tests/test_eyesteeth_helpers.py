"""Юнит-тесты генератора Eyes & Teeth Action (без PhotoCraft)."""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path
from types import ModuleType

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


maker = _load("make_eyesteeth_action")


def test_action_spec_identity_and_dialogs() -> None:
    spec = maker.action_spec()
    assert spec.name == "Eyes & Teeth"
    assert spec.group == "Глаза и зубы"
    assert spec.dialogs == (2, 6, 10)
    assert spec.expected_children == ("Радужка", "Зубы", "Белки")
    assert len(spec.steps) == 18


def test_steps_make_black_masks_and_group() -> None:
    steps = maker.action_steps()
    assert steps[0] == ("select.all", {})
    assert steps[1] == ("select.inverse", {})
    assert steps[2][0] == "layer.newAdjustmentLayer.hueSaturation"
    assert steps[2][1] == {"hue": 0, "saturation": -70, "lightness": 60}
    assert steps[3] == ("layer.setProps", {"name": "Белки"})
    assert steps[10][1] == {"hue": 0, "saturation": 35, "lightness": 10}
    assert steps[14] == ("layer.select", {"layer": "Радужка", "mode": "add"})
    assert steps[15] == ("layer.groupLayers", {})
    assert steps[16] == ("layer.setProps", {"name": "Глаза и зубы"})
    assert steps[17] == ("select.deselect", {})
