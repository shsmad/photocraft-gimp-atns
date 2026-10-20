#!/usr/bin/env python3
"""Записать переносимый Eyes & Teeth Action и экспортировать `.atn`.

Рецепт: группа `Глаза и зубы` с тремя adjustment layers (`Белки`, `Зубы`, `Радужка`).
Каждый слой получает чёрную маску через `select.all → select.inverse` и правится
Hue/Saturation. Пользователь проявляет эффект белой кистью.

Этот способ, в отличие от `layer.layerMask.hideAll`, полностью сериализуется в `.atn`:
`select.*` и adjustment layer имеют Photoshop-эквиваленты. Метод плагина
`Desaturate + Brightness` пока не портирован — Action использует Hue/Saturation.
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

logger = logging.getLogger("eyesteeth.action")

ACTION_SET = "SHSMAD"
ACTION_NAME = "Eyes & Teeth"
GROUP_NAME = "Глаза и зубы"
TARGETS = (
    ("Белки", {"hue": 0, "saturation": -70, "lightness": 60}),
    ("Зубы", {"hue": 0, "saturation": -70, "lightness": 60}),
    ("Радужка", {"hue": 0, "saturation": 35, "lightness": 10}),
)
DIALOGS = (2, 6, 10)


@dataclass(frozen=True)
class ActionSpec:
    name: str
    group: str
    targets: tuple[tuple[str, dict[str, Any]], ...]
    dialogs: tuple[int, ...]
    steps: tuple[tuple[str, dict[str, Any]], ...]

    @property
    def expected_children(self) -> tuple[str, ...]:
        return tuple(name for name, _ in reversed(self.targets))


def action_steps(targets: tuple[tuple[str, dict[str, Any]], ...] = TARGETS) -> tuple[tuple[str, dict[str, Any]], ...]:
    steps: list[tuple[str, dict[str, Any]]] = []
    for name, params in targets:
        steps.extend(
            (
                ("select.all", {}),
                ("select.inverse", {}),
                ("layer.newAdjustmentLayer.hueSaturation", dict(params)),
                ("layer.setProps", {"name": name}),
            ),
        )
    steps.extend(
        (
            ("layer.select", {"layer": targets[0][0]}),
            ("layer.select", {"layer": targets[1][0], "mode": "add"}),
            ("layer.select", {"layer": targets[2][0], "mode": "add"}),
            ("layer.groupLayers", {}),
            ("layer.setProps", {"name": GROUP_NAME}),
            ("select.deselect", {}),
        ),
    )
    return tuple(steps)


def action_spec() -> ActionSpec:
    return ActionSpec(
        name=ACTION_NAME,
        group=GROUP_NAME,
        targets=TARGETS,
        dialogs=DIALOGS,
        steps=action_steps(),
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
    group = next((layer for layer in layers if layer.get("name") == spec.group), None)
    if group is None:
        raise RuntimeError(f"{spec.name}: группа {spec.group!r} не создана")
    if group.get("kind") != "Group":
        raise RuntimeError(f"{spec.name}: {spec.group!r} не группа ({group.get('kind')})")
    children = [child for child in group.get("children") or [] if isinstance(child, dict)]
    names = tuple(child.get("name") for child in children)
    if names != spec.expected_children:
        raise RuntimeError(f"{spec.name}: слои группы {names}, ждём {spec.expected_children}")
    for child in children:
        if child.get("kind") != "Adjustment":
            raise RuntimeError(f"{spec.name}: {child.get('name')!r} не Adjustment")
        if not child.get("hasMask"):
            raise RuntimeError(f"{spec.name}: у {child.get('name')!r} нет маски")
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
    atn_path = out_root / "eyesteeth.atn"
    atn_path.write_bytes(data)
    write_journal(out_root / "eyesteeth.action.json", action, stopped, saved)
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


async def make_action(cli: Path, out_root: Path) -> ActionSpec:
    spec = action_spec()
    out_root.mkdir(parents=True, exist_ok=True)
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        await client.doc_new(width=64, height=64, background="white", name="action-scratch")
        await cmd(client, "layer.new.layer", {"name": "Photo"})
        await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})
        action, stopped = await record_action(client, spec)
        await verify_playback(client, spec, spec.name)
        data = await save_atn(client, spec, action, stopped, out_root)
        await client.doc_close()
    finally:
        await client.close()
    await verify_atn(cli, spec, data)
    logger.info("%s: roundtrip .atn OK", spec.name)
    return spec


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать Eyes & Teeth Action и экспортировать .atn",
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
        spec = asyncio.run(make_action(resolve_cli(args.cli), args.out.expanduser().resolve()))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    logger.info("%s -> eyesteeth.atn + eyesteeth.action.json", spec.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
