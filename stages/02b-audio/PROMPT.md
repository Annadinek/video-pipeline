# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py --input ... --output ...`. Проблема звука Анны —
не шум, а ПЕРЕКОС СПЕКТРА (эффект близости микрофона: низ раздут, верх провален).
Лечим эквалайзером, не шумодавом. Параметры — в `presets/audio.json`. Цепочка:
1. `highpass` ~100 Гц — срез раздутого низа.
2. (если `top_boost=on`) `equalizer` +5 дБ на ~3 кГц (ширина 2 октавы) — подъём
   верха, разборчивость.
3. `loudnorm` I=-14:LRA=11:TP=-1 — громкость.

Никаких шумодавов (arnndn/afftdn/Demucs) и выреза полос 200–400 Гц — проверено,
для этого голоса они не годятся. Модели arnndn лежат в `presets/rnnoise/`, но в
цепочке НЕ используются. Исходник `clip.mp4` не трогаю.

Режим `--variants` (для подбора): два файла `clip_hp.mp4` (только highpass) и
`clip_hp_eq.mp4` (highpass + подъём верха) на сравнение.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- запись в state/pipeline.json (`current.audio`):
  - audio.duration
  - audio.chain  ("hp" | "hp_eq")
  - audio.spectrum_before / spectrum_after  (уровень по 7 полосам, dB)
  - audio.loudness_before / loudness_after
  - audio.peak_before / peak_after

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
