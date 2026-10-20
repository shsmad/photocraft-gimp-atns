"""Юнит-тесты Watermark & Resize 1920 Actions (без PhotoCraft)."""

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


maker = _load("make_waterres_actions")


def test_specs_cover_portrait_and_landscape() -> None:
    specs = maker.action_specs()
    assert [spec.atn_filename for spec in specs] == [
        "waterres-1920-portrait.atn",
        "waterres-1920-landscape.atn",
    ]
    assert (specs[0].width, specs[0].height) == (1280, 1920)
    assert (specs[1].width, specs[1].height) == (1920, 1280)


def test_native_steps_use_bicubic_sharper() -> None:
    spec = maker.action_specs()[0]
    steps = dict(maker.native_steps(spec))
    assert steps["image.imageSize"] == {
        "width": 1280, "height": 1920, "resample": "bicubicSharper",
    }
    assert steps["filter.sharpen.unsharpMask"] == {"amount": 20, "radius": 2.5, "threshold": 0}
    assert steps["filter.other.highPass"] == {"radius": 1}
    assert len(maker.native_steps(spec)) == 6


def test_watermark_translation_and_steps() -> None:
    portrait, landscape = maker.action_specs()
    assert portrait.translate == (-570, 902)
    assert landscape.translate == (-890, 582)
    steps = maker.inserted_steps(portrait)
    assert steps[0][0] == "file.placeEmbedded"
    assert steps[0][1]["scale"] == 100
    assert steps[1] == ("layer.translate", {"layer": "watermark_bw_100", "dx": -570, "dy": 902})
    assert steps[2] == (
        "layer.setProps", {"layer": "watermark_bw_100", "blend": "Difference", "opacity": 0.25},
    )
