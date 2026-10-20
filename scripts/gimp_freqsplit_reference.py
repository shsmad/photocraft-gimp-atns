"""GIMP reference payload for the freqautosplit PoC (run inside Python-Fu).

Запускается обёрткой `scripts/run_gimp_reference.sh` через
`gimp-console -i -n -s --batch-interpreter=python-fu-eval`.

Env:
  FREQ_INPUT      — исходное изображение
  FREQ_OUTDIR     — каталог для composite.png/low.png/high.png/manifest.json
  FREQ_RADIUS     — amount плагина (по умолчанию 10)
  FREQ_BLUR_TYPE  — 0 gaussian | 1 median (по умолчанию 0)
  FREQ_GIMP_VERSION — строка версии для манифеста (опционально)
"""

import json
import os

import gi  # ty: ignore[unresolved-import]

gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402  # ty: ignore[unresolved-import]


def pdb_call(name, args):
    proc = Gimp.get_pdb().lookup_procedure(name)
    config = proc.create_config()
    for key, value in args.items():
        config.set_property(key, value)
    return proc.run(config)


def store_png(image, drawable, path):
    _, x, y = drawable.get_offsets()
    width, height = drawable.get_width(), drawable.get_height()
    tmp = Gimp.Image.new(width, height, image.get_base_type())
    drawable.set_offsets(0, 0)
    layer = Gimp.Layer.new_from_drawable(drawable, tmp)
    Gimp.Layer.set_mode(layer, Gimp.LayerMode.NORMAL)
    tmp.insert_layer(layer, None, 0)
    drawable.set_offsets(x, y)
    pdb_call("file-png-export", {
        "run-mode": Gimp.RunMode.NONINTERACTIVE,
        "image": tmp,
        "file": Gio.File.new_for_path(path),
        "options": None,
        "interlaced": 0,
        "compression": 2,
        "bkgd": True,
        "offs": False,
        "phys": True,
        "time": True,
        "save-transparent": True,
    })
    tmp.delete()


def main():
    input_path = os.environ["FREQ_INPUT"]
    outdir = os.environ["FREQ_OUTDIR"]
    radius = float(os.environ.get("FREQ_RADIUS", "10"))
    blur_type = int(os.environ.get("FREQ_BLUR_TYPE", "0"))
    os.makedirs(outdir, exist_ok=True)

    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(input_path))
    selected = image.get_selected_layers()
    if len(selected) != 1:
        raise RuntimeError(f"ожидался один слой, выбрано: {len(selected)}")
    source = selected[0]

    proc = Gimp.get_pdb().lookup_procedure("plug-in-shsmad-process-freqautosplit")
    config = proc.create_config()
    config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    config.set_property("image", image)
    config.set_core_object_array("drawables", [source])
    config.set_property("createfreqsplit", True)
    config.set_property("amount", radius)
    config.set_property("blur-type", blur_type)
    config.set_property("creatednb", False)
    proc.run(config)

    group = next(layer for layer in image.get_layers() if layer.get_name() == "Ретушь")
    children = group.get_children()
    names = [layer.get_name() for layer in children]
    low = next(
        layer for layer in children
        if layer.get_name().startswith("НЧ (GB") and "Оригинал" not in layer.get_name()
    )
    high = next(layer for layer in children if layer.get_name() == "ВЧ")

    source.set_visible(False)
    try:
        composite = Gimp.Layer.new_from_visible(image, image, "composite")
        store_png(image, composite, os.path.join(outdir, "composite.png"))
    finally:
        source.set_visible(True)

    store_png(image, low, os.path.join(outdir, "low.png"))
    store_png(image, high, os.path.join(outdir, "high.png"))

    manifest = {
        "tool": "gimp",
        "gimp_version": os.environ.get("FREQ_GIMP_VERSION"),
        "plugin": "plug-in-shsmad-process-freqautosplit",
        "recipe": "grain",
        "createfreqsplit": True,
        "creatednb": False,
        "radius": radius,
        "blur_type": blur_type,
        "layers": names,
    }
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    image.delete()
    print("GIMP_REF_OK", names)  # noqa: T201


main()
