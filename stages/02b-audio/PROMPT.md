# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py --input ... --output ...`. Проблема звука Анны —
не шум, а ПЕРЕКОС СПЕКТРА (эффект близости микрофона): горб на низах (~120 Гц)
громче голоса, верх провален. Лечим ПОЛОЧНЫМИ фильтрами (не highpass, не шумодав:
highpass режет ниже среза и горб на 120 Гц не трогает; ровного шума нет).
Параметры — в `presets/audio.json`. Цепочка:
1. `bass` shelf −12 дБ ниже ~200 Гц (ширина 1.2 октавы) — гасит раздутый низ.
2. `treble` shelf +8 дБ выше ~3.5 кГц — поднимает провален­ный верх.
3. `loudnorm` I=-14:LRA=11:TP=-1 — громкость.

Голос (~800 Гц) полки не трогают. Никаких highpass / arnndn / Demucs / узкого
equalizer / выреза полос. Модели arnndn лежат в `presets/rnnoise/`, но не
используются. Исходник `clip.mp4` не трогаю.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- запись в state/pipeline.json (`current.audio`):
  - audio.duration
  - audio.chain  (строка фильтров)
  - audio.spectrum_before / spectrum_after  (уровень по 7 полосам, dB)
  - audio.loudness_before / loudness_after
  - audio.peak_before / peak_after

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
