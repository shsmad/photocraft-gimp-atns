"""Юнит-тесты Watermark & Resize single (без PhotoCraft)."""

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


waterres = _load("waterres_single_poc")


def test_target_size_keeps_long_edge() -> None:
    assert waterres.target_size(1010, 1190, 800) == (679, 800)
    assert waterres.target_size(1190, 1010, 800) == (800, 679)
    assert waterres.target_size(4000, 6000, 1920) == (1280, 1920)
    assert waterres.target_size(100, 100, 50) == (50, 50)


def test_watermark_origin_corners() -> None:
    assert waterres.watermark_origin("down-left", 800, 600, 100, 75, 20, 20) == (20, 505)
    assert waterres.watermark_origin("up-left", 800, 600, 100, 75, 20, 20) == (20, 20)
    assert waterres.watermark_origin("up-right", 800, 600, 100, 75, 20, 20) == (680, 20)
    assert waterres.watermark_origin("down-right", 800, 600, 100, 75, 20, 20) == (680, 505)


def test_parse_args_defaults() -> None:
    args = waterres.parse_args(["--input", "photo.jpg"])
    assert args.auto is False
    assert args.size == 1920
    assert args.no_resize is False
    assert args.pre_blur is False
    assert args.unsharp_radius == 2.5
    assert args.unsharp_amount == 20.0
    assert args.no_final_contrast is False
    assert args.no_watermark is False
    assert args.position == "down-left"
    assert args.offset_x == 20
    assert args.offset_y == 20
    assert args.wm_opacity == 25.0
    assert args.wm_blend == "Difference"


def test_parse_args_overrides() -> None:
    args = waterres.parse_args(
        ["--input", "photo.jpg", "--auto", "--no-resize", "--no-watermark",
         "--position", "up-right", "--wm-opacity", "40", "--wm-blend", "Normal"],
    )
    assert args.auto is True
    assert args.no_resize is True
    assert args.no_watermark is True
    assert args.position == "up-right"
    assert args.wm_opacity == 40.0
    assert args.wm_blend == "Normal"
