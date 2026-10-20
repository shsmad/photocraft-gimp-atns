#!/usr/bin/env python3
"""Записать Skin Smoothing Actions и экспортировать `.atn`.

Повторяет `SHSMAD_Process_skinsmooth.py`:
- группа `Сглаживание кожи` с копией `Оригинал`;
- слой `(a) Мягкость (Lighten Only, <type> <radius>)` с blur;
- опционально GB+HP: `НЧ (GB N)` + `ВЧ (HP N)` Linear Light.

По умолчанию создаются три переносимых Action: gaussian, median, selective
(radius 10, без GB+HP). `--blur-type` и `--gbhp` позволяют собрать нужный вариант.
`Lighten` и blur-фильтры экспортируются в `.atn` без потерь.
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

logger = logging.getLogger("skinsmooth.action")

ACTION_SET = "SHSMAD"
GROUP_NAME = "Сглаживание кожи"
BASE_NAME = "Оригинал"
BLUR_TYPES = ("gaussian", "median", "selective")
DEFAULT_RADIUS = 10.0
DEFAULT_MAX_DELTA = 0.2
DEFAULT_HP_RADIUS = 3.0
DEFAULT_HP_OPACITY = 50


def fmt(value: float) -> str:
    return f"{value:g}"


def soft_name(blur_type: str, radius: float) -> str:
    return f"(a) Мягкость (Lighten Only, {blur_type} {fmt(radius)})"


def low_name(radius: float) -> str:
    return f"НЧ (GB {fmt(radius)})"


def high_name(radius: float) -> str:
    return f"ВЧ (HP {fmt(radius)})"


def blur_threshold(max_delta: float) -> int:
    return max(2, min(255, round(max_delta * 255)))


def blur_step(blur_type: str, radius: float, max_delta: float) -> tuple[str, dict[str, Any]]:
    if blur_type == "gaussian":
        return ("filter.blur.gaussianBlur", {"radius": radius})
    if blur_type == "median":
        return ("filter.noise.median", {"radius": max(1, round(radius))})
    if blur_type == "selective":
        return ("filter.blur.surfaceBlur", {"radius": radius, "threshold": blur_threshold(max_delta)})
    raise ValueError(f"неизвестный blur_type: {blur_type}")


@dataclass(frozen=True)
class ActionSpec:
    name: str
    stem: str
    blur_type: str
    radius: float
    max_delta: float
    gbhp: bool
    hp_radius: float
    hp_opacity: int
    steps: tuple[tuple[str, dict[str, Any]], ...]
    dialogs: tuple[int, ...]

    @property
    def atn_filename(self) -> str:
        return f"{self.stem}.atn"

    @property
    def json_filename(self) -> str:
        return f"{self.stem}.action.json"

    @property
    def expected_children(self) -> tuple[str, ...]:
        soft = soft_name(self.blur_type, self.radius)
        if self.gbhp:
            return (soft, high_name(self.hp_radius), low_name(self.hp_radius), BASE_NAME)
        return (soft, BASE_NAME)


def build_steps(
    *, blur_type: str, radius: float, max_delta: float, gbhp: bool,
    hp_radius: float, hp_opacity: int,
) -> tuple[tuple[tuple[str, dict[str, Any]], ...], tuple[int, ...]]:
    steps: list[tuple[str, dict[str, Any]]] = [
        ("layer.duplicate", {"name": BASE_NAME}),
    ]
    soft = soft_name(blur_type, radius)
    steps.append(("layer.duplicate", {"layer": BASE_NAME, "name": soft}))
    blur_index = len(steps)
    steps.append(blur_step(blur_type, radius, max_delta))
    steps.append(("layer.setProps", {"layer": soft, "blend": "Lighten"}))
    dialogs = [blur_index]
    if gbhp:
        steps.extend(
            (
                ("layer.duplicate", {"layer": BASE_NAME, "name": high_name(hp_radius)}),
                ("filter.other.highPass", {"radius": hp_radius}),
                ("layer.setProps", {"layer": high_name(hp_radius), "blend": "Linear Light",
                                    "opacity": hp_opacity / 100.0}),
            ),
        )
        dialogs.append(len(steps) - 2)
        steps.extend(
            (
                ("layer.duplicate", {"layer": BASE_NAME, "name": low_name(hp_radius)}),
                ("filter.blur.gaussianBlur", {"radius": hp_radius}),
            ),
        )
        dialogs.append(len(steps) - 1)
    steps.append(("layer.select", {"layer": BASE_NAME}))
    if gbhp:
        steps.extend(
            (
                ("layer.select", {"layer": low_name(hp_radius), "mode": "add"}),
                ("layer.select", {"layer": high_name(hp_radius), "mode": "add"}),
            ),
        )
    steps.extend(
        (
            ("layer.select", {"layer": soft, "mode": "add"}),
            ("layer.groupLayers", {}),
            ("layer.setProps", {"name": GROUP_NAME}),
        ),
    )
    return tuple(steps), tuple(dialogs)


def action_specs(
    *, blur_types: tuple[str, ...], radius: float, max_delta: float, gbhp: bool,
    hp_radius: float, hp_opacity: int,
) -> tuple[ActionSpec, ...]:
    specs: list[ActionSpec] = []
    for blur_type in blur_types:
        title = blur_type.capitalize()
        name = f"Skin Smoothing ({title}{' GB+HP' if gbhp else ''})"
        stem = f"skinsmooth-{blur_type}{'-gbhp' if gbhp else ''}"
        steps, dialogs = build_steps(
            blur_type=blur_type, radius=radius, max_delta=max_delta, gbhp=gbhp,
            hp_radius=hp_radius, hp_opacity=hp_opacity,
        )
        specs.append(
            ActionSpec(
                name=name, stem=stem, blur_type=blur_type, radius=radius,
                max_delta=max_delta, gbhp=gbhp, hp_radius=hp_radius,
                hp_opacity=hp_opacity, steps=steps, dialogs=dialogs,
            ),
        )
    return tuple(specs)


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


async def verify_playback(client: MCPClient, spec: ActionSpec, action_name: str) -> None:
    await client.doc_new(width=128, height=128, background="white", name="playback")
    await cmd(client, "layer.new.layer", {"name": "extra"})
    await cmd(client, "layer.new.layer", {"name": "Photo"})
    await cmd(client, "paint.stroke", {"points": [[64, 64]], "size": 50, "color": "#cc3333"})
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
    group = next((layer for layer in layers if layer.get("name") == GROUP_NAME), None)
    if group is None:
        raise RuntimeError(f"{spec.name}: группа {GROUP_NAME!r} не создана")
    children = [child for child in group.get("children") or [] if isinstance(child, dict)]
    names = tuple(child.get("name") for child in children)
    if names != spec.expected_children:
        raise RuntimeError(f"{spec.name}: слои группы {names}, ждём {spec.expected_children}")
    soft = children[0]
    if soft.get("blend") != "Lighten":
        raise RuntimeError(f"{spec.name}: soft blend {soft.get('blend')!r}, ждём Lighten")
    if spec.gbhp:
        high = children[1]
        if high.get("blend") != "Linear Light":
            raise RuntimeError(f"{spec.name}: high blend {high.get('blend')!r}")
        if abs(float(high.get("opacity", 0.0)) - spec.hp_opacity / 100.0) > 1e-6:
            raise RuntimeError(f"{spec.name}: high opacity {high.get('opacity')}")
    await client.doc_close()


def write_journal(path: Path, action: dict[str, Any], stopped: Any, saved: dict[str, Any]) -> None:
    payload = {
        "action": action,
        "stopped": stopped,
        "saved": {key: value for key, value in saved.items() if key != "data"},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def save_atn(client: MCPClient, spec: ActionSpec, action: dict[str, Any], stopped: Any, out_root: Path) -> bytes:
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


async def make_actions(cli: Path, out_root: Path, specs: tuple[ActionSpec, ...]) -> tuple[ActionSpec, ...]:
    out_root.mkdir(parents=True, exist_ok=True)
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        await client.doc_new(width=64, height=64, background="white", name="action-scratch")
        await cmd(client, "layer.new.layer", {"name": "Photo"})
        await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})
        for spec in specs:
            action, stopped = await record_action(client, spec)
            await verify_playback(client, spec, spec.name)
            await save_atn(client, spec, action, stopped, out_root)
        await client.doc_close()
    finally:
        await client.close()

    for spec in specs:
        data = (out_root / spec.atn_filename).read_bytes()
        await verify_atn(cli, spec, data)
        logger.info("%s: roundtrip .atn OK", spec.name)
    return specs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать Skin Smoothing Actions и экспортировать .atn",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--blur-type", choices=("all", *BLUR_TYPES), default="all")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS)
    parser.add_argument("--max-delta", type=float, default=DEFAULT_MAX_DELTA,
                        help="selective: max color delta 0..1")
    parser.add_argument("--gbhp", action="store_true", help="добавить GB+HP split")
    parser.add_argument("--hp-radius", type=float, default=DEFAULT_HP_RADIUS)
    parser.add_argument("--hp-opacity", type=int, default=DEFAULT_HP_OPACITY, help="High Pass opacity, %%")
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
    if args.radius <= 0 or args.hp_radius <= 0:
        logger.error("радиусы должны быть > 0")
        return 2
    if not 0.0 <= args.max_delta <= 1.0:
        logger.error("--max-delta должен быть в диапазоне 0..1")
        return 2
    if not 0 <= args.hp_opacity <= 100:
        logger.error("--hp-opacity должен быть в диапазоне 0..100")
        return 2
    blur_types = BLUR_TYPES if args.blur_type == "all" else (args.blur_type,)
    specs = action_specs(
        blur_types=blur_types, radius=args.radius, max_delta=args.max_delta,
        gbhp=args.gbhp, hp_radius=args.hp_radius, hp_opacity=args.hp_opacity,
    )
    try:
        asyncio.run(make_actions(resolve_cli(args.cli), args.out.expanduser().resolve(), specs))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    for spec in specs:
        logger.info("%s -> %s", spec.name, spec.atn_filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
