# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py --input ... --output ...`. РОВНО ДВЕ ВЕЩИ
(решение Анны):
1. **DeepFilterNet** — нейросетевой шумодав РЕЧИ (обучен на речи в реальном шуме,
   MIT, бесплатно, на CPU быстро: 30 с звука ≈ 2 с). Модель лежит в репозитории
   (`presets/deepfilternet/DeepFilterNet3/`), скачивать не нужно.
2. `loudnorm` I=-14:LRA=11:TP=-1 — громкость.

Никаких bass/treble/equalizer/afftdn/arnndn и никакой настройки под тип шума:
arnndn (RNNoise) обучен на телефонном качестве и даёт «колодец», а полки/вырезы
полос портят голос. DeepFilterNet сам разбирает любой фон. Модели arnndn лежат в
`presets/rnnoise/`, но не используются. Исходник `clip.mp4` не трогаю.

DeepFilterNet — не ffmpeg-фильтр, а отдельная программа (`deepFilter`), поэтому
проход: извлекаю звук в wav 48 кГц → `deepFilter` чистит → `loudnorm` и возврат
дорожки в mp4 (видео копирую). Зависимости ставит workflow: `deepfilternet==0.5.6`,
`torch==2.0.1`, `torchaudio==2.0.2` (свежий torchaudio ломает импорт).

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- запись в state/pipeline.json (`current.audio`):
  - audio.duration
  - audio.denoise  (модель DeepFilterNet)
  - audio.dfn_seconds  (сколько секунд считал шумодав)
  - audio.noise_floor_before / noise_floor_after  (фон в тихом месте, dB)
  - audio.voice_before / voice_after  (уровень голоса, LUFS)
  - audio.loudness_final / peak_final

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
