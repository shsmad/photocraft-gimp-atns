# HANDOFF — photocraft-gimp-atns

Дата: 2026-10-20. Передача контекста после сессии: перенос `freqautosplit` из GIMP в
PhotoCraft, Action/`.atn`, GIMP-эталон, попиксельный diff; автоматизация вынесена из
MIDI-проекта в этот репозиторий.

## Состояние

Готово и проверено на живом PhotoCraft **0.10.0** и GIMP **3.2.6**:

- интерактивный PoC (`scripts/freqautosplit_poc.py`) — диалоги **Gaussian Blur → High Pass**,
  живое превью, откат по Cancel; `--auto` — headless;
- GIMP-эталон реальным плагином (`scripts/run_gimp_reference.sh`);
- diff/метрики (`scripts/compare_images.py`);
- переносимый Action и экспорт `.atn` (`scripts/make_freqautosplit_action.py`);
- установка Action в GUI (`scripts/install_freqautosplit_action.py`);
- конвертер Action JSON ↔ `.atn` через headless MCP (`scripts/convert_action.py`,
  roundtrip текущего `.atn` байт-в-байт);
- opt-in integration-тест конвертера и `pdm run check`;
- недеструктивный PoC Add Skin Noise в режимах `pore`/`texture` (`scripts/addskinnoise_poc.py`);
- Add Skin Pores/Texture Actions и `.atn` (`scripts/make_addskinnoise_actions.py`);
- Eyes & Teeth Action с группой и чёрными масками (`scripts/make_eyesteeth_action.py`);
- Watermark & Resize (single) скрипт (`scripts/waterres_single_poc.py`);
- Skin Smoothing Actions (`scripts/make_skinsmooth_actions.py`).

Артефакты:

- `actions/freqautosplit.atn` — 23 шага (без `locks.pixels`), диалоги `[2, 8]`;
- `actions/freqautosplit.action.json` — 24 шага (с `locks.pixels`), журнал `actions.get`;
- `actions/addskin-pores.atn` — 6 шагов, High Pass 0.5 вместо Emboss, диалоги `[2, 3, 4]`;
- `actions/addskin-texture.atn` — 8 шагов, диалоги `[3, 4]`;
- `actions/addskin-pores-emboss.action.json` — native Action с Emboss (без `.atn`);
- `actions/eyesteeth.atn` — 18 шагов, диалоги `[2, 6, 10]`, группа `Глаза и зубы`;
- `actions/eyesteeth.action.json` — журнал Eyes & Teeth;
- `actions/skinsmooth-{gaussian,median,selective}.atn` — 8 шагов, диалог `[2]`;
- Action уже установлен в `~/.config/photocraft/Presets/actions.json`
  (`SHSMAD / Frequency Split (GB+HP)`, 24 шага, диалоги `[2, 8]`);
- отчёты: `out/freqsplit-poc/<asset>/report.{md,json}` + `diff/*.png`.

Git: ветка `main`, remote `origin` настроен. Push — только по явной команде. CI не
планируется. MIDI-проект содержит незакоммиченные обновления знаний под 0.10 (см. ниже).

## Ключевые решения и находки

- **Рецепт**: LOW = Gaussian Blur; HIGH = High Pass + **Linear Light 50%**. Это
  математически эквивалентно GIMP `Grain Merge`; при 100% детали удваиваются (RMSE к
  исходнику ~0.014 вместо ~0.002).
- **Порядок диалогов**: Blur → High Pass. HIGH создаётся после LOW и поднимается
  `layer.arrange.bringForward` ×2.
- **Action переносимый**: ссылки на слои — по именам, группа — `layer.groupLayers`,
  id не используются. Первый шаг дублирует **активный** слой, поэтому имя исходника
  не важно; нужен ровно один выбранный пиксельный слой.
- **`.atn` vs JSON**: `.atn` теряет `locks.pixels` (нет Photoshop-эквивалента) → 23 шага;
  JSON/native Action — 24 шага. Диалоги сохраняются в `.atn` (`[2, 8]`).
