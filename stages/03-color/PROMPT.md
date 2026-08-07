# ЭТАП 03 — ЦВЕТ И СГЛАЖИВАНИЕ ЛИЦА
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip_stab.mp4
- presets/color.json (настройки), presets/cinema.cube (LUT, если есть)

## ЧТО ДЕЛАЮ
Запускаю `scripts/color.py`:
1. Цвет. Есть LUT `presets/cinema.cube` — крашу по нему (lut3d). Пресета нет —
   базовая коррекция eq (контраст/яркость/насыщенность/гамма из color.json).
2. Сглаживание кожи. bilateral сглаживает по яркости, сохраняя края (глаза,
   волосы, контур не мылит). Уровень off/low/medium/high.
3. Резкость. Лёгкий unsharp после сглаживания — кожа мягкая, детали чёткие.
Разрешение и звук не меняю (-c:a copy). Работаю на 1080p.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_color.mp4
- запись в state/pipeline.json:
  - color.resolution_before / color.resolution_after
  - color.brightness_before / color.brightness_after
  - color.saturation_before / color.saturation_after
  - color.color (LUT или eq)
  - color.skin_smooth
  - color.sharpen

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
