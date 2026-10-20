# photocraft-gimp-atns

Автоматизация PhotoCraft **0.10**: перенос GIMP-плагина `freqautosplit` в PhotoCraft
(GB+HP), интерактивные радиусы, Action для панели Actions, экспорт `.atn`, эталон
из GIMP и попиксельное сравнение.

Это отдельный проект; MIDI-прослойка (`X-Touch Mini`) живёт в `../photocraft-midi`.

## Что внутри

- `scripts/freqautosplit_poc.py` — строит стек `Ретушь / НЧ / ВЧ / DnB` в PhotoCraft;
  интерактивные диалоги Blur и High Pass или `--auto` (headless).
- `scripts/make_freqautosplit_action.py` — записывает переносимый Action и экспортирует
  `actions/freqautosplit.atn` + `actions/freqautosplit.action.json`.
- `scripts/install_freqautosplit_action.py` — ставит Action в запущенный PhotoCraft
  (`.atn` или JSON).
- `scripts/convert_action.py` — конвертирует Action JSON ↔ `.atn` через headless
  PhotoCraft (без GUI).
- `scripts/addskinnoise_poc.py` — недеструктивный PoC Add Skin Noise: `pore`
  (алгоритм Google) и `texture` (рецепт из `gimp.md`), оба на Smart Object.
- `scripts/make_addskinnoise_actions.py` — записывает Add Skin Pores/Texture Actions,
  проверяет playback и экспортирует `.atn`.
- `scripts/make_eyesteeth_action.py` — записывает Eyes & Teeth Action с группой и
  adjustment layers, экспортирует `.atn`.
- `scripts/waterres_single_poc.py` — Watermark & Resize (single) над текущим
  изображением через MCP; `.atn` для resize невозможен.
- `scripts/make_skinsmooth_actions.py` — Skin Smoothing Actions (Gaussian/Median/
  Selective) и экспорт `.atn`.
- `scripts/gimp_freqsplit_reference.py` + `scripts/run_gimp_reference.sh` — эталон
  реальным GIMP-плагином `plug-in-shsmad-process-freqautosplit`.
- `scripts/compare_images.py` — RMSE/PSNR/max/доля `|Δ|>2/255`, diff-PNG и отчёты.

## Требования

