#!/usr/bin/env python3
"""Конвертация Action между JSON и `.atn` через headless PhotoCraft MCP.

`.atn` — бинарный формат Photoshop; корректно собрать и разобрать его умеет только
PhotoCraft. Скрипт запускает `photocraft-cli mcp` (GUI не нужен) и использует
`actions.load/get/insert/save`.

  pack   <action.json> [--out <file.atn>] [--action NAME] [--force]
  unpack <file.atn>    [--out <file.action.json>] [--force]

`pack` пропускает шаги без Photoshop-эквивалента (`skipUnsupported`) и печатает их
список. `unpack` сохраняет `steps`, `dialogs`, `disabled`, `color` и метаданные загрузки.
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

logger = logging.getLogger("action.convert")

ATN_MAGIC = b"\x00\x00\x00\x10"
JOURNAL_SUFFIX = ".action.json"


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


def default_atn_path(source: Path) -> Path:
    name = source.name
    if name.endswith(JOURNAL_SUFFIX):
        return source.with_name(name[: -len(JOURNAL_SUFFIX)] + ".atn")
    return source.with_suffix(".atn")


def default_json_path(source: Path) -> Path:
    return source.with_suffix(JOURNAL_SUFFIX)


def validate_atn(data: bytes) -> None:
    if len(data) < 8 or not data.startswith(ATN_MAGIC):
        raise ValueError("не похоже на .atn (Photoshop Actions, version 16)")


def load_actions(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: некорректный JSON ({exc})") from exc
    if isinstance(data, dict) and isinstance(data.get("actions"), list):
        raw = data["actions"]
    elif isinstance(data, dict) and isinstance(data.get("action"), dict):
        raw = [data["action"]]
    elif isinstance(data, dict) and isinstance(data.get("steps"), list):
        raw = [data]
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError(f"{path}: ожидался action.steps, actions[] или {JOURNAL_SUFFIX}")
    actions: list[dict[str, Any]] = []
    for index, action in enumerate(raw):
        if not isinstance(action, dict):
            raise ValueError(f"{path}: action[{index}] — не объект")
        name = action.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: action[{index}] без name")
        if not isinstance(action.get("steps"), list):
            raise ValueError(f"{path}: action[{index}] без steps")
        actions.append(action)
    if not actions:
        raise ValueError(f"{path}: нет ни одного Action")
    return actions


def pick_action(actions: list[dict[str, Any]], name: str | None) -> dict[str, Any]:
    if name is None:
        if len(actions) != 1:
            names = ", ".join(str(action["name"]) for action in actions)
            raise ValueError(f"в JSON несколько Action ({names}); выберите --action NAME")
        return actions[0]
    for action in actions:
        if action["name"] == name:
            return action
    names = ", ".join(str(action["name"]) for action in actions)
    raise ValueError(f"Action {name!r} не найден; доступны: {names}")


def ensure_writable(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"{path}: файл существует; используйте --force для перезаписи")
    path.parent.mkdir(parents=True, exist_ok=True)


async def pack_action(client: MCPClient, action: dict[str, Any], out_path: Path) -> None:
    name = action["name"]
    set_name = action.get("set") or None
    steps = action["steps"]
    try:
        await client.command_run("actions.delete", {"action": name})
    except McpError as exc:
        logger.debug("actions.delete: %s", exc)
    await client.command_run("actions.record", {"name": name})
    await client.command_run("actions.stop")
    for index, step in enumerate(steps):
        if not isinstance(step, list) or len(step) != 2:
            raise ValueError(f"шаг {index}: ожидался [id, params]")
        command_id, params = step
        await client.command_run(
            "actions.insert",
            {"action": name, "id": command_id, "params": params or {}, "at": index},
        )
    if set_name:
        await client.command_run("actions.assignSet", {"action": name, "set": set_name})
    for step_index in action.get("dialogs") or []:
        await client.command_run("actions.dialog", {"action": name, "step": step_index, "enabled": True})
    for step_index in action.get("disabled") or []:
        await client.command_run("actions.toggle", {"action": name, "step": step_index, "enabled": False})
    color = action.get("color")
    if color:
        await client.command_run("actions.setColor", {"action": name, "color": color})

    current = await client.command_run("actions.get", {"action": name})
    actual = current.get("steps") or []
    if len(actual) != len(steps):
        raise RuntimeError(f"после вставки {len(actual)} шагов вместо {len(steps)}")

    save_args: dict[str, Any] = {"action": name, "skipUnsupported": True}
    if set_name:
        save_args["set"] = set_name
    saved = await client.command_run("actions.save", save_args)
    if not isinstance(saved, dict) or "data" not in saved:
        raise RuntimeError(f"actions.save: неожиданный ответ {str(saved)[:200]}")
    data = base64.b64decode(saved["data"])
    out_path.write_bytes(data)
    skipped = saved.get("skipped") or []
    logger.info("готово: %s (%d байт, %s шагов)", out_path, len(data), saved.get("steps"))
    if skipped:
        logger.warning("пропущены шаги без Photoshop-эквивалента: %s", skipped)


async def unpack_action(client: MCPClient, data: bytes, out_path: Path) -> None:
    payload = base64.b64encode(data).decode()
    loaded = await client.command_run("actions.load", {"data": payload, "replace": True})
    names = loaded.get("actions") if isinstance(loaded, dict) else None
    if not names:
        raise RuntimeError(f"actions.load не вернул Action: {loaded!r}")
    actions: list[dict[str, Any]] = []
    for name in names:
        action = await client.command_run("actions.get", {"action": name})
        if not isinstance(action, dict):
            raise RuntimeError(f"actions.get({name!r}): неожиданный ответ {action!r}")
        actions.append(action)
    result: dict[str, Any] = {"action": actions[0], "loaded": loaded} if len(actions) == 1 else {
        "actions": actions,
        "loaded": loaded,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    steps = sum(len(action.get("steps") or []) for action in actions)
    logger.info("готово: %s (%d Action, %d шагов)", out_path, len(actions), steps)
    unsupported = loaded.get("unsupported") or []
    warnings = loaded.get("warnings") or []
    if unsupported:
        logger.warning("PhotoCraft не умеет играть шаги: %s", unsupported)
    if warnings:
        logger.warning("warnings при загрузке: %s", warnings)


async def run(args: argparse.Namespace) -> None:
    source = args.input.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"файл не найден: {source}")
    action: dict[str, Any] | None = None
    data = b""
    if args.command == "pack":
        action = pick_action(load_actions(source), args.action)
        out_path = (args.out or default_atn_path(source)).expanduser().resolve()
    else:
        data = source.read_bytes()
        validate_atn(data)
        out_path = (args.out or default_json_path(source)).expanduser().resolve()
    ensure_writable(out_path, args.force)

    client = MCPClient(resolve_cli(args.cli), bridge=False)
    try:
        await client.start()
        if action is not None:
            await pack_action(client, action, out_path)
        else:
            await unpack_action(client, data, out_path)
    finally:
        await client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=None, help="выходной файл")
    common.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    common.add_argument("--cli", default=None, help="путь к photocraft-cli")
    common.add_argument("--verbose", action="store_true")

    parser = argparse.ArgumentParser(description="Конвертировать Action: JSON ↔ .atn")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", parents=[common], help="JSON → .atn")
    pack.add_argument("input", type=Path, help="JSON с action.steps (журнал actions.get)")
    pack.add_argument("--action", default=None, help="имя Action, если в JSON их несколько")
    unpack = sub.add_parser("unpack", parents=[common], help=".atn → JSON")
    unpack.add_argument("input", type=Path, help="Photoshop Actions (.atn)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
