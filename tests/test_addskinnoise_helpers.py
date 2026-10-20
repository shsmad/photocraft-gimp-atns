"""Юнит-тесты чистых хелперов Add Skin Noise PoC (без PhotoCraft)."""

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


noise = _load("addskinnoise_poc")


def test_layer_names_include_recipe() -> None:
    assert noise.layer_name("pore", "gaussian", 0.3, 1.0) == "Кожа: поры (Emboss 1.0)"
    assert noise.layer_name("pore", "gaussian", 0.3, 2) == "Кожа: поры (Emboss 2.0)"
    assert noise.layer_name("texture", "median", 2) == "Кожа: текстура (Median 2.0)"


def test_mode_defaults() -> None:
    assert noise.resolve_amount("pore", None) == 3.0
    assert noise.resolve_amount("texture", None) == 12.0
    assert noise.resolve_blur_type("pore", None) == "gaussian"
    assert noise.resolve_blur_type("texture", None) == "median"
    assert noise.resolve_blur_size("pore", None) == 0.3
    assert noise.resolve_blur_size("texture", None) == 2.0
    assert noise.resolve_steps("pore", None) == 1
    assert noise.resolve_steps("texture", None) == 2
    assert noise.resolve_blend("pore", None) == "Overlay"
    assert noise.resolve_blend("texture", None) == "Overlay"
    assert noise.resolve_opacity("pore", None) == 0.5
    assert noise.resolve_opacity("texture", None) == 1.0
    assert noise.resolve_distribution("pore", None) == "gaussian"
    assert noise.resolve_distribution("texture", None) == "uniform"


def test_explicit_overrides() -> None:
    assert noise.resolve_amount("pore", 5.0) == 5.0
    assert noise.resolve_blur_type("texture", "gaussian") == "gaussian"
    assert noise.resolve_blur_size("texture", 1.5) == 1.5
    assert noise.resolve_steps("texture", 1) == 1
    assert noise.resolve_blend("pore", "Soft Light") == "Soft Light"
    assert noise.resolve_opacity("texture", 0.4) == 0.4
    assert noise.resolve_distribution("texture", "gaussian") == "gaussian"


def test_second_pass_is_half() -> None:
    assert noise.second_value(12.0) == 6.0
    assert noise.second_value(2.0) == 1.0


def test_parse_args_defaults_and_overrides() -> None:
    args = noise.parse_args(["--input", "photo.jpg"])
    assert args.mode == "pore"
    assert args.amount is None
    assert args.blur_type is None
    assert args.blur_size is None
    assert args.steps is None
    assert args.mask == "none"
    assert args.emboss_height is None
    assert args.monochromatic is True

    args = noise.parse_args(
        ["--input", "photo.jpg", "--mode", "texture", "--steps", "1",
         "--blur-type", "gaussian", "--no-monochromatic", "--opacity", "0.4", "--mask", "skin"],
    )
    assert args.mode == "texture"
    assert args.steps == 1
    assert args.blur_type == "gaussian"
    assert args.monochromatic is False
    assert args.opacity == 0.4
    assert args.mask == "skin"
