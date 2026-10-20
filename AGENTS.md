# AGENTS.md

Автоматизация PhotoCraft 0.10: переносимый Action/`.atn` для freqautosplit (GB+HP),
GIMP-эталон и попиксельное сравнение. MIDI-прослойка X-Touch Mini — отдельный проект
`../photocraft-midi` (не дублировать сюда). Контекст прошлой сессии — `HANDOFF.md`,
пользовательские команды — `README.md`, детали PoC — `scripts/freqautosplit_poc.md`.

## Состояние репозитория

- Git-репозиторий есть, ветка `main`, remote `origin` настроен, CI нет. Коммиты —
  напрямую в `main` (ветка/PR — для рискованных изменений), push — только по явной просьбе.
- `actions/` — готовые артефакты, `out/` — результаты прогонов (gitignored). Не править
  вручную; перегенерировать скриптами.
- Полный цикл требует внешних PhotoCraft 0.10 (GUI+control или `photocraft-cli`) и
  GIMP 3.2 с плагином `SHSMAD_Process_freqautosplit`; в репозитории их нет.
- Unit-тесты — чистые функции (numpy/Pillow); integration-тесты конвертера и Add Skin
  Actions требуют явный `PHOTOCRAFT_CLI` и иначе пропускаются. GIMP для тестов не нужен.

## Структура

- `src/photocraft_atns/mcp_client.py` — единственный модуль пакета: MCP-клиент поверх
  `photocraft-cli mcp` (stdio, протокол `2025-11-25`), bridge и headless.
  `STREAM_LIMIT = 4 МиБ` — ответы `command_list` бывают очень большими.
- `scripts/freqautosplit_poc.py` — сборка стека `Ретушь`; интерактив (диалоги) или `--auto`.
- `scripts/make_freqautosplit_action.py` — запись Action, проверка playback, экспорт `.atn`.
- `scripts/install_freqautosplit_action.py` — установка Action в запущенный GUI (`--atn`/`--json`).
- `scripts/convert_action.py` — headless-конвертер Action JSON ↔ `.atn` (`pack`/`unpack`).
- `scripts/addskinnoise_poc.py` — недеструктивный PoC Add Skin Noise (`pore`/`texture`),
  Smart Object + smart filters, auto/интерактив.
- `scripts/make_addskinnoise_actions.py` — запись/проверка Add Skin Pores/Texture Actions,
  экспорт `.atn` и native JSON.
- `scripts/make_eyesteeth_action.py` — Eyes & Teeth Action (группа + adjustment layers),
  экспорт `.atn`.
- `scripts/waterres_single_poc.py` — Watermark & Resize (single) через MCP; `.atn` не
  используется, потому что `image.imageSize` в него не экспортируется.
- `scripts/make_skinsmooth_actions.py` — Skin Smoothing Actions (Gaussian/Median/
  Selective, опционально GB+HP), экспорт `.atn`.
- `scripts/gimp_freqsplit_reference.py`, `scripts/run_gimp_reference.sh` — эталон GIMP.
- `scripts/compare_images.py` — RMSE/PSNR/max, diff-PNG, `report.{md,json}`.
- `tests/test_poc_helpers.py` — юнит-тесты слоёв/метрик.
- `tests/test_addskinnoise_helpers.py` — юнит-тесты режимов/значений PoC.
- `tests/test_addskinnoise_actions_helpers.py` — юнит-тесты шагов/Action-спеков.
- `tests/test_eyesteeth_helpers.py` — юнит-тесты шагов/группы Eyes & Teeth.
- `tests/test_integration_convert.py` — opt-in roundtrip `.atn` (нужен `PHOTOCRAFT_CLI`).
- `tests/test_integration_addskinnoise_actions.py` — opt-in генерация и playback Actions.
- `tests/test_integration_eyesteeth_action.py` — opt-in генерация/playback Eyes & Teeth.
- `tests/test_waterres_helpers.py` — юнит-тесты размеров/позиции watermark.
- `tests/test_integration_waterres_single.py` — opt-in auto resize + watermark.
- `tests/test_skinsmooth_helpers.py` — юнит-тесты blur/GB+HP шагов.
- `tests/test_integration_skinsmooth_actions.py` — opt-in генерация/playback.

