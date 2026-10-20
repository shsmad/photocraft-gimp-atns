# AGENTS.md

Автоматизация PhotoCraft 0.10: переносимый Action/`.atn` для freqautosplit (GB+HP),
GIMP-эталон и попиксельное сравнение. MIDI-прослойка X-Touch Mini — отдельный проект
`../photocraft-midi` (не дублировать сюда). Контекст прошлой сессии — `HANDOFF.md`,
пользовательские команды — `README.md`, детали PoC — `scripts/freqautosplit_poc.md`.

## Состояние репозитория

- Git-репозиторий есть, ветка `main`; remote не настроен, CI нет. Коммиты — напрямую
  в `main` (ветка/PR — для рискованных изменений), push — только по явной просьбе.
- `actions/` — готовые артефакты, `out/` — результаты прогонов (gitignored). Не править
  вручную; перегенерировать скриптами.
- Полный цикл требует внешних PhotoCraft 0.10 (GUI+control или `photocraft-cli`) и
  GIMP 3.2 с плагином `SHSMAD_Process_freqautosplit`; в репозитории их нет.
- Unit-тесты — чистые функции (numpy/Pillow); integration-тест конвертера требует явный
  `PHOTOCRAFT_CLI` и иначе пропускается. GIMP для тестов не нужен.

## Структура

- `src/photocraft_atns/mcp_client.py` — единственный модуль пакета: MCP-клиент поверх
  `photocraft-cli mcp` (stdio, протокол `2025-11-25`), bridge и headless.
  `STREAM_LIMIT = 4 МиБ` — ответы `command_list` бывают очень большими.
- `scripts/freqautosplit_poc.py` — сборка стека `Ретушь`; интерактив (диалоги) или `--auto`.
- `scripts/make_freqautosplit_action.py` — запись Action, проверка playback, экспорт `.atn`.
- `scripts/install_freqautosplit_action.py` — установка Action в запущенный GUI (`--atn`/`--json`).
- `scripts/convert_action.py` — headless-конвертер Action JSON ↔ `.atn` (`pack`/`unpack`).
- `scripts/gimp_freqsplit_reference.py`, `scripts/run_gimp_reference.sh` — эталон GIMP.
- `scripts/compare_images.py` — RMSE/PSNR/max, diff-PNG, `report.{md,json}`.
- `tests/test_poc_helpers.py` — юнит-тесты слоёв/метрик.
- `tests/test_integration_convert.py` — opt-in roundtrip `.atn` (нужен `PHOTOCRAFT_CLI`).

## Команды

```bash
pdm install
pdm run lint        # ruff check src tests scripts
pdm run typecheck   # ty check
pdm run test        # pytest, ~0.1 c (integration skip без PHOTOCRAFT_CLI)
pdm run check       # lint + typecheck + test

# один тест
pdm run test tests/test_poc_helpers.py::test_metrics_identical_and_full_difference

# integration-тест конвертера (roundtrip .atn)
PHOTOCRAFT_CLI=<cli> pdm run test tests/test_integration_convert.py

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
