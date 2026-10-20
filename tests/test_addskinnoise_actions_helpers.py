"""Юнит-тесты генератора Add Skin Noise Actions (без PhotoCraft)."""

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


maker = _load("make_addskinnoise_actions")


def test_pore_portable_uses_highpass() -> None:
    steps = dict(maker.pore_steps("highpass"))
    assert "filter.other.highPass" in steps
    assert "filter.stylize.emboss" not in steps
    assert steps["filter.other.highPass"] == {"radius": 0.5}
    assert steps["filter.noise.addNoise"]["amount"] == 3


def test_pore_emboss_uses_emboss() -> None:
    steps = dict(maker.pore_steps("emboss"))
    assert "filter.stylize.emboss" in steps
    assert "filter.other.highPass" not in steps
    assert steps["filter.stylize.emboss"] == {"angle": 130, "height": 1, "amount": 70}


def test_texture_steps_fill_gray_and_stack_filters() -> None:
    steps = dict(maker.texture_steps())
    assert steps["layer.new.layer"]["mode"] == "Overlay"
    assert steps["edit.fill"] == {"contents": "gray"}
    assert steps["filter.noise.median"] == {"radius": 1}
    assert [step for step, _ in maker.texture_steps()].count("filter.noise.median") == 2


def test_action_specs_artifacts_and_dialogs() -> None:
    specs = {spec.name: spec for spec in maker.action_specs()}
    pore = specs[maker.PORE_NAME]
    texture = specs[maker.TEXTURE_NAME]
    emboss = specs[maker.PORE_EMBOSS_NAME]
    assert pore.atn_filename == "addskin-pores.atn"
    assert texture.atn_filename == "addskin-texture.atn"
    assert pore.dialogs == (2, 3, 4)
    assert texture.dialogs == (3, 4)
    assert pore.portable is True
    assert texture.portable is True
    assert emboss.portable is False
    assert emboss.json_filename == "addskin-pores-emboss.action.json"
    assert emboss.expected_filters[1] == "filter.stylize.emboss"
