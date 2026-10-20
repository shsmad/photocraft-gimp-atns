#!/usr/bin/env python3
"""PoC: Add Skin Noise в PhotoCraft — недеструктивные режимы `pore` и `texture`.

`pore` повторяет алгоритм «поры кожи»: дублирует активный слой, превращает копию в
Smart Object, добавляет Gaussian-шум, Emboss и лёгкий Gaussian Blur. Фильтры остаются
smart filters и редактируются через Layer → Smart Filter. Слой — Overlay, opacity 50 %.

`texture` повторяет рецепт из `gimp.md`: нейтральный серый слой в Overlay превращается
в Smart Object, на него накладываются шум и Median как smart filters.

Оба режима не меняют исходный слой; маска (`--mask black|skin`) скрывает эффект и
позволяет проявлять его кистью. `--auto` работает headless и экспортирует
`composite.png`, `composite_unmasked.png` (при маске) и отдельный слой.

Запуск и требования — `scripts/addskinnoise_poc.md`.
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

logger = logging.getLogger("addskinnoise")

POLL_SECONDS = 0.2
FILTER_WAIT_SECONDS = 120.0
BLUR_TITLES = {"gaussian": "Gaussian", "median": "Median"}
KINDS = {"pore": "pores", "texture": "texture"}
DEFAULT_AMOUNTS = {"pore": 3.0, "texture": 12.0}
DEFAULT_BLUR_SIZES = {"pore": 0.3, "texture": 2.0}
DEFAULT_STEPS = {"pore": 1, "texture": 2}
DEFAULT_BLENDS = {"pore": "Overlay", "texture": "Overlay"}
DEFAULT_OPACITIES = {"pore": 0.5, "texture": 1.0}
DEFAULT_DISTRIBUTIONS = {"pore": "gaussian", "texture": "uniform"}
DEFAULT_EMBOSS = {"angle": 130.0, "height": 1.0, "amount": 70.0}


class UserCancelled(RuntimeError):
    """Пользователь закрыл диалог фильтра без применения."""


def fmt_size(value: float) -> str:
    return str(float(value))


def layer_name(mode: str, blur_type: str, blur_size: float, emboss_height: float | None = None) -> str:
    if mode == "pore":
        return f"Кожа: поры (Emboss {fmt_size(emboss_height or DEFAULT_EMBOSS['height'])})"
    return f"Кожа: текстура ({BLUR_TITLES[blur_type]} {fmt_size(blur_size)})"


def resolve_amount(mode: str, explicit: float | None) -> float:
    return DEFAULT_AMOUNTS[mode] if explicit is None else explicit


def resolve_blur_type(mode: str, explicit: str | None) -> str:
    return explicit or ("gaussian" if mode == "pore" else "median")


def resolve_blur_size(mode: str, explicit: float | None) -> float:
    return DEFAULT_BLUR_SIZES[mode] if explicit is None else explicit


def resolve_steps(mode: str, explicit: int | None) -> int:
    return DEFAULT_STEPS[mode] if explicit is None else explicit


def resolve_blend(mode: str, explicit: str | None) -> str:
    return explicit or DEFAULT_BLENDS[mode]


def resolve_opacity(mode: str, explicit: float | None) -> float:
    return DEFAULT_OPACITIES[mode] if explicit is None else explicit


def resolve_distribution(mode: str, explicit: str | None) -> str:
    return explicit or DEFAULT_DISTRIBUTIONS[mode]


def second_value(value: float) -> float:
    return value / 2.0


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
class NoiseResult:
    mode: str
    amount: float
    blur_type: str
    blur_size: float
    steps: int
    distribution: str
    blend: str
    opacity: float
    mask: str
    emboss_angle: float | None
    emboss_height: float | None
    emboss_amount: float | None
    layer_id: int
    layer_name: str
    smart_filters: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class AddSkinNoise:
    def __init__(
        self,
        client: MCPClient,
        *,
        interactive: bool,
        mode: str,
        amount: float,
        blur_type: str,
        blur_size: float,
        steps: int,
        distribution: str,
        monochromatic: bool,
        seed: int,
        emboss_angle: float,
        emboss_height: float,
        emboss_amount: float,
        blend: str,
        opacity: float,
        mask: str,
        keep_partial: bool,
    ) -> None:
        self._client = client
        self._interactive = interactive
        self._mode = mode
        self._amount = amount
        self._blur_type = blur_type
        self._blur_size = blur_size
        self._steps = steps
        self._distribution = distribution
        self._monochromatic = monochromatic
        self._seed = seed
        self._emboss_angle = emboss_angle
        self._emboss_height = emboss_height
        self._emboss_amount = emboss_amount
        self._blend = blend
        self._opacity = opacity
        self._mask = mask
        self._keep_partial = keep_partial

    async def run(self, *, export_native: bool, out_rel: str | None, asset_stem: str) -> NoiseResult:
        result = NoiseResult(
            mode=self._mode,
            amount=self._amount,
            blur_type=self._blur_type,
            blur_size=self._blur_size,
            steps=self._steps,
            distribution=self._distribution,
            blend=self._blend,
            opacity=self._opacity,
            mask=self._mask,
            emboss_angle=self._emboss_angle if self._mode == "pore" else None,
            emboss_height=self._emboss_height if self._mode == "pore" else None,
            emboss_amount=self._emboss_amount if self._mode == "pore" else None,
            layer_id=0,
            layer_name="",
            started_at=utc_now(),
        )
        doc = await self._doc()
        source_id = self._pick_source(doc)
        created: list[int] = []
        try:
            name = layer_name(self._mode, self._blur_type, self._blur_size, self._emboss_height)
            layer_id = await self._create_layer(source_id, name)
            created.append(layer_id)
            layer_id = await self._as_smart_object(layer_id)
            created[-1] = layer_id

            await self._apply_filters(layer_id)
            result.layer_name = name
            await self._cmd(
                "layer.setProps",
                {"layer": layer_id, "name": name, "blend": self._blend, "opacity": self._opacity},
            )
            result.amount = self._amount
            result.blur_size = self._blur_size
            result.emboss_height = self._emboss_height if self._mode == "pore" else None
            result.layer_id = layer_id
            result.smart_filters = await self._smart_filters(layer_id)

            if export_native:
                if out_rel is None:
                    raise RuntimeError("не задан каталог экспорта")
                if self._mask != "none":
                    await self._client.doc_save(f"{out_rel}/composite_unmasked.png", format="png")
                await self._export_layer(layer_id, out_rel, asset_stem, KINDS[self._mode])

            if self._mask != "none":
                await self._apply_mask(layer_id)

            if export_native:
                if out_rel is None:
                    raise RuntimeError("не задан каталог экспорта")
                await self._client.doc_save(f"{out_rel}/composite.png", format="png")
        except BaseException:
            if not self._keep_partial:
                await self._rollback(created)
            raise
        result.finished_at = utc_now()
        return result

    async def _apply_filters(self, layer_id: int) -> None:
        self._amount = await self._apply_noise(layer_id, self._amount)
        if self._mode == "pore":
            values = await self._apply_emboss(layer_id)
            self._emboss_angle = float(values.get("angle", self._emboss_angle))
            self._emboss_height = float(values.get("height", self._emboss_height))
            self._emboss_amount = float(values.get("amount", self._emboss_amount))
        self._blur_size = await self._apply_blur(layer_id, self._blur_size)
        if self._mode == "texture" and self._steps > 1:
            await self._apply_noise_direct(layer_id, second_value(self._amount))
            await self._apply_blur_direct(layer_id, second_value(self._blur_size))

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
                found.extend(AddSkinNoise._walk(children))
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

    async def _create_layer(self, source_id: int, name: str) -> int:
        if self._mode == "pore":
            response = await self._cmd("layer.duplicate", {"layer": source_id, "name": name})
        else:
            response = await self._cmd(
                "layer.new.layer", {"name": name, "mode": "Overlay", "fillNeutral": True},
            )
        layer_id = int(response["layer"])
        await self._bring_to_front(layer_id)
        return layer_id

    async def _as_smart_object(self, layer_id: int) -> int:
        response = await self._cmd("layer.smartObjects.convertToSmartObject", {"layer": layer_id})
        new_id = int(response["layer"])
        if new_id != layer_id:
            logger.debug("smart object: слой %s -> %s", layer_id, new_id)
        return new_id

    async def _bring_to_front(self, layer_id: int) -> None:
        doc = await self._doc()
        top = (doc.get("layers") or [None])[0]
        if isinstance(top, dict) and top.get("id") == layer_id:
            return
        await self._cmd("layer.arrange.bringToFront", {"layer": layer_id})

    async def _smart_filters(self, layer_id: int) -> list[dict[str, Any]]:
        doc = await self._doc()
        for layer in self._walk(doc.get("layers") or []):
            if layer.get("id") == layer_id:
                return layer.get("smartFilters") or []
        return []

    async def _cmd(self, command_id: str, params: dict[str, Any] | None = None) -> Any:
        return await self._client.command_run(command_id, params or {})

    # --- фильтры ------------------------------------------------------------
    def _noise_params(self, amount: float) -> dict[str, Any]:
        return {
            "amount": float(amount),
            "distribution": self._distribution,
            "monochromatic": self._monochromatic,
            "seed": self._seed,
        }

    def _blur_command(self) -> str:
        return "filter.blur.gaussianBlur" if self._blur_type == "gaussian" else "filter.noise.median"

    async def _apply_noise(self, layer_id: int, amount: float) -> float:
        await self._select(layer_id)
        if not self._interactive:
            await self._cmd("filter.noise.addNoise", self._noise_params(amount))
            return float(amount)
        values = await self._dialog_fields(
            "filter.noise.addNoise",
            {"amount": float(amount), "distribution": self._distribution,
             "monochromatic": self._monochromatic, "seed": self._seed},
            ("amount",),
            "Add Noise",
        )
        return float(values["amount"])

    async def _apply_emboss(self, layer_id: int) -> dict[str, Any]:
        await self._select(layer_id)
        params = {
            "angle": float(self._emboss_angle),
            "height": float(self._emboss_height),
            "amount": float(self._emboss_amount),
        }
        if not self._interactive:
            await self._cmd("filter.stylize.emboss", params)
            return params
        return await self._dialog_fields("filter.stylize.emboss", params, ("angle", "height", "amount"), "Emboss")

    async def _apply_blur(self, layer_id: int, radius: float) -> float:
        await self._select(layer_id)
        if not self._interactive:
            await self._cmd(self._blur_command(), {"radius": float(radius)})
            return float(radius)
        values = await self._dialog_fields(
            self._blur_command(), {"radius": float(radius)}, ("radius",), BLUR_TITLES[self._blur_type],
        )
        return float(values["radius"])

    async def _apply_noise_direct(self, layer_id: int, amount: float) -> None:
        await self._select(layer_id)
        await self._cmd("filter.noise.addNoise", self._noise_params(amount))

    async def _apply_blur_direct(self, layer_id: int, radius: float) -> None:
        await self._select(layer_id)
        await self._cmd(self._blur_command(), {"radius": float(radius)})

    async def _select(self, layer_id: int) -> None:
        await self._cmd("layer.select", {"layer": layer_id})

    async def _dialog_fields(
        self, command: str, initial: dict[str, Any], numeric: tuple[str, ...], title: str,
    ) -> dict[str, Any]:
        before = await self._doc()
        revision = before.get("revision")
        opened = await self._client.ui_menu_invoke(command)
        dialog_id = opened.get("dialog") if isinstance(opened, dict) else None
        if dialog_id is None:
            raise RuntimeError(f"{title}: не удалось открыть диалог ({opened!r})")
        for key, value in initial.items():
            try:
                await self._client.ui_dialog_set(dialog_id, key, value)
            except McpError as exc:
                logger.debug("%s: не удалось выставить %s=%r: %s", title, key, value, exc)

        last = dict(initial)
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
            fields = current.get("fields") or {}
            for key in numeric:
                value = fields.get(key)
                if isinstance(value, int | float):
                    last[key] = value

        logger.info("%s: диалог закрыт, ждём применения (%s)", title, last)
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

    # --- маска, экспорт, откат ----------------------------------------------
    async def _apply_mask(self, layer_id: int) -> None:
        if self._mask == "black":
            await self._cmd("layer.layerMask.hideAll", {"layer": layer_id})
            return
        if self._mask != "skin":
            return
        await self._cmd("layer.layerMask.hideAll", {"layer": layer_id})
        selected = await self._cmd("select.colorRange", {"select": "skinTones"})
        if not (isinstance(selected, dict) and selected.get("selected")):
            logger.warning("skin-маска: кожа не выделена, маска осталась чёрной")
            return
        await self._cmd("layer.layerMask.revealSelection", {"layer": layer_id})
        doc = await self._doc()
        if doc.get("hasSelection"):
            try:
                await self._cmd("select.deselect")
            except McpError as exc:
                logger.debug("select.deselect: %s", exc)

    async def _export_layer(self, layer_id: int, out_rel: str, stem: str, kind: str) -> None:
        await self._cmd(
            "layer.duplicate",
            {"layer": layer_id, "document": "new", "documentName": f"{stem}_{kind}"},
        )
        await self._cmd("layer.setProps", {"blend": "Normal", "opacity": 1.0})
        await self._client.doc_save(f"{out_rel}/{kind}.png", format="png")
        await self._client.doc_close()
        logger.info("экспорт: %s/%s.png", out_rel, kind)

    async def _rollback(self, created: list[int]) -> None:
        try:
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
        description="Add Skin Noise PoC: pore (Google) или texture (gimp.md), оба на Smart Object",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", required=True, type=Path, help="исходное изображение")
    parser.add_argument("--mode", choices=("pore", "texture"), default="pore", help="режим PoC")
    parser.add_argument("--auto", action="store_true", help="без диалогов (headless MCP)")
    parser.add_argument("--amount", type=float, default=None, help="RGB Noise, %% (pore 3, texture 12)")
    parser.add_argument("--blur-type", choices=("gaussian", "median"), default=None,
                        help="тип blur (pore — gaussian, texture — median)")
    parser.add_argument("--blur-size", type=float, default=None, help="радиус blur (pore 0.3, texture 2)")
    parser.add_argument("--steps", type=int, choices=(1, 2), default=None, help="проходы (texture 2, pore 1)")
    parser.add_argument("--distribution", choices=("uniform", "gaussian"), default=None)
    parser.add_argument("--monochromatic", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--emboss-angle", type=float, default=None, help="pore: угол Emboss")
    parser.add_argument("--emboss-height", type=float, default=None, help="pore: высота Emboss")
    parser.add_argument("--emboss-amount", type=float, default=None, help="pore: сила Emboss")
    parser.add_argument("--blend", default=None, help="режим слоя (по умолчанию Overlay)")
    parser.add_argument("--opacity", type=float, default=None, help="opacity слоя 0..1 (pore 0.5)")
    parser.add_argument("--mask", choices=("none", "black", "skin"), default="none",
                        help="none | black (рисовать кистью) | skin (автовыделение кожи)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "addskinnoise-poc", help="корень результатов")
    parser.add_argument(
        "--read-root", type=Path, default=None, help="read-root для auto (по умолчанию каталог --input)",
    )
    parser.add_argument("--cli", default=None, help="путь к photocraft-cli")
    parser.add_argument("--port", type=int, default=None, help="control-порт (иначе из .env)")
    parser.add_argument("--token-file", type=Path, default=None, help="токен (иначе из .env)")
    parser.add_argument("--keep-partial", action="store_true", help="не удалять слои при отмене/ошибке")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def make_noise(args: argparse.Namespace, client: MCPClient, *, interactive: bool) -> AddSkinNoise:
    return AddSkinNoise(
        client,
        interactive=interactive,
        mode=args.mode,
        amount=resolve_amount(args.mode, args.amount),
        blur_type=resolve_blur_type(args.mode, args.blur_type),
        blur_size=resolve_blur_size(args.mode, args.blur_size),
        steps=resolve_steps(args.mode, args.steps),
        distribution=resolve_distribution(args.mode, args.distribution),
        monochromatic=args.monochromatic,
        seed=args.seed,
        emboss_angle=DEFAULT_EMBOSS["angle"] if args.emboss_angle is None else args.emboss_angle,
        emboss_height=DEFAULT_EMBOSS["height"] if args.emboss_height is None else args.emboss_height,
        emboss_amount=DEFAULT_EMBOSS["amount"] if args.emboss_amount is None else args.emboss_amount,
        blend=resolve_blend(args.mode, args.blend),
        opacity=resolve_opacity(args.mode, args.opacity),
        mask=args.mask,
        keep_partial=args.keep_partial,
    )


async def run_auto(args: argparse.Namespace, cli: Path, out_root: Path, stem: str) -> NoiseResult:
    source = args.input.expanduser().resolve()
    read_root = (args.read_root or source.parent).expanduser().resolve()
    if not source.is_relative_to(read_root):
        raise SystemExit(f"--input вне read-root: {source} не под {read_root}")

    native_dir = out_root / stem / args.mode / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    out_rel = f"{stem}/{args.mode}/native"

    client = MCPClient(cli, bridge=False, read_root=read_root, write_root=out_root)
    try:
        await client.start()
        await client.doc_open(str(source))
        noise = make_noise(args, client, interactive=False)
        result = await noise.run(export_native=True, out_rel=out_rel, asset_stem=stem)
        if not args.keep_partial:
            await client.doc_close()
        return result
    finally:
        await client.close()


async def run_interactive(args: argparse.Namespace, cli: Path, out_root: Path, stem: str) -> NoiseResult:
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
            "Запустите PhotoCraft с --control и откройте изображение.",
        ) from exc
    try:
        noise = make_noise(args, client, interactive=True)
        return await noise.run(export_native=False, out_rel=None, asset_stem=stem)
    finally:
        await client.close()


def write_manifest(
    *, args: argparse.Namespace, cli: Path, out_root: Path, stem: str, result: NoiseResult,
) -> Path:
    source = args.input.expanduser().resolve()
    run_dir = out_root / stem / args.mode
    manifest = {
        "asset": str(source),
        "sha256": sha256_file(source),
        "mode": "auto" if args.auto else "interactive",
        "recipe": result.mode,
        "amount": result.amount,
        "second_amount": second_value(result.amount) if result.mode == "texture" and result.steps > 1 else None,
        "blur_type": result.blur_type,
        "blur_size": result.blur_size,
        "second_blur_size": (
            second_value(result.blur_size) if result.mode == "texture" and result.steps > 1 else None
        ),
        "steps": result.steps,
        "distribution": result.distribution,
        "monochromatic": args.monochromatic,
        "seed": args.seed,
        "emboss": (
            {"angle": result.emboss_angle, "height": result.emboss_height, "amount": result.emboss_amount}
            if result.mode == "pore" else None
        ),
        "blend": result.blend,
        "opacity": result.opacity,
        "mask": result.mask,
        "layer": result.layer_name,
        "layer_id": result.layer_id,
        "smart_filters": result.smart_filters,
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
    amount = resolve_amount(args.mode, args.amount)
    if not 1.0 <= amount <= 100.0:
        logger.error("--amount должен быть в диапазоне 1..100")
        return 2
    blur_size = resolve_blur_size(args.mode, args.blur_size)
    if blur_size <= 0:
        logger.error("--blur-size должен быть > 0")
        return 2
    steps = resolve_steps(args.mode, args.steps)
    if args.mode == "pore" and steps != 1:
        logger.error("pore поддерживает только --steps 1")
        return 2
    opacity = resolve_opacity(args.mode, args.opacity)
    if not 0.0 <= opacity <= 1.0:
        logger.error("--opacity должен быть в диапазоне 0..1")
        return 2
    if args.mode == "texture" and any(
        value is not None for value in (args.emboss_angle, args.emboss_height, args.emboss_amount)
    ):
        logger.error("--emboss-* доступны только для --mode pore")
        return 2

    cli = resolve_cli(args.cli)
    out_root = args.out.expanduser().resolve()
    stem = source.stem
    logger.info(
        "cli=%s, out=%s, run=%s, mode=%s",
        cli, out_root, "auto" if args.auto else "interactive", args.mode,
    )

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
        "готово: %s, amount=%s, %s=%s, emboss=%s, mask=%s, smart filters %d, манифест %s",
        result.layer_name, result.amount, result.blur_type, result.blur_size,
        result.emboss_height if result.mode == "pore" else "нет", result.mask,
        len(result.smart_filters), manifest,
    )
    if args.auto:
        logger.info("результаты: %s", out_root / stem / args.mode / "native")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