- **Имена слоёв** фиксированы на записанный радиус (`НЧ (GB 10.0)`), даже если в диалоге
  выбрать другой. Для динамических имён — использовать PoC-скрипт, не Action.
- **Эталон**: реальный `plug-in-shsmad-process-freqautosplit` (`createfreqsplit=on`,
  `creatednb=off`, `amount=10`, `blur-type=0`), GIMP headless через `python-fu-eval`.
- **Сравнение**: LOW и композит — численно, HIGH — информативно (разные кодировки).
  Жёсткого порога нет.
- **Плагины GIMP в PhotoCraft не загружаются**: только WASM-фильтры без сети/ФС.
  rembg/SD — только внешние скрипты по MCP (в этом проекте не реализованы).
- **Конвертер JSON ↔ `.atn`**: `.atn` — Photoshop 8BIM, поэтому конвертация идёт через
  headless PhotoCraft (`actions.load/get/insert/save`), а не локальным парсером.
  `pack` пропускает шаги без Photoshop-эквивалента (`locks.pixels`) и печатает `skipped`;
  `unpack` даёт JSON, совместимый с `install_freqautosplit_action.py --json`.
- **Add Skin Noise**: плагин в `~/.config/GIMP/3.2/plug-ins/SHSMAD_Process/plugins/`
  разрушающий (Normal 100 %) и содержит баг с blur; поэтому PoC недеструктивный:
  `pore` — дубль → Smart Object → шум 3 → Emboss → blur 0.3 → Overlay 0.5;
  `texture` — серый слой → Smart Object → шум 12 → median 2 → шум 6 → median 1.
  Фильтры остаются smart filters; `--mask black|skin`.
- **Add Skin Actions**: `filter.stylize.emboss` не сериализуется в `.atn`, поэтому
  `addskin-pores.atn` использует High Pass 0.5 (визуально близко), а точный Emboss
  остаётся в `addskin-pores-emboss.action.json`. `layerMask.*` в `.atn` тоже теряются.
  Детали и repro — `atn-issue.txt`.
- **Когда Emboss появится в `.atn`**: в `make_addskinnoise_actions.py` заменить
  `pore_steps("highpass")` на `pore_steps("emboss")` для `PORE_NAME`, обновить
  `expected_filters` и перегенерировать `actions/addskin-pores.atn`; native JSON
  оставить для совместимости со старыми PhotoCraft.
- **Eyes & Teeth**: чёрные маски создаются через `select.all → select.inverse` перед
  adjustment layer — этот путь экспортируется в `.atn`, в отличие от `layerMask.hideAll`.
  Группа собирается `layer.groupLayers`; после Play маски проявляются белой кистью.
  Метод `Desaturate + Brightness` пока не портирован.
- **Watermark & Resize (single)**: `.atn` невозможен — `image.imageSize` пропускается
  при `actions.save`; `file.placeEmbedded` в MCP-playback отключён. Поэтому MCP-скрипт:
  merged-копия (`image.duplicate mergedOnly:true`), Lanczos + Unsharp r2.5/20, High Pass
  1/Linear Light 40%, watermark Down-Left 20/20/25%/Difference через `doc_place`.
  Issue по resize — `atn-issue-imageSize.txt`.
- **Skin Smoothing**: `Lighten` — это PhotoCraft-имя для GIMP Lighten Only. Группа
  собирается `layer.groupLayers`; selective threshold = `max_delta * 255`. GB+HP:
  создавать base → soft → high → low, иначе группа получается в обратном порядке.

## Окружение (факты)

- PhotoCraft AppImage: `~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage`.
- `photocraft-cli 0.10.0 (8407d9ccb, 2026-10-18)`; MCP-протокол согласуется как `2025-11-25`.
- В сессии использовался распакованный CLI:
  `/tmp/opencode/pc010/squashfs-root/usr/bin/photocraft-cli` — **`/tmp` эфемерен**.
  Восстановить:

  ```bash
  ~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage --appimage-extract
  export PHOTOCRAFT_CLI=$PWD/squashfs-root/usr/bin/photocraft-cli
  ```

