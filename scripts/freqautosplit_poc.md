# freqautosplit PoC (PhotoCraft 0.10)

Перенос GIMP-плагина `SHSMAD_Process_freqautosplit` в PhotoCraft: строит тот же стек
слоёв (группа `Ретушь`, `НЧ (GB N) Оригинал`, `НЧ (GB N)`, `ВЧ`, `DnB Face/Body/Global`),
но разделение частот делает нативно:

- **LOW** = копия слоя → Gaussian Blur;
- **HIGH** = копия исходника → High Pass + режим `Linear Light`, opacity **50%**
  (это математически эквивалентно GIMP `Grain Merge`);
- вместо GIMP `Grain Extract/Merge` (в PhotoCraft их нет) — GB+HP, где HIGH при 50% Linear Light
  даёт тот же вклад, что Grain Merge.

В интерактивном режиме радиусы Blur и High Pass подбираются в живых диалогах PhotoCraft
(превью на холсте), затем применяются. В `--auto` — без диалогов, headless.

## Файлы

- `freqautosplit_poc.py` — сборка стека (bridge/headless), интерактив и auto.
- `gimp_freqsplit_reference.py` — payload Python-Fu, запускающий реальный GIMP-плагин.
- `run_gimp_reference.sh` — обёртка `gimp-console` для эталона.
- `compare_images.py` — метрики и diff-PNG.
- `make_freqautosplit_action.py` — записывает переносимый Action и экспортирует `.atn`.
- `install_freqautosplit_action.py` — ставит Action в запущенный PhotoCraft (`.atn` или JSON).

## Требования

- PhotoCraft **0.10.0**, `photocraft-cli` доступен как `PHOTOCRAFT_CLI` или через
  смонтированный AppImage (`/tmp/.mount_*/usr/bin/photocraft-cli`).
- Для интерактива: GUI запущен с control-портом (штатный `../photocraft-midi/launch-photocraft-midi.sh`),
  в `.env` — `PHOTOCRAFT_CONTROL_PORT` и `PHOTOCRAFT_CONTROL_TOKEN_FILE`.
- Для эталона: GIMP **3.2** и установленный плагин `plug-in-shsmad-process-freqautosplit`.
- Зависимости проекта: `pdm install` (numpy, Pillow, python-dotenv).

## Интерактивный прогон

1. Запустите GUI: `../photocraft-midi/launch-photocraft-midi.sh` (MIDI-демон можно не трогать).
2. Откройте изображение в PhotoCraft и выделите **ровно один пиксельный слой**.
3. Запустите:

```bash
PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py --input /path/to/photo.jpg
```

Откроются два диалога:

1. **Gaussian Blur** — радиус LOW (по умолчанию 10);
2. **High Pass** — радиус HIGH (по умолчанию следует за фактически выбранным blur, если `--hp-radius` не задан);

Крутите радиус с превью и нажимайте **OK**. `Cancel` в любом диалоге → созданные слои
удаляются (кроме случая `--keep-partial`). Имена слоёв используют фактически выбранные
радиусы (формат как у GIMP: `НЧ (GB 10.0)`).

## Auto-прогон (headless, для чисел)

```bash
export PHOTOCRAFT_CLI=/path/to/squashfs-root/usr/bin/photocraft-cli
PYTHONPATH=src .venv/bin/python scripts/freqautosplit_poc.py \
  --input /path/to/photo.jpg --auto --radius 10
```

Если CLI не найден, скрипт подскажет распаковку AppImage:

```bash
~/soft/artcraft/photocraft-0.10.0-linux-x86_64.AppImage --appimage-extract
export PHOTOCRAFT_CLI=$PWD/squashfs-root/usr/bin/photocraft-cli
```

Результаты: `out/freqsplit-poc/<asset>/native/{composite,low,high}.png` + `manifest.json`
(ассет, sha256, версии, фактические радиусы).

Полезные флаги: `--hp-radius`, `--out`, `--read-root` (по умолчанию каталог `--input`),
`--keep-partial`, `--verbose`.

