#!/usr/bin/env python3
"""PoC: frequency separation (GB+HP) в PhotoCraft с интерактивными радиусами.

Строит стек как GIMP-плагин `freqautosplit` (полный паритет по слоям), но
разделение частот делает нативно: LOW = Gaussian Blur, HIGH = High Pass +
Linear Light. В интерактивном режиме радиусы подбираются в живых диалогах
PhotoCraft (bridge), в auto — через `command_run` (headless).

Запуск и требования — `scripts/freqautosplit_poc.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photocraft_atns.mcp_client import (  # noqa: E402
    MCPClient,
    McpConnectionError,
    McpError,
    discover_cli,
)

logger = logging.getLogger("freqsplit")

GROUP_NAME = "Ретушь"
DEFAULT_RADIUS = 10.0
DNB_LAYERS = ("DnB Face", "DnB Body", "DnB Global")
POLL_SECONDS = 0.2
FILTER_WAIT_SECONDS = 120.0


class UserCancelled(RuntimeError):
    """Пользователь закрыл диалог фильтра без применения."""


def fmt_radius(value: float) -> str:
    """Как в GIMP-плагине: f-string от double, т.е. 10.0, а не 10."""
    return str(float(value))


def layer_names(radius: float) -> tuple[str, str, str]:
    r = fmt_radius(radius)
    return f"НЧ (GB {r}) Оригинал", f"НЧ (GB {r})", "ВЧ"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cli_version(cli: Path) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            [str(cli), "--version"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class StackResult:
    radius: float
    hp_radius: float
    layer_ids: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""


class FreqSplit:
    def __init__(
        self,
        client: MCPClient,
        *,
        interactive: bool,
        radius: float,
        hp_radius: float | None,
        keep_partial: bool,
    ) -> None:
        self._client = client
        self._interactive = interactive
        self._radius = radius
        self._hp_radius = hp_radius
        self._keep_partial = keep_partial

    # --- публичный сценарий -------------------------------------------------
    async def run(self, *, export_native: bool, out_rel: str | None, asset_stem: str) -> StackResult:
        result = StackResult(
            radius=self._radius, hp_radius=self._hp_radius or self._radius, started_at=utc_now(),
        )
        doc = await self._doc()
        source_id = self._pick_source(doc)
        created: list[int] = []
        group_id: int | None = None
        try:
            low_orig, low, high = layer_names(self._radius)
            a = await self._duplicate(source_id, low_orig)
            created.append(a)

            self._radius = await self._apply_filter(
                a, "filter.blur.gaussianBlur", self._radius, "Gaussian Blur",
            )
            if self._hp_radius is None:
                self._hp_radius = self._radius
            low_orig, low, high = layer_names(self._radius)
            await self._cmd("layer.setProps", {"layer": a, "name": low_orig})

            b = await self._duplicate(a, low)
            created.append(b)

            c = await self._duplicate(source_id, high)
            created.append(c)
            self._hp_radius = await self._apply_filter(
                c, "filter.other.highPass", self._hp_radius, "High Pass",
            )

            group_id = (await self._cmd("layer.new.group", {"name": GROUP_NAME}))["layer"]
            await self._cmd("layer.moveTo", {"layer": a, "target": group_id, "position": "into"})
            await self._cmd("layer.moveTo", {"layer": b, "target": a, "position": "above"})
            await self._cmd("layer.moveTo", {"layer": c, "target": b, "position": "above"})

            await self._select(c)
            for name in DNB_LAYERS:
                response = await self._cmd(
                    "layer.new.layer", {"name": name, "mode": "Soft Light", "opacity": 0.5},
                )
                created.append(int(response["layer"]))

            await self._cmd("layer.setProps", {"layer": c, "blend": "Linear Light", "opacity": 0.5})
            await self._cmd("layer.setProps", {"layer": a, "locks": {"pixels": True}})

            result.radius = self._radius
            result.hp_radius = self._hp_radius
            result.layer_ids = {"low_orig": a, "low": b, "high": c, "group": group_id}

            if export_native:
                if out_rel is None:
                    raise RuntimeError("не задан каталог экспорта")
                await self._export(source_id, b, c, out_rel, asset_stem)
        except BaseException:
            if not self._keep_partial:
                await self._rollback(group_id, created)
            raise
        result.finished_at = utc_now()
        return result

    # --- документ и слои ----------------------------------------------------
    async def _doc(self) -> dict[str, Any]:
        document = await self._client.doc_inspect()
        if not isinstance(document, dict) or not document.get("layers"):
            raise RuntimeError("нет открытого документа: откройте изображение в PhotoCraft")
        return document

    @staticmethod
    def _walk(layers: list[Any]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            found.append(layer)
            children = layer.get("children")
            if isinstance(children, list):
                found.extend(FreqSplit._walk(children))
        return found

    def _pick_source(self, doc: dict[str, Any]) -> int:
        selected = doc.get("selectedLayers") or []
        if len(selected) != 1:
            raise RuntimeError(f"выберите ровно один слой (сейчас выбрано: {len(selected)})")
        target = selected[0]
        for layer in self._walk(doc.get("layers") or []):
            if layer.get("id") == target:
                if layer.get("kind") != "Pixel":
                    raise RuntimeError(f"слой «{layer.get('name')}» не пиксельный ({layer.get('kind')})")
                return int(target)
        raise RuntimeError("выбранный слой не найден в документе")

    async def _select(self, layer_id: int) -> None:
        await self._cmd("layer.select", {"layer": layer_id})

    async def _duplicate(self, layer_id: int, name: str) -> int:
        response = await self._cmd("layer.duplicate", {"layer": layer_id, "name": name})
        return int(response["layer"])

    async def _cmd(self, command_id: str, params: dict[str, Any] | None = None) -> Any:
        return await self._client.command_run(command_id, params or {})

    # --- фильтры ------------------------------------------------------------
    async def _apply_filter(self, layer_id: int, command: str, initial: float, title: str) -> float:
        await self._select(layer_id)
        if not self._interactive:
            await self._cmd(command, {"radius": float(initial)})
            return float(initial)
        return await self._dialog_radius(command, initial, title)

    async def _dialog_radius(self, command: str, initial: float, title: str) -> float:
        before = await self._doc()
        revision = before.get("revision")
        opened = await self._client.ui_menu_invoke(command)
        dialog_id = opened.get("dialog") if isinstance(opened, dict) else None
        if dialog_id is None:
            raise RuntimeError(f"{title}: не удалось открыть диалог ({opened!r})")
        try:
            await self._client.ui_dialog_set(dialog_id, "radius", float(initial))
        except McpError as exc:
            logger.debug("%s: не удалось выставить стартовый радиус: %s", title, exc)

        last = float(initial)
        deadline = time.monotonic() + FILTER_WAIT_SECONDS
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if time.monotonic() > deadline:
                raise RuntimeError(f"{title}: диалог не закрыт за {FILTER_WAIT_SECONDS:.0f} c")
            ui = await self._client.ui_inspect()
            dialogs = (ui.get("dialogs") or []) if isinstance(ui, dict) else []
            current = next(
                (d for d in dialogs if isinstance(d, dict) and d.get("id") == dialog_id), None,
            )
            if current is None:
                break
            value = (current.get("fields") or {}).get("radius")
            if isinstance(value, int | float):
                last = float(value)

        logger.info("%s: диалог закрыт, ждём применения (радиус ≈ %s)", title, fmt_radius(last))
        await self._wait_applied(revision, title)
        return last

    async def _wait_applied(self, revision: Any, title: str) -> None:
        deadline = time.monotonic() + FILTER_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            doc = await self._doc()
            if revision is not None and doc.get("revision") != revision:
                return
            ui = await self._client.ui_inspect()
            running = ((ui.get("jobs") or {}).get("running") or []) if isinstance(ui, dict) else []
            if not running:
                await asyncio.sleep(POLL_SECONDS)
                doc = await self._doc()
                if revision is not None and doc.get("revision") != revision:
                    return
                raise UserCancelled(f"{title}: диалог закрыт без применения (Cancel)")
        raise RuntimeError(f"{title}: фильтр не применился за {FILTER_WAIT_SECONDS:.0f} c")

    # --- экспорт и откат ----------------------------------------------------
    async def _export(self, source_id: int, low_id: int, high_id: int, out_rel: str, stem: str) -> None:
        await self._cmd("layer.setProps", {"layer": source_id, "visible": False})
        try:
            await self._client.doc_save(f"{out_rel}/composite.png", format="png")
        finally:
            await self._cmd("layer.setProps", {"layer": source_id, "visible": True})

        for label, layer_id in (("low", low_id), ("high", high_id)):
            await self._cmd(
                "layer.duplicate",
                {"layer": layer_id, "document": "new", "documentName": f"{stem}_{label}"},
            )
            await self._cmd("layer.setProps", {"blend": "Normal", "opacity": 1.0})
            await self._client.doc_save(f"{out_rel}/{label}.png", format="png")
            await self._client.doc_close()
            logger.info("экспорт: %s/%s.png", out_rel, label)

    async def _rollback(self, group_id: int | None, created: list[int]) -> None:
        try:
            if group_id is not None:
                await self._cmd("layer.delete", {"layer": group_id})
            doc = await self._doc()
            existing = {int(layer["id"]) for layer in self._walk(doc.get("layers") or [])}
            for layer_id in reversed(created):
                if layer_id not in existing:
                    continue
                try:
                    await self._cmd("layer.delete", {"layer": layer_id})
                except McpError as exc:
                    logger.debug("rollback: слой %s уже удалён: %s", layer_id, exc)
            logger.info("rollback: созданные слои удалены")
        except (McpError, McpConnectionError, RuntimeError) as exc:
            logger.warning("rollback не удался: %s", exc)


# --- CLI --------------------------------------------------------------------
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
    if found is not None:
        return found
    raise SystemExit(
        "photocraft-cli не найден.\n"
        "Распакуйте AppImage и задайте PHOTOCRAFT_CLI:\n"
        "  ~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage --appimage-extract\n"
        "  export PHOTOCRAFT_CLI=$PWD/squashfs-root/usr/bin/photocraft-cli",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="freqautosplit PoC: GB+HP стек с интерактивными радиусами",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", required=True, type=Path, help="исходное изображение")
    parser.add_argument("--auto", action="store_true", help="без диалогов (headless MCP)")
    parser.add_argument(
        "--radius", type=float, default=DEFAULT_RADIUS,
        help="радиус LOW (и high-pass по умолчанию)",
    )
    parser.add_argument(
        "--hp-radius", type=float, default=None,
        help="радиус high-pass (по умолчанию = выбранный радиус blur)",
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "freqsplit-poc", help="корень результатов")
    parser.add_argument(
        "--read-root", type=Path, default=None,
        help="read-root для auto (по умолчанию каталог --input)",
    )
    parser.add_argument("--cli", default=None, help="путь к photocraft-cli")
    parser.add_argument("--port", type=int, default=None, help="control-порт (иначе из .env)")
    parser.add_argument("--token-file", type=Path, default=None, help="токен (иначе из .env)")
    parser.add_argument("--keep-partial", action="store_true", help="не удалять слои при отмене/ошибке")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def run_auto(args: argparse.Namespace, cli: Path, out_root: Path, stem: str) -> StackResult:
    source = args.input.expanduser().resolve()
    read_root = (args.read_root or source.parent).expanduser().resolve()
    if not source.is_relative_to(read_root):
        raise SystemExit(f"--input вне read-root: {source} не под {read_root}")

    native_dir = out_root / stem / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    out_rel = f"{stem}/native"

    client = MCPClient(cli, bridge=False, read_root=read_root, write_root=out_root)
    try:
        await client.start()
        await client.doc_open(str(source))
        split = FreqSplit(
            client,
            interactive=False,
            radius=args.radius,
            hp_radius=args.hp_radius,
            keep_partial=args.keep_partial,
        )
        result = await split.run(export_native=True, out_rel=out_rel, asset_stem=stem)
        if not args.keep_partial:
            await client.doc_close()
        return result
    finally:
        await client.close()


async def run_interactive(args: argparse.Namespace, cli: Path, out_root: Path, stem: str) -> StackResult:
    port = args.port or int(os.environ.get("PHOTOCRAFT_CONTROL_PORT", "7878"))
    token = args.token_file or Path(
        os.environ.get("PHOTOCRAFT_CONTROL_TOKEN_FILE", "~/.config/photocraft/control.token"),
    ).expanduser()
    if not token.is_file():
        raise SystemExit(
            f"токен не найден: {token}\n"
            "Запустите PhotoCraft с --control (например, ../photocraft-midi/launch-photocraft-midi.sh)",
        )

    client = MCPClient(cli, port, token, bridge=True)
    try:
        await client.start()
    except McpConnectionError as exc:
        await client.close()
        raise SystemExit(
            f"bridge недоступен на 127.0.0.1:{port}: {exc}\n"
            "Запустите PhotoCraft с --control (например, ../photocraft-midi/launch-photocraft-midi.sh) "
            "и откройте изображение.",
        ) from exc
    try:
        split = FreqSplit(
            client,
            interactive=True,
            radius=args.radius,
            hp_radius=args.hp_radius,
            keep_partial=args.keep_partial,
        )
        return await split.run(export_native=False, out_rel=None, asset_stem=stem)
    finally:
        await client.close()


def write_manifest(
    *, args: argparse.Namespace, cli: Path, out_root: Path, stem: str, result: StackResult,
) -> Path:
    source = args.input.expanduser().resolve()
    run_dir = out_root / stem
    manifest = {
        "asset": str(source),
        "sha256": sha256_file(source),
        "mode": "auto" if args.auto else "interactive",
        "recipe": "gb+hp",
        "low": "gaussianBlur",
        "high": "highPass+LinearLight50 (Grain Merge equivalent)",
        "radius": result.radius,
        "hp_radius": result.hp_radius,
        "layers": {
            "group": GROUP_NAME,
            "low_orig": layer_names(result.radius)[0],
            "low": layer_names(result.radius)[1],
            "high": "ВЧ",
            "dnb": list(DNB_LAYERS),
        },
        "layer_ids": result.layer_ids,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "photocraft_cli": str(cli),
        "photocraft_version": cli_version(cli),
    }
    path = (
        run_dir / "native" / "manifest.json"
        if args.auto
        else run_dir / "manifest.interactive.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    source = args.input.expanduser()
    if not source.is_file():
        logger.error("вход не найден: %s", source)
        return 2
    if args.radius <= 0 or (args.hp_radius is not None and args.hp_radius <= 0):
        logger.error("радиусы должны быть > 0")
        return 2

    cli = resolve_cli(args.cli)
    out_root = args.out.expanduser().resolve()
    stem = source.stem
    logger.info("cli=%s, out=%s, mode=%s", cli, out_root, "auto" if args.auto else "interactive")

    try:
        if args.auto:
            result = asyncio.run(run_auto(args, cli, out_root, stem))
        else:
            result = asyncio.run(run_interactive(args, cli, out_root, stem))
    except UserCancelled as exc:
        logger.warning("%s", exc)
        return 1
    except (McpError, McpConnectionError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1

    manifest = write_manifest(args=args, cli=cli, out_root=out_root, stem=stem, result=result)
    logger.info(
        "готово: радиусы %s/%s, манифест %s",
        fmt_radius(result.radius), fmt_radius(result.hp_radius), manifest,
    )
    if args.auto:
        logger.info(
            "дальше: scripts/run_gimp_reference.sh %s %s",
            source, out_root / stem / "gimp",
        )
        logger.info("затем: .venv/bin/python scripts/compare_images.py --run-dir %s", out_root / stem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
