#!/usr/bin/env python3
"""Сравнение GIMP-эталона и native-результата freqautosplit.

Читает `<run-dir>/{gimp,native}/{composite,low,high}.png`, считает метрики,
пишет `diff/*.png`, `report.json` и `report.md`.

Запуск: `.venv/bin/python scripts/compare_images.py --run-dir out/freqsplit-poc/<asset> [--input <source>]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from PIL import Image

logger = logging.getLogger("freqsplit.compare")

PAIRS = ("low", "composite", "high")
EPS = 2.0 / 255.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0


def metrics(reference: np.ndarray, tested: np.ndarray) -> dict[str, Any]:
    if reference.shape != tested.shape:
        raise ValueError(
            f"размеры не совпадают: {reference.shape[1]}x{reference.shape[0]} vs "
            f"{tested.shape[1]}x{tested.shape[0]}",
        )
    delta = np.abs(reference - tested)
    rmse = float(np.sqrt(np.mean(delta**2)))
    psnr = None if rmse == 0.0 else float(20.0 * math.log10(1.0 / rmse))
    per_pixel = delta.max(axis=2)
    mask = (reference[..., 3] > 0.0) & (tested[..., 3] > 0.0)
    rgb_delta = delta[..., :3][mask]
    return {
        "rmse": rmse,
        "psnr": psnr,
        "max_abs": float(delta.max()),
        "mean_abs": float(delta.mean()),
        "pct_gt_2_255": float(np.mean(per_pixel > EPS)),
        "rmse_rgb_masked": float(np.sqrt(np.mean(rgb_delta**2))) if rgb_delta.size else None,
        "masked_pixels": int(mask.sum()),
        "pixels": int(reference.shape[0] * reference.shape[1]),
    }


def save_diff(reference: np.ndarray, tested: np.ndarray, path: Path, amplify: float) -> None:
    delta = np.clip(np.abs(reference - tested) * amplify, 0.0, 1.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((delta * 255.0).round().astype(np.uint8), "RGBA").save(path)


def read_manifest(directory: Path) -> dict[str, Any] | None:
    path = directory / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("не удалось прочитать %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def format_psnr(value: float | None) -> str:
    return "inf" if value is None else f"{value:.2f}"


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# freqautosplit diff: {report['asset']}",
        "",
        f"- input: `{report['input']}`",
        f"- sha256: `{report['input_sha256']}`",
        f"- diff amplify: x{report['amplify']}",
        "",
        "| pair | RMSE | PSNR | max abs | > 2/255 | RMSE RGB |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, data in report["pairs"].items():
        rmse_rgb = data.get("rmse_rgb_masked")
        lines.append(
            f"| {name} | {data['rmse']:.6f} | {format_psnr(data['psnr'])} | "
            f"{data['max_abs']:.6f} | {data['pct_gt_2_255'] * 100:.3f}% | "
            f"{'n/a' if rmse_rgb is None else f'{rmse_rgb:.6f}'} |",
        )
    lines += ["", "Diff images: `diff/<pair>.png`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Сравнить GIMP и native freqautosplit (метрики + diff)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--run-dir", required=True, type=Path, help="каталог ассета (gimp/ и native/)")
    parser.add_argument("--input", type=Path, default=None, help="исходник для метрик реконструкции")
    parser.add_argument("--out", type=Path, default=None, help="куда писать отчёты (по умолчанию run-dir)")
    parser.add_argument("--amplify", type=float, default=8.0, help="усиление diff-PNG")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_dir = args.run_dir.expanduser().resolve()
    out_dir = (args.out or run_dir).expanduser().resolve()
    gimp_dir = run_dir / "gimp"
    native_dir = run_dir / "native"

    try:
        pairs: dict[str, dict[str, Any]] = {}
        for name in PAIRS:
            gimp = load_rgba(gimp_dir / f"{name}.png")
            native = load_rgba(native_dir / f"{name}.png")
            pairs[name] = metrics(gimp, native)
            save_diff(gimp, native, out_dir / "diff" / f"{name}.png", args.amplify)
            logger.info("%s: RMSE %.6f", name, pairs[name]["rmse"])

        source_path = args.input.expanduser().resolve() if args.input is not None else None
        if source_path is not None:
            if not source_path.is_file():
                raise OSError(f"исходник не найден: {source_path}")
            source = load_rgba(source_path)
            for name, directory in (
                ("gimp_composite_vs_source", gimp_dir),
                ("native_composite_vs_source", native_dir),
            ):
                composite = load_rgba(directory / "composite.png")
                pairs[name] = metrics(source, composite)
                save_diff(source, composite, out_dir / "diff" / f"{name}.png", args.amplify)

        report: dict[str, Any] = {
            "run_dir": str(run_dir),
            "asset": run_dir.name,
            "input": str(source_path) if source_path is not None else None,
            "input_sha256": (
                sha256_file(source_path) if source_path is not None and source_path.is_file() else None
            ),
            "amplify": args.amplify,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "manifests": {
                "gimp": read_manifest(gimp_dir),
                "native": read_manifest(native_dir),
            },
            "pairs": pairs,
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        write_report_md(out_dir / "report.md", report)
        logger.info("отчёты: %s", out_dir / "report.md")
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