## Команды

```bash
pdm install
pdm run lint        # ruff check src tests scripts
pdm run typecheck   # ty check
pdm run test        # pytest, ~0.1 c (integration skip без PHOTOCRAFT_CLI)
pdm run check       # lint + typecheck + test

# один тест
pdm run test tests/test_poc_helpers.py::test_metrics_identical_and_full_difference

# integration-тесты (roundtrip .atn, генерация/playback Add Skin Actions)
PHOTOCRAFT_CLI=<cli> pdm run test tests/test_integration_convert.py \
  tests/test_integration_addskinnoise_actions.py tests/test_integration_eyesteeth_action.py \
  tests/test_integration_waterres_single.py tests/test_integration_skinsmooth_actions.py

# Add Skin Actions: regenerate actions/addskin-*.atn + native JSON
.venv/bin/python scripts/make_addskinnoise_actions.py

# Eyes & Teeth Action: regenerate actions/eyesteeth.{atn,action.json}
.venv/bin/python scripts/make_eyesteeth_action.py

# Skin Smoothing Actions: regenerate actions/skinsmooth-*.atn
.venv/bin/python scripts/make_skinsmooth_actions.py

# Watermark & Resize (single): текущий GUI-документ
.venv/bin/python scripts/waterres_single_poc.py --size 1920

# headless-проверка
PHOTOCRAFT_CLI=<cli> .venv/bin/python scripts/waterres_single_poc.py \
  --auto --input <file> --out out/waterres-single

# скрипты: PYTHONPATH не нужен — скрипт сам вставляет src в sys.path
.venv/bin/python scripts/freqautosplit_poc.py --input <file>
```

Python 3.14; `_.env_file = ".env"` в `pyproject.toml`, поэтому `pdm run` читает `.env`.
Прямой `.venv/bin/python` тоже безопасен: скрипты сами вызывают
`load_dotenv(REPO_ROOT/".env")`. `ty` не видит стабы `gi` в GIMP-payload — строки помечены
`# ty: ignore[unresolved-import]`.

Headless (`--auto`) требует `photocraft-cli`. Если `/tmp` очищен:

```bash
~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage --appimage-extract
export PHOTOCRAFT_CLI=$PWD/squashfs-root/usr/bin/photocraft-cli
```

## PhotoCraft 0.10: MCP-факты

- Bridge: `test -f .env` + `PHOTOCRAFT_CONTROL_PORT` / `PHOTOCRAFT_CONTROL_TOKEN_FILE`;
  GUI обязателен. Headless: `--bridge` не передаётся, нужны automation-roots для
  `doc_open`/`doc_save`.
- Bridge-only: `ui_*`, `control_call`. Headless-only: `doc_select`, `doc_close`.
- `doc_save` в bridge принимает только `path` (± `tgaBits`), без `format`; в headless — с
  `format`. Каталог назначения должен уже существовать.
- `doc_export_layers` в bridge уходит в ambient `file.export.layersToFiles` и **отклоняется**;
  для поштучного экспорта — `layer.duplicate {document:"new"}` + `doc_save`.
- `command_list` — голый массив; часть команд (`view.zoomIn/…`) в нём отсутствует, но
  исполняется через `command_run`.
- Интерактивные фильтры: `ui_menu_invoke {id}` без params открывает Command-диалог с
  live-preview и возвращает `{dialog:id}`; поля — `ui_inspect.dialogs[].fields` (radius,
  `__preview`, `__pvCx/__pvCy/__pvZoom`); управление — `control_call ui.dialog.set/confirm/cancel`.
- Actions: `actions.record/stop/insert/get/dialog/assignSet/play/load/save`. `actions.save`
  без `path` возвращает `.atn` в base64 `data`; `actions.load` принимает `path` или `data`.
  `actions.dialog` включает показ диалога шага; шаг без Photoshop-эквивалента требует
  `skipUnsupported: true`. Headless `actions.play` играет записанные params.
