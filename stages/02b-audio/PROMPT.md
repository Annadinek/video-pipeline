# ЭТАП 02b — ЗВУК
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- outputs/ready/[id]/clip.mp4

## ЧТО ДЕЛАЮ
Запускаю `scripts/audio_clean.py --input ... --output ...`. Цепочка из ЧЕТЫРЁХ
шагов (решение Анны):
1. **DeepFilterNet** — нейросетевой шумодав РЕЧИ (обучен на речи в реальном шуме,
   MIT, бесплатно, на CPU быстро: 30 с звука ≈ 2 с). Модель лежит в репозитории
   (`presets/deepfilternet/DeepFilterNet3/`), скачивать не нужно.
2. **Подмешать исходник обратно** — чтобы вернуть немного воздуха комнаты (иначе
   «тихий подвал», неживо): `amix` 85% очищенного + 15% исходного. Долю исходника
   держим в `presets/audio.json` полем `dry_mix` (умолч. 0.15).
3. **`equalizer=f=4000:width_type=o:width=1.5:g=4`** — узкий возврат верха голоса,
   который DeepFilterNet подрезает (голос «как из трубы»). Узко — чтобы не тянуть
   шипение.
4. `loudnorm` I=-14:LRA=11:TP=-1 — громкость.

Никаких bass/treble/afftdn/arnndn и никакой настройки под тип шума: arnndn
(RNNoise) обучен на телефонном качестве и даёт «колодец». Шум убирает только
DeepFilterNet; здесь equalizer — не шумодав, а узкий возврат верха. Модели arnndn
лежат в `presets/rnnoise/`, но не используются. Исходник `clip.mp4` не трогаю.

DeepFilterNet — не ffmpeg-фильтр, а отдельная программа (`deepFilter`), поэтому
проход: извлекаю звук в wav 48 кГц (он же исходник для подмеса) → `deepFilter`
чистит → одним фильтрографом `amix` (очищенный + исходник) → `equalizer 4000` →
`loudnorm` и возврат дорожки в mp4 (видео копирую). Зависимости ставит workflow:
`deepfilternet==0.5.6`, `torch==2.0.1`, `torchaudio==2.0.2` (свежий torchaudio
ломает импорт).

## ЧТО ОТДАЮ
- outputs/ready/[id]/clip_audio_clean.mp4
- запись в state/pipeline.json (`current.audio`):
  - audio.duration
  - audio.denoise  (модель DeepFilterNet)
  - audio.dfn_seconds  (сколько секунд считал шумодав)
  - audio.dry_mix  (доля подмешанного исходника)
  - audio.noise_floor_before / noise_floor_dfn / noise_floor_after  (фон, dB)
  - audio.band48_before / band48_dfn / band48_after  (верх 4–8 кГц к общему, dB)
  - audio.voice_before  (уровень голоса исходника, LUFS)
  - audio.loudness_final / peak_final

## КОГДА СТОП
- нет ffmpeg → blocked
- две неудачные попытки → blocked, отчёт, идти дальше