## Action и `.atn`

```bash
PYTHONPATH=src .venv/bin/python scripts/make_freqautosplit_action.py
```

Скрипт в headless-сессии записывает Action `SHSMAD / Frequency Split (GB+HP)`,
включает диалоги у шагов Blur/High Pass, проверяет воспроизведение на новом
документе и пишет:

- `actions/freqautosplit.atn` — Photoshop Actions (загружается через
  `actions.load` / панель Actions);
- `actions/freqautosplit.action.json` — шаги (`actions.get`) и результат экспорта.

Action переносим между документами: все ссылки на слои — по именам, группа
собирается через `layer.groupLayers`, id не используются. При запуске выберите
**ровно один исходный слой**. Имена слоёв фиксированы на записанный радиус
(`НЧ (GB 10.0)`), даже если в диалоге выбрать другой. В `.atn` пропускается шаг
`locks.pixels` (у Photoshop-формата нет эквивалента) — в нативном Action из JSON он есть.

### Установка в запущенный PhotoCraft

```bash
# загрузить .atn (23 шага, без locks.pixels)
PYTHONPATH=src .venv/bin/python scripts/install_freqautosplit_action.py --atn

# пересобрать нативный Action из JSON (24 шага, с locks.pixels и диалогами)
PYTHONPATH=src .venv/bin/python scripts/install_freqautosplit_action.py --json
```

По умолчанию берутся порт/токен из `.env`; для другого экземпляра — `--port`/`--token-file`.
Действие сохраняется в `~/.config/photocraft/Presets/actions.json` и остаётся после перезапуска.

### Как играть

1. `Window → Actions` (`Alt+F9`), вкладка **Actions** в правом доке.
2. Выберите действие `SHSMAD / Frequency Split (GB+HP)`.
3. В документе выберите **один исходный слой**.
4. Нажмите **Play** (треугольник внизу панели).
5. Откроются диалоги **Gaussian Blur**, затем **High Pass** — подстройте и OK.

`.atn` также можно загрузить из панели Actions: меню панели (≡) → **Load Actions…**.
`action.json` — не формат импорта, а журнал шагов (`actions.get`); его использует `--json`,
и он же служит для воспроизводимости.

## GIMP-эталон

```bash
scripts/run_gimp_reference.sh /path/to/photo.jpg \
  out/freqsplit-poc/<asset>/gimp 10
```

Пишет `gimp/{composite,low,high}.png` и `gimp/manifest.json`. GIMP-плагин работает по
рецепту `Grain Extract/Merge`; LOW и композит сравниваются численно, HIGH — информативно
(кодировки разные, смотреть глазами).

## Сравнение

```bash
.venv/bin/python scripts/compare_images.py \
  --run-dir out/freqsplit-poc/<asset> \
  --input /path/to/photo.jpg
```

Пишет `diff/*.png` (усиление x8), `report.json` и `report.md` с RMSE, PSNR, max|Δ|,
долей |Δ|>2/255 и RMSE по RGB (по непрозрачным пикселям). Жёсткого порога нет.

## Результаты на тестовых ассетах (radius 10)

На `DSC00463_part_before.JPG`, `faceskin_before.png` и полном `DSC00463.JPG`:
LOW/HIGH — RMSE ≈ 0.003–0.008 (PSNR ≈ 41–51 dB), композит — RMSE ≈ 0.003–0.004
(PSNR ≈ 47–51 dB). Реконструкция native-композита к исходнику не хуже GIMP-варианта.
Точные цифры — в `out/freqsplit-poc/<asset>/report.md`.

## Замечания

- `--auto` закрывает документ после экспорта; `--keep-partial` оставляет его для отладки
  (headless-сессия завершится вместе с процессом).
- GIMP может писать в stderr предупреждения GEGL/утечки буфера при выходе — на результат
  не влияют.
- Ничего не коммитится автоматически; `out/` в `.gitignore`.