- `.env`: `PHOTOCRAFT_CONTROL_PORT=7878`,
  `PHOTOCRAFT_CONTROL_TOKEN_FILE=~/.config/photocraft/control.token`
  (путь; содержимое токена не читать/не логировать).
- GUI для интерактива: `../photocraft-midi/launch-photocraft-midi.sh` (или AppImage с
  `--control <port> --control-token-file <path>`).
- GIMP: `/usr/bin/gimp-console` 3.2.6, Python-Fu `python-fu-eval`; плагин установлен в
  `~/.config/GIMP/3.2/plug-ins/SHSMAD_Process/`.
- Тестовые ассеты (внешние, `../mysnippets/gimp/...`):
  `DSC00463_part_before.JPG` (1010×1190), `faceskin_before.png` (438×295),
  `DSC00463.JPG` (4000×6000).

## Проверенные MCP-факты PhotoCraft 0.10

- Bridge-only: `ui_*`, `control_call`; headless-only: `doc_select`, `doc_close`.
- `doc_save` в bridge — только `path` (± `tgaBits`); `format` только в headless.
- `doc_export_layers` в bridge уходит в ambient `file.export.layersToFiles` и **отклоняется**;
  для поштучного экспорта — `layer.duplicate {document:"new"}` + `doc_save`.
- `doc_render_preview`/`render_preview` в bridge — скриншот окна; headless — PNG (≤2048, ≤5 МиБ).
- `command_list` — голый массив; часть команд (`view.zoomIn/…`) в нём отсутствует, но
  исполняется через `command_run`.
- Диалоги: `ui_menu_invoke {id}` без params → `{dialog:id}` + live-preview; поля в
  `ui_inspect.dialogs[].fields` (`radius`, `__preview`, `__pvCx/__pvCy/__pvZoom`);
  `control_call ui.dialog.set/confirm/cancel`.
- Actions: `actions.load/save` принимают `data` (base64) — удобно без FS-путей;
  `actions.save` без `path` возвращает `data`; при шаге без Photoshop-эквивалента нужен
  `skipUnsupported: true`; `actions.dialog` включает диалог шага; `actions.play` в
  headless играет записанные params.
- Слои: `layer.duplicate/new.layer/new.group` возвращают `{layer:id}`; `layer`-параметры
  принимают имена; `layer.moveTo` требует **id** в `target`; `layer.groupLayers` —
  selection-based и переносим; `layer.arrange.bringForward` — активный слой.
- Фильтры применять **до** `locks.pixels` (иначе «Could not complete…»).
- `doc_save` требует, чтобы каталог назначения уже существовал на хосте.
- `filter.blur.gaussianBlur`, `filter.other.highPass`, `filter.noise.addNoise/median`,
  `layer.layerMask.*`, `select.subject` — доступны.

## Команды

```bash
pdm install

# интерактив (нужен GUI)
PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py --input <file>
# auto (headless)
PHOTOCRAFT_CLI=<cli> PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py --input <file> --auto

# эталон и сравнение
scripts/run_gimp_reference.sh <file> out/freqsplit-poc/<asset>/gimp 10
.venv/bin/python scripts/compare_images.py --run-dir out/freqsplit-poc/<asset> --input <file>

# Action
PYTHONPATH=src .venv/bin/python scripts/make_freqautosplit_action.py
PYTHONPATH=src .venv/bin/python scripts/install_freqautosplit_action.py --atn
# или --json (нативный Action с locks.pixels)

# конвертер JSON ↔ .atn (headless, GUI не нужен)
.venv/bin/python scripts/convert_action.py pack actions/freqautosplit.action.json \
  --out actions/freqautosplit.atn --force
.venv/bin/python scripts/convert_action.py unpack actions/freqautosplit.atn \
  --out actions/freqautosplit.action.json --force

# Add Skin Noise PoC (недеструктивный, Smart Object)
.venv/bin/python scripts/addskinnoise_poc.py --input <file> --mode pore
.venv/bin/python scripts/addskinnoise_poc.py --input <file> --mode pore --mask black
PHOTOCRAFT_CLI=<cli> .venv/bin/python scripts/addskinnoise_poc.py --input <file> --mode texture --auto

# Add Skin Actions
.venv/bin/python scripts/make_addskinnoise_actions.py

# Eyes & Teeth Action
.venv/bin/python scripts/make_eyesteeth_action.py

# Skin Smoothing Actions
.venv/bin/python scripts/make_skinsmooth_actions.py

# Watermark & Resize (single)
.venv/bin/python scripts/waterres_single_poc.py --size 1920
PHOTOCRAFT_CLI=<cli> .venv/bin/python scripts/waterres_single_poc.py \
  --auto --input <file> --out out/waterres-single

pdm run check   # lint + typecheck + test
PHOTOCRAFT_CLI=<cli> pdm run test tests/test_integration_convert.py \
  tests/test_integration_addskinnoise_actions.py tests/test_integration_eyesteeth_action.py \
  tests/test_integration_waterres_single.py tests/test_integration_skinsmooth_actions.py
```

