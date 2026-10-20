# Add Skin Noise PoC (PhotoCraft 0.10)

Недеструктивные режимы добавления текстуры кожи. Исходный слой не меняется: эффект живёт
на отдельном Smart Object, фильтры остаются smart filters и редактируются через
`Layer → Smart Filter`.

## Режимы

- `pore` — алгоритм «поры кожи»: дублирует активный слой → Smart Object → Gaussian
  Noise 3 % → Emboss (angle 130, height 1, amount 70) → Gaussian Blur 0.3 px →
  Overlay, opacity 0.5.
- `texture` — рецепт из `gimp.md`: новый нейтральный серый слой → Smart Object →
  RGB Noise 12 % → Median 2 → RGB Noise 6 % → Median 1 → Overlay.

Плагин `SHSMAD_Process_addskinnoise.py` как режим не используется: он дублирует слой в
`Normal` 100 % и разрушающе заменяет изображение. В нём также баг: blur получает
`amount/100`, а второй проход передаёт `image` вместо слоя.

## Требования

- PhotoCraft 0.10 и `photocraft-cli` (`PHOTOCRAFT_CLI` или AppImage-маунт).
- Для интерактива: GUI с control-портом и токеном (`.env`), открытое изображение,
  выбран **ровно один пиксельный слой**.
- Для `--auto`: GUI не нужен.

## Интерактив

```bash
.venv/bin/python scripts/addskinnoise_poc.py --input /path/to/photo.jpg --mode pore
```

Откроются диалоги шума, Emboss и blur. Для `texture` — шум и Median; второй проход
применится с половиной значений. После этого можно добавить маску:

```bash
--mask black   # чёрная маска, проявлять белой кистью
--mask skin    # автовыделение кожи через select.colorRange skinTones
```

`Cancel` откатывает созданные слои (кроме `--keep-partial`).

## Auto (headless)

```bash
export PHOTOCRAFT_CLI=/path/to/squashfs-root/usr/bin/photocraft-cli
.venv/bin/python scripts/addskinnoise_poc.py --input /path/to/photo.jpg --mode pore --auto
```

Результаты:

```
out/addskinnoise-poc/<asset>/<mode>/native/
  composite.png           # финальный результат (с маской, если задана)
  composite_unmasked.png  # без маски (только при --mask)
  pores.png | texture.png # отдельный слой эффекта
  manifest.json           # параметры, smart filters, версия CLI
```

Повторный запуск с тем же `<mode>` перезаписывает `native/`; для сравнения вариантов
используйте отдельный `--out`.

## Параметры

- `--amount` — RGB Noise, %: `pore` 3, `texture` 12 (второй проход = `/2`).
- `--blur-type` — `gaussian` для `pore`, `median` для `texture`.
- `--blur-size` — `pore` 0.3, `texture` 2 (второй = `/2`).
- `--steps 1|2` — `pore` только 1, `texture` 2.
- `--distribution` — `pore` gaussian, `texture` uniform.
- `--[no-]monochromatic`, `--seed`.
- `--emboss-angle`, `--emboss-height`, `--emboss-amount` — только `pore`.
- `--blend`, `--opacity` — по умолчанию Overlay; opacity 0.5 у `pore`, 1.0 у `texture`.
- `--mask none|black|skin`.
- `--out`, `--read-root`, `--cli`, `--port`, `--token-file`, `--keep-partial`, `--verbose`.

## Дальше

Режимы утверждены, Actions записаны:

- `actions/addskin-pores.atn` — pore с High Pass 0.5 (Emboss не экспортируется в `.atn`);
- `actions/addskin-texture.atn` — texture;
- `actions/addskin-pores-emboss.action.json` — native pore с настоящим Emboss.

Генерация: `scripts/make_addskinnoise_actions.py`. Ограничения `.atn` и repro —
`atn-issue.txt`. Осталось визуально проверить Actions в GUI.

Когда `filter.stylize.emboss` начнёт экспортироваться в `.atn`, `addskin-pores.atn`
нужно пересобрать с настоящим Emboss: в `make_addskinnoise_actions.py` заменить
`pore_steps("highpass")` на `pore_steps("emboss")` для `PORE_NAME` и обновить
`expected_filters`; native JSON останется для старых версий PhotoCraft.
