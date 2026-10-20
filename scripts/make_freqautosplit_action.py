#!/usr/bin/env python3
"""Собрать переносимый PhotoCraft Action и экспортировать его в `.atn`.

Действия:
1. В headless-сессии записывает последовательность freqautosplit (GB+HP) на
   скретч-документе. Все ссылки на слои — по именам, поэтому Action переносим
   между документами.
2. Включает диалоги у шагов Gaussian Blur / High Pass (`actions.dialog`).
3. Проверяет воспроизведение на новом документе (другой id/имя исходного слоя).
4. Сохраняет `.atn` (bytes, без path) и JSON шагов в `out/freqsplit-poc/`.

Требование: в документе выбран ровно один исходный слой; Action сам скопирует
его в `freq-src`, чтобы имя исходника было неважно.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys

from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photocraft_atns.mcp_client import MCPClient, McpConnectionError, McpError, discover_cli  # noqa: E402

logger = logging.getLogger("freqsplit.action")

ACTION_SET = "SHSMAD"
ACTION_NAME = "Frequency Split (GB+HP)"
DEFAULT_RADIUS = 10.0
GROUP_NAME = "Ретушь"
DNB_LAYERS = ("DnB Face", "DnB Body", "DnB Global")


def resolve_cli(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
        raise SystemExit(f"--cli: файл не найден: {path}")
    env = os.environ.get("PHOTOCRAFT_CLI")
    if env:
        path = Path(env).expanduser()
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


def names(radius: float) -> tuple[str, str, str]:
    r = str(float(radius))
    return f"НЧ (GB {r}) Оригинал", f"НЧ (GB {r})", "ВЧ"


async def cmd(client: MCPClient, command_id: str, params: dict[str, Any] | None = None) -> Any:
    return await client.command_run(command_id, params or {})


async def record_sequence(client: MCPClient, radius: float) -> dict[str, Any]:
    low_orig, low, high = names(radius)
    await cmd(client, "actions.record", {"name": ACTION_NAME})
    await cmd(client, "layer.duplicate", {"name": "freq-src"})
    await cmd(client, "layer.duplicate", {"layer": "freq-src", "name": low_orig})
    await cmd(client, "filter.blur.gaussianBlur", {"radius": radius})
    await cmd(client, "layer.duplicate", {"layer": low_orig, "name": low})
    await cmd(client, "layer.duplicate", {"layer": "freq-src", "name": high})
    await cmd(client, "layer.select", {"layer": high})
    await cmd(client, "layer.arrange.bringForward")
    await cmd(client, "layer.arrange.bringForward")
    await cmd(client, "filter.other.highPass", {"radius": radius})
    await cmd(client, "layer.setProps", {"layer": high, "blend": "Linear Light", "opacity": 0.5})
    await cmd(client, "layer.select", {"layer": high})
    for name in DNB_LAYERS:
        await cmd(client, "layer.new.layer", {"name": name, "mode": "Soft Light", "opacity": 0.5})
    await cmd(client, "layer.delete", {"layer": "freq-src"})
    for name in (low_orig, low, high, *DNB_LAYERS):
        await cmd(client, "layer.select", {"layer": name, "mode": "add"})
    await cmd(client, "layer.groupLayers")
    await cmd(client, "layer.setProps", {"name": GROUP_NAME})
    await cmd(client, "layer.setProps", {"layer": low_orig, "locks": {"pixels": True}})
    stopped = await cmd(client, "actions.stop")
    if not isinstance(stopped, dict):
        raise RuntimeError(f"actions.stop: неожиданный ответ {stopped!r}")
    return stopped


def find_step(steps: list[Any], command_id: str) -> int:
    for index, step in enumerate(steps):
        if isinstance(step, list) and len(step) == 2 and step[0] == command_id:
            return index
    raise RuntimeError(f"в Action нет шага {command_id}")


def tree(items: list[Any], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for layer in items:
        if not isinstance(layer, dict):
            continue
        lines.append(
            "  " * depth
            + f"{layer.get('name')!r} {layer.get('kind')} {layer.get('blend')} op={layer.get('opacity')}",
        )
        children = layer.get("children")
        if isinstance(children, list):
            lines.extend(tree(children, depth + 1))
    return lines


async def verify_playback(client: MCPClient, radius: float) -> None:
    await client.doc_new(width=64, height=64, background="white", name="playback")
    await cmd(client, "layer.new.layer", {"name": "extra"})
    await cmd(client, "layer.new.layer", {"name": "Photo"})
    await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})
    await cmd(client, "layer.select", {"layer": "Photo"})
    result = await cmd(client, "actions.play", {"action": ACTION_NAME})
    if not isinstance(result, dict) or result.get("failed"):
        raise RuntimeError(f"playback не удался: {result!r}")
    document = await client.doc_inspect()
    if not isinstance(document, dict):
        raise RuntimeError("doc_inspect: неожиданный ответ")
    logger.info("playback layers:\n%s", "\n".join(tree(document.get("layers") or [])))
    group = next(
        (
            layer
            for layer in document.get("layers") or []
            if isinstance(layer, dict) and layer.get("name") == GROUP_NAME
        ),
        None,
    )
    if group is None:
        raise RuntimeError("playback: группа «Ретушь» не создана")
    low_orig, low, high = names(radius)
    expected = [*reversed(DNB_LAYERS), high, low, low_orig]
    actual = [child.get("name") for child in group.get("children") or [] if isinstance(child, dict)]
    if actual != expected:
        raise RuntimeError(f"playback: неожиданный порядок слоёв {actual!r}, ждём {expected!r}")
    await client.doc_close()


async def make_action(cli: Path, out_root: Path, radius: float) -> None:
    client = MCPClient(cli, bridge=False)
    try:
        await client.start()
        await client.doc_new(width=64, height=64, background="white", name="action-scratch")
        await cmd(client, "layer.new.layer", {"name": "src"})
        await cmd(client, "paint.stroke", {"points": [[32, 32]], "size": 20, "color": "#cc3333"})

        stopped = await record_sequence(client, radius)
        action = await cmd(client, "actions.get", {"action": ACTION_NAME})
        if not isinstance(action, dict):
            raise RuntimeError(f"actions.get: неожиданный ответ {action!r}")
        steps = action.get("steps") or []
        blur_index = find_step(steps, "filter.blur.gaussianBlur")
        high_index = find_step(steps, "filter.other.highPass")
        for index in (blur_index, high_index):
            await cmd(client, "actions.dialog", {"action": ACTION_NAME, "step": index, "enabled": True})
        await cmd(client, "actions.assignSet", {"action": ACTION_NAME, "set": ACTION_SET})
        action = await cmd(client, "actions.get", {"action": ACTION_NAME})
        if not isinstance(action, dict):
            raise RuntimeError(f"actions.get после настройки: неожиданный ответ {action!r}")
        logger.info(
            "Action: %d шагов, диалоги %s, set=%s",
            len(action.get("steps") or []), action.get("dialogs"), action.get("set"),
        )

        await verify_playback(client, radius)

        save_args: dict[str, Any] = {"action": ACTION_NAME, "set": ACTION_SET}
        try:
            saved = await cmd(client, "actions.save", save_args)
        except McpError as exc:
            logger.warning("actions.save без skipUnsupported не удался: %s; пробуем skipUnsupported", exc)
            save_args["skipUnsupported"] = True
            saved = await cmd(client, "actions.save", save_args)
        if not isinstance(saved, dict) or "data" not in saved:
            raise RuntimeError(f"actions.save: неожиданный ответ {str(saved)[:200]}")
        data = base64.b64decode(saved["data"])
        out_root.mkdir(parents=True, exist_ok=True)
        atn_path = out_root / "freqautosplit.atn"
        atn_path.write_bytes(data)
        json_path = out_root / "freqautosplit.action.json"
        json_path.write_text(
            json.dumps({"action": action, "stopped": stopped, "saved": {k: v for k, v in saved.items() if k != "data"}},
                       ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        logger.info("готово: %s (%d байт), JSON: %s", atn_path, len(data), json_path)
        skipped = saved.get("skipped")
        if skipped:
            logger.warning("при экспорте .atn пропущены шаги: %s", skipped)
    finally:
        await client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать Action и .atn для freqautosplit")
    parser.add_argument("--cli", default=None, help="путь к photocraft-cli")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "actions", help="куда писать .atn")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS, help="радиус для записанных параметров/имён")
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
        asyncio.run(make_action(resolve_cli(args.cli), args.out.expanduser().resolve(), args.radius))
    except (McpError, McpConnectionError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