- Python 3.14, [pdm](https://pdm-project.org/).
- PhotoCraft **0.10.0**; `photocraft-cli` — через `PHOTOCRAFT_CLI` или смонтированный
  AppImage (`/tmp/.mount_*/usr/bin/photocraft-cli`).
- Для интерактива: GUI запущен с control-портом и токеном (см. `.env`).
- Для эталона: GIMP **3.2** с установленным плагином `SHSMAD_Process_freqautosplit`.

```bash
pdm install
cp .env.example .env   # порт/токен; при необходимости PHOTOCRAFT_CLI
```

## Быстрый старт

Интерактив (GUI, диалоги с превью):

```bash
# запустите PhotoCraft с --control (например, ../photocraft-midi/launch-photocraft-midi.sh)
PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py --input /path/to/photo.jpg
```

Откроются **Gaussian Blur**, затем **High Pass**; OK применяет. Cancel откатывает
созданные слои.

Auto (headless, для чисел):

```bash
export PHOTOCRAFT_CLI=/path/to/squashfs-root/usr/bin/photocraft-cli
PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py --input /path/to/photo.jpg --auto --radius 10
```

Результаты: `out/freqsplit-poc/<asset>/native/{composite,low,high}.png` + `manifest.json`.

GIMP-эталон и сравнение:

```bash
scripts/run_gimp_reference.sh /path/to/photo.jpg out/freqsplit-poc/<asset>/gimp 10
.venv/bin/python scripts/compare_images.py \
  --run-dir out/freqsplit-poc/<asset> --input /path/to/photo.jpg
```

## Add Skin Noise PoC

```bash
# GUI: откройте изображение и выберите один пиксельный слой
.venv/bin/python scripts/addskinnoise_poc.py --input /path/to/photo.jpg --mode pore

# headless: текстура по рецепту gimp.md
PHOTOCRAFT_CLI=/path/to/photocraft-cli .venv/bin/python scripts/addskinnoise_poc.py \
  --input /path/to/photo.jpg --mode texture --auto
```

Оба режима создают Smart Object; фильтры остаются редактируемыми. `--mask black|skin`
добавляет маску для локального проявления.

Результаты: `out/addskinnoise-poc/<asset>/<mode>/native/`. Подробности —
`scripts/addskinnoise_poc.md`.

## Action и `.atn`

```bash
PYTHONPATH=src .venv/bin/python scripts/make_freqautosplit_action.py
```

Создаёт `actions/freqautosplit.atn` (23 шага, без `locks.pixels`) и
`actions/freqautosplit.action.json` (24 шага, с `locks.pixels`; журнал `actions.get`).
Диалоги включены на шагах `[2, 8]`: **Gaussian Blur → High Pass**.

Установка в запущенный PhotoCraft:

```bash
PYTHONPATH=src .venv/bin/python scripts/install_freqautosplit_action.py --atn
# или нативный Action из JSON (с locks.pixels):
PYTHONPATH=src .venv/bin/python scripts/install_freqautosplit_action.py --json
```

В UI: `Window → Actions` (`Alt+F9`) → действие `SHSMAD / Frequency Split (GB+HP)` →
выберите **один исходный слой** → **Play**.

`action.json` — не формат импорта, а журнал шагов; его использует `--json`.
Подробности — в `scripts/freqautosplit_poc.md`.

Конвертация JSON ↔ `.atn` (headless, GUI не нужен):

```bash
.venv/bin/python scripts/convert_action.py pack actions/freqautosplit.action.json \
  --out actions/freqautosplit.atn --force
.venv/bin/python scripts/convert_action.py unpack actions/freqautosplit.atn \
  --out actions/freqautosplit.action.json --force
```

`pack` пропускает шаги без Photoshop-эквивалента (`locks.pixels`) и печатает их;
`unpack` даёт JSON, совместимый с `install_freqautosplit_action.py --json`.

### Add Skin Noise Actions

```bash
.venv/bin/python scripts/make_addskinnoise_actions.py
```

Артефакты:

- `actions/addskin-pores.atn` — `Add Skin Pores`: Smart Object, шум 3, High Pass 0.5,
  blur 0.3, Overlay 0.5. High Pass заменяет Emboss, который PhotoCraft не пишет в `.atn`.
- `actions/addskin-texture.atn` — `Add Skin Texture`: серый слой, Smart Object, шум 12 →
  median 2 → шум 6 → median 1, Overlay.
- `actions/addskin-pores-emboss.action.json` — native Action с настоящим Emboss
  (только PhotoCraft; `.atn` этот шаг не поддерживает).

Маски в `.atn` не экспортируются (`layerMask.*` пропускается); добавляйте их вручную
или используйте native Action из JSON.

Когда PhotoCraft начнёт сериализовать `filter.stylize.emboss` в `.atn`, `addskin-pores.atn`
нужно перегенерировать с настоящим Emboss: в `make_addskinnoise_actions.py` заменить
`pore_steps("highpass")` на `pore_steps("emboss")` для `PORE_NAME` и обновить
`expected_filters`. Native JSON после этого нужен только для старых версий PhotoCraft.

Установка в GUI:

```bash
.venv/bin/python scripts/install_freqautosplit_action.py --atn actions/addskin-pores.atn
.venv/bin/python scripts/install_freqautosplit_action.py --atn actions/addskin-texture.atn
.venv/bin/python scripts/install_freqautosplit_action.py --json actions/addskin-pores-emboss.action.json
```

### Eyes & Teeth Action

```bash
.venv/bin/python scripts/make_eyesteeth_action.py
```

Создаёт `actions/eyesteeth.atn` (18 шагов, диалоги `[2, 6, 10]`) и
`actions/eyesteeth.action.json`.

Внутри — группа `Глаза и зубы` с тремя adjustment layers: `Белки`, `Зубы`, `Радужка`
(Hue/Saturation, значения как в GIMP-плагине). Чёрные маски создаются связкой
`select.all → select.inverse → adjustment layer`; это, в отличие от `layerMask.hideAll`,
полностью переживает `.atn`. После Play проявите эффект белой кистью по маскам.

Метод плагина `Desaturate + Brightness` пока не портирован — используется
Hue/Saturation. Установка:

```bash
.venv/bin/python scripts/install_freqautosplit_action.py --atn actions/eyesteeth.atn
```

### Skin Smoothing Actions

```bash
.venv/bin/python scripts/make_skinsmooth_actions.py
```

Создаёт три переносимых Action с диалогом радиуса (`[2]`):

- `actions/skinsmooth-gaussian.atn` — Gaussian Blur 10;
- `actions/skinsmooth-median.atn` — Median 10;
- `actions/skinsmooth-selective.atn` — Surface Blur 10 / threshold 51.

Внутри — группа `Сглаживание кожи`: копия `Оригинал` и слой
`(a) Мягкость (Lighten Only, <type> <radius>)`. Опционально:

```bash
.venv/bin/python scripts/make_skinsmooth_actions.py --blur-type gaussian --gbhp
```

добавит `НЧ (GB 3)` + `ВЧ (HP 3)` Linear Light 50%. Selective threshold =
`max_delta * 255` (0.2 → 51). Установка:

```bash
.venv/bin/python scripts/install_freqautosplit_action.py --atn actions/skinsmooth-gaussian.atn
```

### Watermark & Resize (single)

```bash
# GUI: обработать текущий документ; результат — новый документ, не сохраняется
.venv/bin/python scripts/waterres_single_poc.py --size 1920

# headless-проверка
PHOTOCRAFT_CLI=/path/to/photocraft-cli .venv/bin/python scripts/waterres_single_poc.py \
  --auto --input /path/to/photo.jpg --out out/waterres-single
```

Скрипт делает merged-копию (`image.duplicate {mergedOnly: true}`), оригинал со слоями
не трогает. В копии: Lanczos resize по длинной стороне, Unsharp Mask r2.5/20/0,
High Pass 1 + Linear Light 40%, watermark `watermark_bw_100.png` (Down-Left 20/20,
opacity 25%, Difference, без масштабирования). Флаги: `--pre-blur`,
`--no-final-contrast`, `--no-watermark`, `--no-resize`.

Чистый `.atn` для resize невозможен: `image.imageSize` не экспортируется в PhotoCraft
0.10.0. Детали и repro — `atn-issue-imageSize.txt`. Результаты headless:
`out/waterres-single/<asset>/{result.png,manifest.json}`.

## Проверки

```bash
pdm run check   # lint + typecheck + test
```

Integration-тест конвертера запускается только при явном `PHOTOCRAFT_CLI`:

```bash
PHOTOCRAFT_CLI=/path/to/photocraft-cli pdm run test
```

Без переменной он пропускается. CI в проекте нет.

## Лицензия

MIT.

Текущий контекст сессии и проверенные детали — `HANDOFF.md`.
