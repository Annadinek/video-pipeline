# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py`:
1. Паузы. silencedetect: порог −35 dB, пауза от 0.45 с. Вырезаю из видео и звука
   одновременно, оставляя по 0.12 с с каждого края — чтобы не рубить слова.
   Резка пересборкой отрезков, не аудиофильтром: звук и картинка не разъезжаются.
   Пауз не найдено — резку пропускаю, пишу в отчёт, но шум и громкость делаю обязательно.
2. Шум. Сначала arnndn, если модели нет — afftdn. Мягко.
3. Громкость. loudnorm в два прохода до −16 LUFS, пик не выше −1 dB.
Дополнительно делаю `clip_audio_nodenoise.mp4` — та же обработка, но без шумоподавления,
чтобы Анна сравнила на слух.
Исходник `clip.mp4` не трогаю.

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- outputs/ready/[id]/clip_audio_nodenoise.mp4 (для сравнения на слух)
- запись в state/pipeline.json:
  - audio.duration_before
  - audio.duration_after
  - audio.pauses_cut
  - audio.loudness_before
  - audio.loudness_after
  - audio.denoise

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
