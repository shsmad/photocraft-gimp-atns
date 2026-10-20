#!/usr/bin/env python3
"""Записать Watermark & Resize Actions с фиксированной длинной стороной 1920.

Один Action не умеет «длинная сторона = 1920» для произвольных пропорций: Photoshop
Image Size хранит фиксированные ширину/высоту (или проценты). Поэтому создаются два
варианта:

- portrait 1280×1920 (2:3);
- landscape 1920×1280 (3:2).

Внутри: `layer.mergeVisible` (как single-режим плагина), `image.imageSize` с
`bicubicSharper`, Unsharp r2.5/20, High Pass 1 + Linear Light 40%, watermark
`watermark_bw_100.png` (Down-Left 20/20, 25%, Difference).

Watermark добавляется через `file.placeEmbedded` (экспортируется в `.atn`), затем
`layer.translate` в угол и `layer.setProps`. Путь к watermark в `.atn` абсолютный,
поэтому action локальный; MCP-playback шага `file.placeEmbedded` отключён.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photocraft_atns.mcp_client import MCPClient, McpConnectionError, McpError, discover_cli  # noqa: E402

logger = logging.getLogger("waterres.action")

ACTION_SET = "SHSMAD"
WATERMARK = REPO_ROOT / "watermark_bw_100.png"
WATERMARK_NAME = WATERMARK.stem
WM_WIDTH = 100
WM_HEIGHT = 75
OFFSET_X = 20
OFFSET_Y = 20
WM_OPACITY = 0.25
WM_BLEND = "Difference"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    stem: str
    width: int
    height: int

    @property
    def atn_filename(self) -> str:
        return f"{self.stem}.atn"

    @property
    def json_filename(self) -> str:
        return f"{self.stem}.action.json"

    @property
    def translate(self) -> tuple[int, int]:
        centered_x = (self.width - WM_WIDTH) / 2.0
        centered_y = (self.height - WM_HEIGHT) / 2.0
        desired_x = OFFSET_X
        desired_y = self.height - WM_HEIGHT - OFFSET_Y
        return round(desired_x - centered_x), round(desired_y - centered_y)


def action_specs() -> tuple[ActionSpec, ...]:
    return (
        ActionSpec("Watermark & Resize 1920 Portrait", "waterres-1920-portrait", 1280, 1920),
        ActionSpec("Watermark & Resize 1920 Landscape", "waterres-1920-landscape", 1920, 1280),
    )


def native_steps(spec: ActionSpec) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("layer.mergeVisible", {}),
        ("image.imageSize", {"width": spec.width, "height": spec.height, "resample": "bicubicSharper"}),
        ("filter.sharpen.unsharpMask", {"amount": 20, "radius": 2.5, "threshold": 0}),
        ("layer.duplicate", {"name": "Final contrast (HP 1 1)"}),
        ("filter.other.highPass", {"radius": 1}),
        ("layer.setProps", {"layer": "Final contrast (HP 1 1)", "blend": "Linear Light", "opacity": 0.4}),
    )


def inserted_steps(spec: ActionSpec) -> tuple[tuple[str, dict[str, Any]], ...]:
    dx, dy = spec.translate
    return (
        ("file.placeEmbedded", {"path": str(WATERMARK), "scale": 100}),
        ("layer.translate", {"layer": WATERMARK_NAME, "dx": dx, "dy": dy}),
        ("layer.setProps", {"layer": WATERMARK_NAME, "blend": WM_BLEND, "opacity": WM_OPACITY}),
    )


def resolve_cli(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
        raise SystemExit(f"--cli: файл не найден: {path}")
    from_env = os.environ.get("PHOTOCRAFT_CLI")
    if from_env:
        path = Path(from_env).expanduser()
        if path.is_file():
            return path
        raise SystemExit(f"PHOTOCRAFT_CLI: файл не найден: {path}")
    found = discover_cli()
    if found is None:
        raise SystemExit(
            "photocraft-cli не найден. Распакуйте AppImage и задайте PHOTOCRAFT_CLI:\n"
            "  ~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage --appimage-extract\n"
            "  export PHOTOCRAFT_CLI=$PWD/squashfs-root/usr/bin/photocraft-cli",
        )
    return found


async def cmd(client: MCPClient, command_id: str, params: dict[str, Any] | None = None) -> Any:
    return await client.command_run(command_id, params or {})


def walk(layers: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        found.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            found.extend(walk(children))
    return found


async def record_action(client: MCPClient, spec: ActionSpec) -> tuple[dict[str, Any], Any]:
    try:
        await cmd(client, "actions.delete", {"action": spec.name})
    except McpError as exc:
        logger.debug("actions.delete(%s): %s", spec.name, exc)
    await cmd(client, "actions.record", {"name": spec.name})
    for command_id, params in native_steps(spec):
        await cmd(client, command_id, params)
    stopped = await cmd(client, "actions.stop")
    for command_id, params in inserted_steps(spec):
        await cmd(client, "actions.insert", {"action": spec.name, "id": command_id, "params": params})
    await cmd(client, "actions.assignSet", {"action": spec.name, "set": ACTION_SET})
    action = await cmd(client, "actions.get", {"action": spec.name})
    if not isinstance(action, dict):
        raise RuntimeError(f"actions.get({spec.name!r}): неожиданный ответ {action!r}")
    steps = action.get("steps") or []
    expected = len(native_steps(spec)) + len(inserted_steps(spec))
    if len(steps) != expected:
        raise RuntimeError(f"{spec.name}: {len(steps)} шагов вместо {expected}")
    logger.info("%s: %d шагов", spec.name, len(steps))
    return action, stopped


async def verify_prefix(client: MCPClient, spec: ActionSpec) -> None:
    source_w, source_h = (400, 600) if spec.height > spec.width else (600, 400)
    await client.doc_new(width=source_w, height=source_h, background="white", name="playback")
    await cmd(client, "layer.new.layer", {"name": "Photo"})
    await cmd(client, "paint.stroke", {"points": [[source_w // 2, source_h // 2]], "size": 80, "color": "#cc3333"})
    played = await cmd(client, "actions.play", {"action": spec.name})
    failed = played.get("failed") if isinstance(played, dict) else None
    if not isinstance(failed, dict) or failed.get("id") != "file.placeEmbedded":
        raise RuntimeError(f"{spec.name}: ожидался останов на file.placeEmbedded, получили {played!r}")
    document = await client.doc_inspect()
    if not isinstance(document, dict):
        raise RuntimeError(f"{spec.name}: doc_inspect вернул не объект")
    if (document.get("width"), document.get("height")) != (spec.width, spec.height):
        raise RuntimeError(
            f"{spec.name}: размер {document.get('width')}x{document.get('height')}, "
            f"ждём {spec.width}x{spec.height}",
        )
    names = {layer.get("name") for layer in walk(document.get("layers") or [])}
    if "Final contrast (HP 1 1)" not in names:
        raise RuntimeError(f"{spec.name}: нет слоя Final contrast")
    logger.info("%s: prefix playback OK (%dx%d), watermark шаг ожидаемо не играет в MCP",
                spec.name, spec.width, spec.height)
    await client.doc_close()


async def save_atn(client: MCPClient, spec: ActionSpec, action: dict[str, Any], stopped: Any, out_root: Path) -> bytes:
    saved = await cmd(client, "actions.save", {"action": spec.name, "set": ACTION_SET, "skipUnsupported": True})
    if not isinstance(saved, dict) or "data" not in saved:
        raise RuntimeError(f"{spec.name}: actions.save вернул {str(saved)[:200]}")
    skipped = saved.get("skipped") or []
    if skipped:
        raise RuntimeError(f"{spec.name}: при экспорте .atn пропущены шаги: {skipped}")
    data = base64.b64decode(saved["data"])
    (out_root / spec.atn_filename).write_bytes(data)
    journal = {
        "action": action,
        "stopped": stopped,
        "saved": {key: value for key, value in saved.items() if key != "data"},
    }
    (out_root / spec.json_filename).write_text(
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    logger.info("%s: %s (%d байт), skipped=%s", spec.name, spec.atn_filename, len(data), skipped)
    return data


async def verify_atn(cli: Path, spec: ActionSpec, data: bytes) -> None:
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        loaded = await cmd(client, "actions.load", {"data": base64.b64encode(data).decode(), "replace": True})
        if not isinstance(loaded, dict):
            raise RuntimeError(f"{spec.name}: actions.load вернул не объект")
        if loaded.get("unsupported"):
            raise RuntimeError(f"{spec.name}: unsupported при загрузке .atn: {loaded['unsupported']}")
        names = loaded.get("actions") or []
        if not names:
            raise RuntimeError(f"{spec.name}: actions.load не вернул Action")
        action = await cmd(client, "actions.get", {"action": names[0]})
        steps = action.get("steps") if isinstance(action, dict) else None
        if not isinstance(steps, list) or len(steps) != len(native_steps(spec)) + len(inserted_steps(spec)):
            raise RuntimeError(f"{spec.name}: неверное число шагов после load")
        ids = [step[0] for step in steps if isinstance(step, list) and step]
        for expected in ("image.imageSize", "file.placeEmbedded", "layer.translate"):
            if expected not in ids:
                raise RuntimeError(f"{spec.name}: в .atn нет шага {expected}")
    finally:
        await client.close()


async def make_actions(cli: Path, out_root: Path) -> tuple[ActionSpec, ...]:
    specs = action_specs()
    out_root.mkdir(parents=True, exist_ok=True)
    data: dict[str, bytes] = {}
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        for spec in specs:
            await client.doc_new(width=64, height=64, background="white", name="action-scratch")
            await cmd(client, "layer.new.layer", {"name": "Photo"})
            await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})
            action, stopped = await record_action(client, spec)
            data[spec.stem] = await save_atn(client, spec, action, stopped, out_root)
            await verify_prefix(client, spec)
        await client.doc_close()
    finally:
        await client.close()

    for spec in specs:
        await verify_atn(cli, spec, data[spec.stem])
        logger.info("%s: roundtrip .atn OK", spec.name)
    return specs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать Watermark & Resize Actions 1920 (portrait/landscape)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "actions", help="куда писать артефакты")
    parser.add_argument("--cli", default=None, help="путь к photocraft-cli")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not WATERMARK.is_file():
        logger.error("watermark не найден: %s", WATERMARK)
        return 2
    try:
        specs = asyncio.run(make_actions(resolve_cli(args.cli), args.out.expanduser().resolve()))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    for spec in specs:
        logger.info("%s -> %s", spec.name, spec.atn_filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
