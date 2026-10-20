"""Integration-тест Watermark & Resize single: нужен `PHOTOCRAFT_CLI`."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys

from pathlib import Path
from types import ModuleType

import pytest

from PIL import Image, ImageDraw

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


waterres = _load("waterres_single_poc")


def test_auto_resize_and_watermark(tmp_path: Path) -> None:
    assert CLI
    cli = Path(CLI).expanduser()
    assert cli.is_file(), f"PHOTOCRAFT_CLI: файл не найден: {cli}"

    source = tmp_path / "source.png"
    image = Image.new("RGB", (320, 240), "#808080")
    ImageDraw.Draw(image).rectangle((40, 40, 280, 200), fill="#cc8855")
    image.save(source)

    watermark = tmp_path / "mark.png"
    mark = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    ImageDraw.Draw(mark).rectangle((5, 5, 35, 25), fill=(255, 255, 255, 200))
    mark.save(watermark)

    out = tmp_path / "out"
    args = waterres.parse_args([
        "--auto", "--input", str(source), "--watermark", str(watermark),
        "--size", "200", "--out", str(out),
    ])
    manifest = asyncio.run(waterres.run_auto(args, cli))

    result = out / source.stem / "result.png"
    assert result.is_file()
    with Image.open(result) as opened:
        assert opened.size == (200, 150)

    payload = json.loads((out / source.stem / "manifest.json").read_text(encoding="utf-8"))
    assert payload["resized"] == [200, 150]
    assert payload["watermark"]["position"] == "down-left"
    assert payload["final_contrast"]["blend"] == "Linear Light"
    assert any(layer["kind"] == "Smart Object" for layer in payload["layers"])
    assert manifest["result_size"] == [200, 150]
