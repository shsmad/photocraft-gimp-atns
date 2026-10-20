"""Юнит-тесты чистых функций PoC freqautosplit (без PhotoCraft/GIMP)."""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path
from types import ModuleType

import numpy as np
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


freqsplit = _load("freqautosplit_poc")
compare = _load("compare_images")


def test_layer_names_match_gimp_float_format() -> None:
    assert freqsplit.layer_names(10.0) == (
        "НЧ (GB 10.0) Оригинал",
        "НЧ (GB 10.0)",
        "ВЧ",
    )
    assert freqsplit.layer_names(7) == ("НЧ (GB 7.0) Оригинал", "НЧ (GB 7.0)", "ВЧ")
    assert freqsplit.fmt_radius(7.5) == "7.5"


def test_metrics_identical_and_full_difference() -> None:
    zeros = np.zeros((2, 2, 4), dtype=np.float32)
    ones = np.ones((2, 2, 4), dtype=np.float32)

    same = compare.metrics(zeros, zeros)
    assert same["rmse"] == 0.0
    assert same["psnr"] is None
    assert same["max_abs"] == 0.0
    assert same["pct_gt_2_255"] == 0.0

    different = compare.metrics(zeros, ones)
    assert different["rmse"] == pytest.approx(1.0)
    assert different["psnr"] == pytest.approx(0.0)
    assert different["max_abs"] == pytest.approx(1.0)
    assert different["pct_gt_2_255"] == pytest.approx(1.0)


def test_metrics_rejects_size_mismatch() -> None:
    with pytest.raises(ValueError):
        compare.metrics(np.zeros((2, 2, 4), dtype=np.float32), np.zeros((3, 2, 4), dtype=np.float32))
