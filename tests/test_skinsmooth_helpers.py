"""Юнит-тесты генератора Skin Smoothing Actions (без PhotoCraft)."""

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


maker = _load("make_skinsmooth_actions")


def test_blur_step_mapping() -> None:
    assert maker.blur_step("gaussian", 10, 0.2) == ("filter.blur.gaussianBlur", {"radius": 10})
    assert maker.blur_step("median", 10.4, 0.2) == ("filter.noise.median", {"radius": 10})
    assert maker.blur_step("median", 10.6, 0.2) == ("filter.noise.median", {"radius": 11})
    assert maker.blur_step("selective", 10, 0.2) == (
        "filter.blur.surfaceBlur", {"radius": 10, "threshold": 51},
    )


def test_layer_names() -> None:
    assert maker.soft_name("gaussian", 10) == "(a) Мягкость (Lighten Only, gaussian 10)"
    assert maker.low_name(3) == "НЧ (GB 3)"
    assert maker.high_name(3) == "ВЧ (HP 3)"


def test_default_specs_cover_three_blur_types() -> None:
    specs = maker.action_specs(
        blur_types=maker.BLUR_TYPES, radius=10, max_delta=0.2, gbhp=False,
        hp_radius=3, hp_opacity=50,
    )
    assert [spec.blur_type for spec in specs] == ["gaussian", "median", "selective"]
    assert [spec.dialogs for spec in specs] == [(2,), (2,), (2,)]
    for spec in specs:
        assert len(spec.steps) == 8
        assert spec.expected_children == (maker.soft_name(spec.blur_type, 10), "Оригинал")
        assert spec.stem == f"skinsmooth-{spec.blur_type}"


def test_gbhp_spec_order_and_dialogs() -> None:
    specs = maker.action_specs(
        blur_types=("gaussian",), radius=10, max_delta=0.2, gbhp=True,
        hp_radius=3, hp_opacity=50,
    )
    spec = specs[0]
    assert len(spec.steps) == 15
    assert spec.dialogs == (2, 5, 8)
    assert spec.expected_children == (
        maker.soft_name("gaussian", 10), "ВЧ (HP 3)", "НЧ (GB 3)", "Оригинал",
    )
    assert spec.stem == "skinsmooth-gaussian-gbhp"