- Слои: `layer.duplicate/new.layer/new.group` возвращают `{layer:id}`; `layer.moveTo`
  требует **id** в `target`; `layer.groupLayers` — selection-based и переносим.
- Фильтры применять **до** `locks.pixels`, иначе «Could not complete…».

## Action freqautosplit

- Переносимый: ссылки на слои — **по именам**, группа собирается `layer.groupLayers`,
  id не используются. При Play нужен **ровно один исходный пиксельный слой**
  (Action дублирует активный).
- Порядок сборки: `freq-src` → `НЧ (GB N) Оригинал` (Gaussian Blur) → `НЧ (GB N)` →
  `ВЧ` (High Pass, поднимается `layer.arrange.bringForward` ×2) → DnB; затем `groupLayers`.
- Диалоги: **Gaussian Blur → High Pass** (шаги `[2, 8]`).
- Нативный Action: 24 шага, включая `locks.pixels`; `.atn`: 23 шага (шаг lock
  пропускается — нет Photoshop-эквивалента). Имена слоёв фиксируются на записанный
  радиус (`10.0`), даже если в диалоге выбрать другой.
- Меняешь рецепт — перегенерируй `make_freqautosplit_action.py`, затем при необходимости
  переустанови `install_freqautosplit_action.py --json`.
- `convert_action.py` ходит в headless `photocraft-cli mcp` (GUI не нужен): `pack`
  повторяет шаги через `actions.insert` и сохраняет с `skipUnsupported`, `unpack` —
  `actions.load` + `actions.get`. Roundtrip текущего `.atn` байт-в-байт; JSON совместим с
  `--json`.

## GIMP-эталон и diff

- GIMP 3.2.6, `gimp-console -i -n -s --batch-interpreter=python-fu-eval`; плагин
  `plug-in-shsmad-process-freqautosplit` (`createfreqsplit=on`, `creatednb=off`,
  `amount=10`, `blur-type=0`). `drawables` задаётся `config.set_core_object_array`.
- Экспорт: `composite` (исходник скрыт, `new_from_visible`), `low` (рабочий НЧ), `high` (ВЧ);
  `file-png-export`. GEGL-warning/утечка буфера при выходе — безвредны.
- Сравнение: LOW/композит — численно; HIGH — информативно (Grain Merge vs High Pass).
  Жёсткого порога нет. Калибровка радиуса — только при заметном расхождении.
- Результаты (radius 10): LOW/композит RMSE ≈ 0.003–0.008 (PSNR ≈ 41–51 dB);
  native-реконструкция не хуже GIMP. Полные таблицы — в `out/freqsplit-poc/<asset>/report.md`.

## Add Skin Noise (PoC)

- `scripts/addskinnoise_poc.py`: `pore` (алгоритм Google: дубль → Smart Object →
  Gaussian Noise 3 → Emboss → Gaussian Blur 0.3 → Overlay 0.5) и `texture` (серый слой →
  Smart Object → Noise 12 → Median 2 → Noise 6 → Median 1 → Overlay).
- Оба режима недеструктивные: smart filters редактируются, `--mask black|skin` скрывает
  эффект; исходный слой не меняется. `plugin` не используем — он даёт Normal 100 % и
  разрушающе заменяет изображение.
- Проверка: `out/addskinnoise-poc/<asset>/<mode>/native/`; в манифесте — `smart_filters`.
- Actions: `scripts/make_addskinnoise_actions.py` → `actions/addskin-pores.atn` (High Pass
  0.5 вместо Emboss), `actions/addskin-texture.atn`, `actions/addskin-pores-emboss.action.json`
  (native Emboss). Диалоги `[2, 3, 4]` / `[3, 4]`; roundtrip `.atn` проверяется скриптом.
- `filter.stylize.emboss`, `layerMask.*` и `bevelEmboss` не экспортируются в `.atn` —
  см. `atn-issue.txt`. GIMP-эталон пока не делался.
