# ЭТАП 03 — ЦВЕТ И СГЛАЖИВАНИЕ ЛИЦА
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip_stab.mp4
- presets/color.json (настройки), presets/cinema.cube (LUT, если есть)

## ЧТО ДЕЛАЮ
Запускаю `scripts/color.py`:
1. Измеряю СОДЕРЖИМОЕ кадра через ffmpeg signalstats (не заголовок файла):
   средняя яркость и разброс тонов, насыщенность, пересветы и провалы в тенях.
2. Цвет. Есть LUT `presets/cinema.cube` — крашу по нему (lut3d). Нет — коррекция eq;
   в авто-режиме параметры eq ПОДБИРАЮТСЯ от измерений (тёмный → ярче, плоский →
   контраст, тускло → насыщенность; при пересветах/провалах клиппинг не усиливаю).
3. Сглаживание кожи — OpenCV: Haar-каскад находит лицо, по YCrCb строю маску кожи
   ВНУТРИ лица, bilateralFilter сглаживает только кожу. Глаза, брови, волосы, фон
   не трогаю. Уровень off/low/medium/high.
4. Лёгкая резкость unsharp. Разрешение и звук не меняю.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_color.mp4
- запись в state/pipeline.json (color.*):
  - resolution_before / resolution_after
  - before / after (яркость, разброс тонов, насыщенность, пересветы%, провалы%)
  - color (LUT или подобранный eq), skin_smooth, faces_pct, sharpen

## КОГДА СТОП
- нет ffmpeg или OpenCV → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
