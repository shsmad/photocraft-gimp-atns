#!/usr/bin/env python3
"""PoC: Watermark & Resize (single) над текущим изображением.

Не batch-режим. Делает merged-копию текущего документа (`image.duplicate
{mergedOnly: true}`), оригинал со слоями не трогает, и в копии:

1. опционально Gaussian pre-blur;
2. resize по длинной стороне (`--size`) с bicubicSharper;
3. Unsharp Mask radius 2.5 / amount 20 / threshold 0;
4. опциональный контрастный слой: duplicate → High Pass 1 → Linear Light 40%;
5. опционально watermark: `doc_place`, Down-Left, offsets 20/20, opacity 25%,
   blend Difference, без масштабирования.

В bridge-режиме результат — новый документ, не сохраняется. `--auto` открывает
`--input`, обрабатывает и сохраняет результат в `out/waterres-single/<stem>/`.

По умолчанию используется `bicubicSharper` — Photoshop-совместимый режим, поэтому
resize переносим в `.atn`. `file.placeEmbedded` в MCP-playback отключён, поэтому
watermark в скрипте идёт через `doc_place`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photocraft_atns.mcp_client import MCPClient, McpConnectionError, McpError, discover_cli  # noqa: E402

logger = logging.getLogger("waterres")

DEFAULT_SIZE = 1920
DEFAULT_WATERMARK = REPO_ROOT / "watermark_bw_100.png"
POSITIONS = ("up-left", "up-right", "down-left", "down-right")


def target_size(width: int, height: int, size: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("ширина и высота должны быть > 0")
    if size <= 0:
        raise ValueError("--size должен быть > 0")
    scale = size / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def watermark_origin(
    position: str, doc_w: int, doc_h: int, wm_w: int, wm_h: int, offset_x: int, offset_y: int,
) -> tuple[int, int]:
    if position not in POSITIONS:
        raise ValueError(f"неизвестная позиция: {position}")
    x = offset_x if "left" in position else doc_w - wm_w - offset_x
    y = offset_y if "up" in position else doc_h - wm_h - offset_y
    return round(x), round(y)


def cli_version(cli: Path) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            [str(cli), "--version"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


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


class WaterRes:
    def __init__(
        self,
        client: MCPClient,
        *,
        size: int,
        resize: bool,
        pre_blur: bool,
        blur_size: float,
        resample: str,
        unsharp_radius: float,
        unsharp_amount: float,
        threshold: float,
        final_contrast: bool,
        watermark: Path | None,
        position: str,
        offset_x: int,
        offset_y: int,
        wm_opacity: float,
        wm_blend: str,
        keep_partial: bool,
    ) -> None:
        self._client = client
        self._size = size
        self._resize = resize
        self._pre_blur = pre_blur
        self._blur_size = blur_size
        self._resample = resample
        self._unsharp_radius = unsharp_radius
        self._unsharp_amount = unsharp_amount
        self._threshold = threshold
        self._final_contrast = final_contrast
        self._watermark = watermark
        self._position = position
        self._offset_x = offset_x
        self._offset_y = offset_y
        self._wm_opacity = wm_opacity
        self._wm_blend = wm_blend
        self._keep_partial = keep_partial

    async def run(self, *, export_path: Path | None = None) -> dict[str, Any]:
        source = await self._client.doc_inspect()
        if not isinstance(source, dict) or not source.get("layers"):
            raise RuntimeError("нет открытого документа")
        source_name = str(source.get("name") or "document")
        source_w = int(source["width"])
        source_h = int(source["height"])

        created = await cmd(
            self._client, "image.duplicate",
            {"name": f"{Path(source_name).stem} waterres", "mergedOnly": True},
        )
        document = await self._client.doc_inspect()
        if not isinstance(document, dict):
            raise RuntimeError("doc_inspect после image.duplicate вернул не объект")
        document_name = str(document.get("name") or created.get("name") or "waterres")
        created_layers: list[int] = []
        try:
            target_w, target_h = target_size(source_w, source_h, self._size) if self._resize else (source_w, source_h)
            if self._resize:
                if self._pre_blur:
                    await cmd(self._client, "filter.blur.gaussianBlur", {"radius": self._blur_size})
                await cmd(
                    self._client, "image.imageSize",
                    {"width": target_w, "height": target_h, "resample": self._resample},
                )
                await cmd(
                    self._client, "filter.sharpen.unsharpMask",
                    {"amount": self._unsharp_amount, "radius": self._unsharp_radius,
                     "threshold": self._threshold},
                )
                if self._final_contrast:
                    duplicate = await cmd(
                        self._client, "layer.duplicate", {"name": "Final contrast (HP 1 1)"},
                    )
                    layer_id = int(duplicate["layer"])
                    created_layers.append(layer_id)
                    await cmd(self._client, "filter.other.highPass", {"radius": 1})
                    await cmd(
                        self._client, "layer.setProps",
                        {"layer": layer_id, "blend": "Linear Light", "opacity": 0.4},
                    )

            if self._watermark is not None:
                await self._place_watermark(target_w, target_h)

            document = await self._client.doc_inspect()
            if export_path is not None:
                export_path.parent.mkdir(parents=True, exist_ok=True)
                await self._client.doc_save(str(export_path), format="png")
        except BaseException:
            if not self._keep_partial:
                logger.warning("обработка прервана; новый документ %r оставлен для отладки", document_name)
            raise

        manifest = {
            "source": source_name,
            "document": document_name,
            "source_size": [source_w, source_h],
            "size": self._size,
            "resample": self._resample if self._resize else None,
            "resized": [target_w, target_h] if self._resize else None,
            "pre_blur": {"radius": self._blur_size} if self._resize and self._pre_blur else None,
            "unsharp": (
                {"radius": self._unsharp_radius, "amount": self._unsharp_amount,
                 "threshold": self._threshold}
                if self._resize else None
            ),
            "final_contrast": (
                {"name": "Final contrast (HP 1 1)", "high_pass": 1, "blend": "Linear Light", "opacity": 0.4}
                if self._resize and self._final_contrast else None
            ),
            "watermark": (
                {"path": str(self._watermark), "position": self._position,
                 "offsets": [self._offset_x, self._offset_y], "opacity": self._wm_opacity,
                 "blend": self._wm_blend}
                if self._watermark is not None else None
            ),
            "result_size": [int(document["width"]), int(document["height"])],
            "layers": [
                {"name": layer.get("name"), "kind": layer.get("kind"),
                 "blend": layer.get("blend"), "opacity": layer.get("opacity"),
                 "bounds": layer.get("bounds")}
                for layer in document.get("layers") or [] if isinstance(layer, dict)
            ],
            "created_layer_ids": created_layers,
            "photocraft_cli": "See command log",
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        return manifest

    async def _place_watermark(self, doc_w: int, doc_h: int) -> None:
        if self._watermark is None:
            return
        placed = await self._client.tool("doc_place", {"path": str(self._watermark), "scale": 100})
        if not isinstance(placed, dict) or "layer" not in placed:
            raise RuntimeError(f"doc_place: неожиданный ответ {placed!r}")
        layer_id = int(placed["layer"])
        bounds = placed.get("bounds") or [0, 0, 0, 0]
        x0, y0, x1, y1 = (float(value) for value in bounds)
        wm_w = round(x1 - x0)
        wm_h = round(y1 - y0)
        desired_x, desired_y = watermark_origin(
            self._position, doc_w, doc_h, wm_w, wm_h, self._offset_x, self._offset_y,
        )
        await cmd(
            self._client, "layer.translate",
            {"layer": layer_id, "dx": round(desired_x - x0), "dy": round(desired_y - y0)},
        )
        await cmd(
            self._client, "layer.setProps",
            {"layer": layer_id, "blend": self._wm_blend, "opacity": self._wm_opacity},
        )


def make_waterres(args: argparse.Namespace, client: MCPClient) -> WaterRes:
    watermark = None
    if not args.no_watermark:
        watermark = args.watermark.expanduser().resolve()
        if not watermark.is_file():
            raise SystemExit(f"watermark не найден: {watermark}")
    return WaterRes(
        client,
        size=args.size,
        resize=not args.no_resize,
        pre_blur=args.pre_blur,
        blur_size=args.blur_size,
        resample=args.resample,
        unsharp_radius=args.unsharp_radius,
        unsharp_amount=args.unsharp_amount,
        threshold=args.threshold,
        final_contrast=not args.no_final_contrast,
        watermark=watermark,
        position=args.position,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        wm_opacity=args.wm_opacity / 100.0,
        wm_blend=args.wm_blend,
        keep_partial=args.keep_partial,
    )


async def run_auto(args: argparse.Namespace, cli: Path) -> dict[str, Any]:
    if args.input is None:
        raise SystemExit("--auto требует --input")
    source = args.input.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"--input: файл не найден: {source}")
    watermark = None if args.no_watermark else args.watermark.expanduser().resolve()
    if watermark is not None and not watermark.is_file():
        raise SystemExit(f"watermark не найден: {watermark}")
    if args.read_root is not None:
        read_root = args.read_root.expanduser().resolve()
    elif watermark is not None:
        read_root = Path(os.path.commonpath([str(source.parent), str(watermark.parent)]))
    else:
        read_root = source.parent
    if not source.is_relative_to(read_root):
        raise SystemExit(f"--input вне read-root: {source} не под {read_root}")

    out_root = args.out.expanduser().resolve()
    run_dir = out_root / source.stem
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.png"

    client = MCPClient(cli, bridge=False, read_root=read_root, write_root=out_root)
    try:
        await client.start()
        await client.doc_open(str(source))
        waterres = make_waterres(args, client)
        manifest = await waterres.run(export_path=result_path)
        await client.doc_close()
        await client.doc_close()
    finally:
        await client.close()

    manifest["source"] = str(source)
    manifest["result_path"] = str(result_path)
    manifest["photocraft_cli"] = str(cli)
    manifest["photocraft_version"] = cli_version(cli)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    logger.info("готово: %s", result_path)
    return manifest


async def run_bridge(args: argparse.Namespace, cli: Path) -> dict[str, Any]:
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
        waterres = make_waterres(args, client)
        manifest = await waterres.run(export_path=None)
    finally:
        await client.close()
    manifest["photocraft_cli"] = str(cli)
    manifest["photocraft_version"] = cli_version(cli)
    logger.info("готово: новый документ %r оставлен открытым (не сохранён)", manifest["document"])
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watermark & Resize (single): merged-копия текущего изображения",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--auto", action="store_true", help="headless: открыть --input и сохранить result.png")
    parser.add_argument("--input", type=Path, default=None, help="вход для --auto")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE, help="длинная сторона результата, px")
    parser.add_argument("--no-resize", action="store_true", help="не менять размер")
    parser.add_argument("--pre-blur", action="store_true", help="Gaussian blur перед resize")
    parser.add_argument("--blur-size", type=float, default=1.0, help="радиус pre-blur")
    parser.add_argument(
        "--resample",
        choices=("nearest", "bilinear", "bicubic", "bicubicSmoother", "bicubicSharper",
                 "bicubicAutomatic", "automatic", "preserveDetails"),
        default="bicubicSharper",
        help="Photoshop-совместимый resample для image.imageSize",
    )
    parser.add_argument("--unsharp-radius", type=float, default=2.5)
    parser.add_argument("--unsharp-amount", type=float, default=20.0, help="в процентах PhotoCraft")
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--no-final-contrast", action="store_true", help="не добавлять High Pass 40%%")
    parser.add_argument("--no-watermark", action="store_true")
    parser.add_argument("--watermark", type=Path, default=DEFAULT_WATERMARK)
    parser.add_argument("--position", choices=POSITIONS, default="down-left")
    parser.add_argument("--offset-x", type=int, default=20)
    parser.add_argument("--offset-y", type=int, default=20)
    parser.add_argument("--wm-opacity", type=float, default=25.0, help="opacity watermark, 0..100")
    parser.add_argument("--wm-blend", choices=("Difference", "Normal"), default="Difference")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "waterres-single")
    parser.add_argument("--read-root", type=Path, default=None)
    parser.add_argument("--cli", default=None, help="путь к photocraft-cli")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--keep-partial", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.no_resize and args.no_watermark:
        logger.error("нечего делать: и resize, и watermark отключены")
        return 2
    if args.size <= 0:
        logger.error("--size должен быть > 0")
        return 2
    if not 0.0 <= args.wm_opacity <= 100.0:
        logger.error("--wm-opacity должен быть в диапазоне 0..100")
        return 2
    try:
        cli = resolve_cli(args.cli)
        if args.auto:
            asyncio.run(run_auto(args, cli))
        else:
            asyncio.run(run_bridge(args, cli))
    except (McpError, McpConnectionError, RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