- Когда Emboss появится в `.atn`: в `make_addskinnoise_actions.py` переключить
  `PORE_NAME` на `pore_steps("emboss")`, обновить `expected_filters`, перегенерировать
  `actions/addskin-*.atn`; native JSON оставить для старых версий PhotoCraft.

## Eyes & Teeth (Action)

- `scripts/make_eyesteeth_action.py` → `actions/eyesteeth.atn` (18 шагов, диалоги
  `[2, 6, 10]`) + JSON. Группа `Глаза и зубы`, adjustment layers `Белки`, `Зубы`,
  `Радужка` (Hue/Saturation: −70/+60, −70/+60, +35/+10).
- Чёрные маски: `select.all → select.inverse → layer.newAdjustmentLayer.hueSaturation`.
  Это экспортируется в `.atn`; `layer.layerMask.hideAll` — нет. Группировка —
  `layer.groupLayers`.
- После Play эффект проявляется белой кистью. Метод `Desaturate + Brightness` не
  портирован; используется Hue/Saturation.

## Skin Smoothing (Action)

- `scripts/make_skinsmooth_actions.py` → `actions/skinsmooth-{gaussian,median,selective}.atn`
  (8 шагов, диалог `[2]`). Группа `Сглаживание кожи`: `Оригинал` + soft-слой
  `Lighten` с blur. Selective: Surface Blur, threshold = `max_delta * 255`.
- `--gbhp` добавляет `НЧ (GB N)` (Gaussian) и `ВЧ (HP N)` (High Pass, Linear Light,
  hp_opacity); 15 шагов, диалоги `[2, 5, 8]`. Порядок создания: base → soft → high → low;
  `layer.duplicate` вставляет новую копию над base, поэтому группа получается
  soft/high/low/base.
- Все шаги (`Lighten`, blur, high pass, groupLayers) экспортируются в `.atn` без `skipped`.

## Watermark & Resize (single)

- `scripts/waterres_single_poc.py` — MCP-скрипт, не `.atn`: `image.duplicate
  {mergedOnly: true}` (оригинал не трогается), Lanczos resize по длинной стороне,
  Unsharp `radius 2.5 / amount 20 / threshold 0`, High Pass 1 + Linear Light 40%,
  watermark `watermark_bw_100.png` (Down-Left 20/20, 25%, Difference).
- `.atn` с resize невозможен: `image.imageSize` пропускается при `actions.save`
  (`atn-issue-imageSize.txt`); `file.placeEmbedded` экспортируется, но MCP-playback
  отключён; `doc_place` работает в MCP. Bridge требует GUI с control-портом; `--auto`
  работает headless и пишет `result.png` + `manifest.json`.
- Pre-blur (`--pre-blur`) — только в скрипте; в `.atn` runtime-флага нет. Watermark
  не масштабируется; маска/слои не нужны.

## GIMP-рецепты → PhotoCraft (только при портировании)

Источник: `../mysnippets/gimp/gimp.md` (+ `v3/plugins/SHSMAD_Process/{IMPLEMENTATION_PLAN,skintones}.md`).

- **Grain Extract/Merge** в PhotoCraft отсутствуют; эквивалент — **Invert + Linear Light**,
  а для HIGH — Linear Light **50%** (математически = Grain Merge; 100% удваивает детали).
- **freqautosplit**: LOW = blur N; HIGH = highPass N + Linear Light 50%; 3-частотный —
  blur N · highPass N→blur M · highPass M (N:M ≈ 3:1).
- Радиусы: 1/250 высоты лица — фактура, 1/100 — мелкие дефекты, 1/50 — крупные.
- GIMP Python-Fu в PhotoCraft не загружается; WASM-плагины — только `filter` без I/O.
  Сеть/ML (rembg, SD) — только внешние скрипты по MCP.

## Правила

- Не коммитить автоматически; `actions/` — артефакты, `out/` — результаты прогонов.
- Не менять `../photocraft-midi` без явной просьбы.
- Токен/порт не логировать; `.env` не коммитить и не перезаписывать.
