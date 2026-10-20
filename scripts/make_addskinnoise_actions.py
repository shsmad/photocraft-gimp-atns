#!/usr/bin/env python3
"""Записать Add Skin Noise Actions и экспортировать переносимые `.atn`.

Артефакты:
- `addskin-pores.atn` — pore-рецепт с High Pass 0.5 вместо Emboss (Emboss не
  сериализуется в `.atn`); Smart Object + smart filters, недеструктивно.
- `addskin-texture.atn` — texture-рецепт из `gimp.md`; серый слой, Smart Object,
  4 smart filters.
- `addskin-pores-emboss.action.json` — native Action с настоящим Emboss (только
  PhotoCraft; в `.atn` этот шаг пропускается).

Оба `.atn` проверяются playback'ом как нативные Actions и после roundtrip
`actions.save → actions.load → actions.play`.
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

logger = logging.getLogger("addskinnoise.action")

ACTION_SET = "SHSMAD"
LAYER_PORE = "Кожа: поры"
LAYER_TEXTURE = "Кожа: текстура"
PORE_NAME = "Add Skin Pores"
TEXTURE_NAME = "Add Skin Texture"
PORE_EMBOSS_NAME = "Add Skin Pores (Emboss)"
SCRATCH_NAME = "action-scratch"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    stem: str
    layer: str
    blend: str
    opacity: float
    steps: tuple[tuple[str, dict[str, Any]], ...]
    dialogs: tuple[int, ...]
    expected_filters: tuple[str, ...]
    portable: bool = True

    @property
    def atn_filename(self) -> str:
        return f"{self.stem}.atn"

    @property
    def json_filename(self) -> str:
        return f"{self.stem}.action.json"


def noise_params(amount: float, distribution: str) -> dict[str, Any]:
    return {"amount": amount, "distribution": distribution, "monochromatic": True, "seed": 0}


def pore_steps(relief: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    if relief == "highpass":
        relief_step: tuple[str, dict[str, Any]] = ("filter.other.highPass", {"radius": 0.5})
    elif relief == "emboss":
        relief_step = ("filter.stylize.emboss", {"angle": 130, "height": 1, "amount": 70})
    else:
        raise ValueError(f"неизвестный relief: {relief}")
    return (
        ("layer.duplicate", {"name": LAYER_PORE}),
        ("layer.smartObjects.convertToSmartObject", {"layer": LAYER_PORE}),
        ("filter.noise.addNoise", noise_params(3, "gaussian")),
        relief_step,
        ("filter.blur.gaussianBlur", {"radius": 0.3}),
        ("layer.setProps", {"layer": LAYER_PORE, "blend": "Overlay", "opacity": 0.5}),
    )


def texture_steps() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("layer.new.layer", {"name": LAYER_TEXTURE, "mode": "Overlay"}),
        ("edit.fill", {"contents": "gray"}),
        ("layer.smartObjects.convertToSmartObject", {"layer": LAYER_TEXTURE}),
        ("filter.noise.addNoise", noise_params(12, "uniform")),
        ("filter.noise.median", {"radius": 2}),
        ("filter.noise.addNoise", noise_params(6, "uniform")),
        ("filter.noise.median", {"radius": 1}),
        ("layer.setProps", {"layer": LAYER_TEXTURE, "blend": "Overlay", "opacity": 1.0}),
    )


def action_specs() -> tuple[ActionSpec, ...]:
    pore_expected = ("filter.noise.addNoise", "filter.other.highPass", "filter.blur.gaussianBlur")
    emboss_expected = ("filter.noise.addNoise", "filter.stylize.emboss", "filter.blur.gaussianBlur")
    texture_expected = (
        "filter.noise.addNoise", "filter.noise.median",
        "filter.noise.addNoise", "filter.noise.median",
    )
    return (
        ActionSpec(
            name=PORE_NAME, stem="addskin-pores", layer=LAYER_PORE,
            blend="Overlay", opacity=0.5, steps=pore_steps("highpass"),
            dialogs=(2, 3, 4), expected_filters=pore_expected, portable=True,
        ),
        ActionSpec(
            name=TEXTURE_NAME, stem="addskin-texture", layer=LAYER_TEXTURE,
            blend="Overlay", opacity=1.0, steps=texture_steps(),
            dialogs=(3, 4), expected_filters=texture_expected, portable=True,
        ),
        ActionSpec(
            name=PORE_EMBOSS_NAME, stem="addskin-pores-emboss", layer=LAYER_PORE,
            blend="Overlay", opacity=0.5, steps=pore_steps("emboss"),
            dialogs=(2, 3, 4), expected_filters=emboss_expected, portable=False,
        ),
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


async def record_action(client: MCPClient, spec: ActionSpec) -> tuple[dict[str, Any], Any]:
    try:
        await cmd(client, "actions.delete", {"action": spec.name})
    except McpError as exc:
        logger.debug("actions.delete(%s): %s", spec.name, exc)
    await cmd(client, "actions.record", {"name": spec.name})
    for command_id, params in spec.steps:
        await cmd(client, command_id, params)
    stopped = await cmd(client, "actions.stop")
    for index in spec.dialogs:
        await cmd(client, "actions.dialog", {"action": spec.name, "step": index, "enabled": True})
    await cmd(client, "actions.assignSet", {"action": spec.name, "set": ACTION_SET})
    action = await cmd(client, "actions.get", {"action": spec.name})
    if not isinstance(action, dict):
        raise RuntimeError(f"actions.get({spec.name!r}): неожиданный ответ {action!r}")
    steps = action.get("steps") or []
    if len(steps) != len(spec.steps):
        raise RuntimeError(f"{spec.name}: {len(steps)} шагов вместо {len(spec.steps)}")
    if tuple(action.get("dialogs") or []) != spec.dialogs:
        raise RuntimeError(f"{spec.name}: диалоги {action.get('dialogs')}, ждём {list(spec.dialogs)}")
    logger.info("%s: %d шагов, диалоги %s", spec.name, len(steps), action.get("dialogs"))
    return action, stopped


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


async def verify_playback(client: MCPClient, spec: ActionSpec, action_name: str) -> None:
    await client.doc_new(width=64, height=64, background="white", name="playback")
    await cmd(client, "layer.new.layer", {"name": "extra"})
    await cmd(client, "layer.new.layer", {"name": "Photo"})
    await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})
    await cmd(client, "layer.select", {"layer": "Photo"})
    played = await cmd(client, "actions.play", {"action": action_name})
    if not isinstance(played, dict) or played.get("failed"):
        raise RuntimeError(f"{spec.name}: playback не удался: {played!r}")

    document = await client.doc_inspect()
    if not isinstance(document, dict):
        raise RuntimeError(f"{spec.name}: doc_inspect вернул не объект")
    layers = walk(document.get("layers") or [])
    if not any(layer.get("name") == "Photo" for layer in layers):
        raise RuntimeError(f"{spec.name}: исходный слой Photo пропал")
    effect = next((layer for layer in layers if layer.get("name") == spec.layer), None)
    if effect is None:
        raise RuntimeError(f"{spec.name}: слой {spec.layer!r} не создан")
    if effect.get("kind") != "Smart Object":
        raise RuntimeError(f"{spec.name}: {spec.layer!r} не Smart Object ({effect.get('kind')})")
    if effect.get("blend") != spec.blend:
        raise RuntimeError(f"{spec.name}: blend {effect.get('blend')!r}, ждём {spec.blend!r}")
    if abs(float(effect.get("opacity", 0.0)) - spec.opacity) > 1e-6:
        raise RuntimeError(f"{spec.name}: opacity {effect.get('opacity')}, ждём {spec.opacity}")
    filters = effect.get("smartFilters") or []
    commands = tuple(item.get("command") for item in filters if isinstance(item, dict))
    if commands != spec.expected_filters:
        raise RuntimeError(f"{spec.name}: smart filters {commands}, ждём {spec.expected_filters}")
    await client.doc_close()


def write_journal(path: Path, action: dict[str, Any], stopped: Any, saved: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {
        "action": action,
        "stopped": stopped,
        "saved": {key: value for key, value in saved.items() if key != "data"} if saved else None,
    }
    if saved is None:
        payload["note"] = "filter.stylize.emboss has no .atn equivalent; native Action only"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def save_portable(
    client: MCPClient, spec: ActionSpec, action: dict[str, Any], stopped: Any, out_root: Path,
) -> bytes:
    saved = await cmd(client, "actions.save", {"action": spec.name, "set": ACTION_SET, "skipUnsupported": True})
    if not isinstance(saved, dict) or "data" not in saved:
        raise RuntimeError(f"{spec.name}: actions.save вернул {str(saved)[:200]}")
    skipped = saved.get("skipped") or []
    if skipped:
        raise RuntimeError(f"{spec.name}: при экспорте .atn пропущены шаги: {skipped}")
    data = base64.b64decode(saved["data"])
    atn_path = out_root / spec.atn_filename
    atn_path.write_bytes(data)
    write_journal(out_root / spec.json_filename, action, stopped, saved)
    logger.info("%s: %s (%d байт), skipped=%s", spec.name, atn_path, len(data), skipped)
    return data


def save_native(out_root: Path, spec: ActionSpec, action: dict[str, Any], stopped: Any) -> None:
    path = out_root / spec.json_filename
    write_journal(path, action, stopped, None)
    logger.info("%s: native Action JSON %s (без .atn)", spec.name, path)


async def verify_atn(cli: Path, spec: ActionSpec, data: bytes) -> None:
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        payload = base64.b64encode(data).decode()
        loaded = await cmd(client, "actions.load", {"data": payload, "replace": True})
        if not isinstance(loaded, dict):
            raise RuntimeError(f"{spec.name}: actions.load вернул не объект")
        if loaded.get("unsupported"):
            raise RuntimeError(f"{spec.name}: unsupported при загрузке .atn: {loaded['unsupported']}")
        names = loaded.get("actions") or []
        if not names:
            raise RuntimeError(f"{spec.name}: actions.load не вернул Action")
        name = names[0]
        action = await cmd(client, "actions.get", {"action": name})
        if not isinstance(action, dict):
            raise RuntimeError(f"{spec.name}: actions.get после load вернул не объект")
        if len(action.get("steps") or []) != len(spec.steps):
            raise RuntimeError(f"{spec.name}: в .atn {len(action.get('steps') or [])} шагов")
        if tuple(action.get("dialogs") or []) != spec.dialogs:
            raise RuntimeError(f"{spec.name}: в .atn диалоги {action.get('dialogs')}")
        await verify_playback(client, spec, name)
    finally:
        await client.close()


async def make_actions(cli: Path, out_root: Path) -> list[ActionSpec]:
    specs = action_specs()
    out_root.mkdir(parents=True, exist_ok=True)
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        await client.doc_new(width=64, height=64, background="white", name=SCRATCH_NAME)
        await cmd(client, "layer.new.layer", {"name": "Photo"})
        await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})

        for spec in specs:
            action, stopped = await record_action(client, spec)
            await verify_playback(client, spec, spec.name)
            if spec.portable:
                await save_portable(client, spec, action, stopped, out_root)
            else:
                save_native(out_root, spec, action, stopped)
        await client.doc_close()
    finally:
        await client.close()

    for spec in specs:
        if not spec.portable:
            continue
        data = (out_root / spec.atn_filename).read_bytes()
        await verify_atn(cli, spec, data)
        logger.info("%s: roundtrip .atn OK", spec.name)
    return list(specs)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать Add Skin Noise Actions и экспортировать .atn",
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
    try:
        specs = asyncio.run(make_actions(resolve_cli(args.cli), args.out.expanduser().resolve()))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    for spec in specs:
        logger.info("%s -> %s", spec.name, spec.atn_filename if spec.portable else spec.json_filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
