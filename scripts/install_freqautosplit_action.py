#!/usr/bin/env python3
"""Установить freqautosplit Action в запущенный PhotoCraft (bridge).

Два режима:
  --atn  <file>  — загрузить Photoshop Actions (actions.load, data/base64);
  --json <file>  — пересобрать нативный Action из шагов actions.get
                   (включая шаг locks.pixels, который теряется в .atn).

По умолчанию берётся `actions/freqautosplit.action.json`.
GUI должен быть запущен с control-портом (см. .env).
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

logger = logging.getLogger("freqsplit.install")

DEFAULT_JSON = REPO_ROOT / "actions" / "freqautosplit.action.json"
DEFAULT_ATN = REPO_ROOT / "actions" / "freqautosplit.atn"


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


def read_action(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    action = data.get("action", data) if isinstance(data, dict) else None
    if not isinstance(action, dict) or not isinstance(action.get("steps"), list):
        raise SystemExit(f"{path}: ожидался объект action.steps")
    return action


async def install_atn(client: MCPClient, path: Path) -> dict[str, Any]:
    payload = base64.b64encode(path.read_bytes()).decode()
    loaded = await client.command_run("actions.load", {"data": payload, "replace": True})
    logger.info("actions.load: %s", json.dumps(loaded, ensure_ascii=False)[:300])
    name = (loaded.get("actions") or [None])[0] if isinstance(loaded, dict) else None
    if not name:
        raise RuntimeError(f"actions.load не вернул имя действия: {loaded!r}")
    return await client.command_run("actions.get", {"action": name})


async def install_json(client: MCPClient, action: dict[str, Any]) -> dict[str, Any]:
    name = action.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("в JSON нет name действия")
    set_name = action.get("set") or "SHSMAD"
    try:
        await client.command_run("actions.delete", {"action": name})
    except McpError as exc:
        logger.debug("actions.delete: %s", exc)
    await client.command_run("actions.record", {"name": name})
    await client.command_run("actions.stop")
    for index, step in enumerate(action["steps"]):
        if not isinstance(step, list) or len(step) != 2:
            raise RuntimeError(f"шаг {index}: ожидался [id, params]")
        command_id, params = step
        await client.command_run(
            "actions.insert",
            {"action": name, "id": command_id, "params": params or {}},
        )
    await client.command_run("actions.assignSet", {"action": name, "set": set_name})
    for step_index in action.get("dialogs") or []:
        await client.command_run("actions.dialog", {"action": name, "step": step_index, "enabled": True})
    return await client.command_run("actions.get", {"action": name})


def describe(action: dict[str, Any]) -> str:
    steps = action.get("steps") or []
    dialogs = action.get("dialogs") or []
    return f"{action.get('set')}/{action.get('name')}: {len(steps)} шагов, диалоги {dialogs}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Установить freqautosplit Action в PhotoCraft")
    parser.add_argument(
        "--atn", nargs="?", const=DEFAULT_ATN, default=None, type=Path,
        help=f"загрузить .atn (без пути: {DEFAULT_ATN})",
    )
    parser.add_argument(
        "--json", nargs="?", const=DEFAULT_JSON, default=None, type=Path,
        help=f"пересобрать из JSON (по умолчанию {DEFAULT_JSON})",
    )
    parser.add_argument("--cli", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    port = args.port or int(os.environ.get("PHOTOCRAFT_CONTROL_PORT", "7878"))
    token = args.token_file or Path(
        os.environ.get("PHOTOCRAFT_CONTROL_TOKEN_FILE", "~/.config/photocraft/control.token"),
    ).expanduser()
    if not token.is_file():
        raise SystemExit(
            f"токен не найден: {token}\n"
            "Запустите PhotoCraft с --control (например, ../photocraft-midi/launch-photocraft-midi.sh)",
        )
    cli = resolve_cli(args.cli)
    client = MCPClient(cli, port, token, bridge=True)
    try:
        await client.start()
        if args.atn is not None and args.json is not None:
            raise SystemExit("выберите один режим: --atn или --json")
        if args.atn is not None:
            action = await install_atn(client, args.atn.expanduser().resolve())
        else:
            path = (args.json or DEFAULT_JSON).expanduser().resolve()
            action = await install_json(client, read_action(path))
        logger.info("установлено: %s", describe(action))
        logger.info("откройте Window → Actions (Alt+F9), выберите действие и нажмите Play")
    finally:
        await client.close()


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