## Результаты (radius 10, native vs GIMP)

| ассет | low RMSE (PSNR) | composite RMSE (PSNR) | native vs source |
| --- | --- | --- | --- |
| `DSC00463_part_before.JPG` | 0.006481 (43.77) | 0.003016 (50.41) | 0.002862 |
| `faceskin_before.png` | 0.002959 (50.58) | 0.002902 (50.75) | 0.002947 |
| `DSC00463.JPG` | 0.008432 (41.48) | 0.004347 (47.24) | 0.004239 |

Полные таблицы — в `out/freqsplit-poc/<asset>/report.md`.

## Ограничения и гочи

- Action требует **один** исходный пиксельный слой; имена фиксированы на радиус.
- `.atn` без `locks.pixels`; native/JSON — с ним.
- GIMP при выходе пишет GEGL-warning/утечку буфера — безвредно.
- Полный кадр 24 МП: сравнение ~57 с и ~2.7 ГБ RSS (наблюдалось).
- Клиентский stdout-лимит поднят до 4 МиБ (большие `command_list`).
- `pack` теряет шаги без Photoshop-эквивалента (`locks.pixels`) — это ожидаемо;
  `unpack` восстанавливает `steps`, `dialogs`, `disabled`, `color`.
- Integration-тест конвертера запускается только при явном `PHOTOCRAFT_CLI`; обычный
  `pdm run test` его пропускает.
- Add Skin Noise: Actions записаны и roundtrip проверен. Осталось визуально проверить
  в GUI загруженные `addskin-pores.atn` / `addskin-texture.atn` и native Emboss.
  Маски в `.atn` не экспортируются — добавлять вручную; при необходимости ставить
  native Action из JSON.
- Не логировать токен; `.env` не коммитить.

## MIDI-проект `../photocraft-midi`

- Автоматизация/скрипты/тест/`out/` удалены; `mcp_client.py` снова только bridge-клиент
  (сохранены `command_batch`, лимит stdout, протокол `2025-11-25`).
- Убраны dev-зависимости `numpy`/`Pillow`; `pyproject.toml`/`pdm.lock` восстановлены.
- Удалён раздел GIMP-рецептов из `AGENTS.md` и PoC-указатель из README.
- Незакоммичены только обновления знаний под 0.10: `README.md`, `AGENTS.md`,
  `.env.example`, `launch-photocraft-midi.sh`, `midi.py` (докстринг),
  `mcp_client.py` (protocolVersion), `tests/test_integration.py` (skip без GUI).
- Проверки: lint/typecheck чисто, **55 passed, 1 skipped**.

## Следующие шаги

- Визуально проверить Add Skin Actions, Eyes & Teeth, Skin Smoothing и Watermark & Resize в GUI.
- Когда Emboss появится в `.atn`: перегенерировать `addskin-pores.atn` с настоящим
  Emboss (см. раздел выше) и обновить issue/документацию.
- Когда `image.imageSize` появится в `.atn`: можно сделать portable resize Action
  (см. `atn-issue-imageSize.txt`).
- Следующий кандидат: `skintone` (сложнее: цветокоррекция + маска по коже).
- Внешний скрипт rembg (план из сессии: экспорт слоя → `:7000` → маска в слой).
- CI не планируется; локальный гейт — `pdm run check`.
