# ЭТАП 02c — СТАБИЛИЗАЦИЯ
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip_audio_clean.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/stabilize.py`:
1. Измеряю тряску (vidstabdetect, ascii-.trf) и печатаю цифрой — среднее покадровое
   смещение камеры в пикселях.
2. Если камера почти не двигалась (тряска ниже skip_below_px) — этап пропускаю,
   файл копирую как есть, БЕЗ пересборки, чтобы не портить картинку. Пишу в отчёт «тряски нет».
3. Иначе стабилизирую два прохода: vidstabdetect → vidstabtransform, настройки из
   presets/stab.json (shakiness, smoothing, zoom, optzoom). Звук сохраняю (-c:a copy).
4. Измеряю остаточную тряску и сколько процентов кадра съедено обрезкой.
Съёмка 1080p/30 — стабилизация зумит внутрь и режет края; показываю это цифрой.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_stab.mp4
- запись в state/pipeline.json:
  - stab.shake_before
  - stab.shake_after
  - stab.crop_percent
  - stab.applied

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
