# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py --input ... --output ...`. Цепочка (чистим →
громкость), параметры — в `presets/audio.json`:
1. highpass ~90 Гц — срез низов (рокот/гул комнаты).
2. Вырез гулкой полосы ~200–400 Гц (`equalizer` с провалом) — убирает «бочку».
3. Шумодав `afftdn` (мягкий) — остаточный шип.
4. Громкость лёгким ФИКСИРОВАННЫМ гейном (`volume`), не динамическим loudnorm:
   гейн двигает голос и фон одинаково и не поднимает фон. Ограничен так, чтобы
   истинный пик остался ниже `true_peak_db`.

Исходник `clip.mp4` не трогаю. Фон в паузах меряю по окнам на каждом шаге —
видно, где падает. Голос не трогаю: слишком сильный вырез 200–400 делает его
глухим/тонким — тогда откатываю и сообщаю, на каком значении сломалось.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- запись в state/pipeline.json (`current.audio`):
  - audio.duration
  - audio.noise_floor_src / noise_floor_highpass / noise_floor_deboom /
    noise_floor_denoise / noise_floor_final  (фон в тихом месте по шагам)
  - audio.loudness_before / loudness_after
  - audio.peak_before / peak_after
  - audio.gain_db
  - audio.denoise

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
