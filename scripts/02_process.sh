#!/usr/bin/env bash
# 02_process.sh — вся обработка картинки и звука длинного видео.
# Вход:  work/00_raw.mp4  (сырое видео из шага 01)
# Выход: work/03_final.mp4 (готовое длинное видео)
#
# Логика по CLAUDE.md:
#   Шаг 2 — вырезаем паузы (auto-editor).
#   Проход 1 — замер тряски (vidstabdetect), быстро, без пересжатия.
#   Проход 2 — ОДНА команда ffmpeg, ОДНО пересжатие: стабилизация -> ретушь ->
#              резкость -> цвет -> шумодав -> музыка -> громкость.
#
# Музыка и цвет (LUT) — НЕОБЯЗАТЕЛЬНЫ: если файла ещё нет (Анна не выбрала),
# соответствующий шаг просто пропускается, видео обрабатывается без него.
set -euo pipefail

WORK_DIR="${WORK_DIR:-work}"
RAW="$WORK_DIR/00_raw.mp4"
CUT="$WORK_DIR/02_cut.mp4"
TRF="$WORK_DIR/transforms.trf"
FINAL="$WORK_DIR/03_final.mp4"

# ---- Выборы этапа настройки (могут быть ещё не сделаны) ----
MUSIC="${MUSIC:-music/ФОНОВАЯ.mp3}"          # ВРЕМЕННО, пока не выбрана — пропускаем
LUT="${LUT:-luts/ТЁПЛЫЙ.cube}"                # ВРЕМЕННО, пока не выбран — пропускаем
DENOISE="${DENOISE:-afftdn=nf=-25}"           # ВРЕМЕННО, лучший выберем на замере
# -----------------------------------------------------------

echo "===== Шаг 2: паузы вырезаны заранее (02_cut_pauses.py) ====="
# Вырезку пауз делает отдельный быстрый шаг 02_cut_pauses.py -> work/02_cut.mp4.
# Здесь просто берём его результат. Если файла нет (шаг пропущен) — работаем на
# исходнике, чтобы конвейер не падал.
if [ -f "$CUT" ]; then
  echo "Беру видео без пауз: $CUT"
else
  echo "02_cut.mp4 нет — работаю на исходнике $RAW"
  CUT="$RAW"
fi

echo "===== Проход 1: замер тряски (vidstabdetect) ====="
ffmpeg -y -i "$CUT" \
  -vf "vidstabdetect=shakiness=8:accuracy=15:result=$TRF" \
  -f null -

# Вшиваемые субтитры (стильные, с подсветкой слов), если готовы (шаг 05).
ASS="review/subs.ass"
if [ -f "$ASS" ]; then
  echo "Субтитры: вшиваю $ASS (по центру, без плашки, с подсветкой слов)"
  SUBFILTER="subtitles=$ASS,"
else
  echo "Субтитры: файл $ASS не найден — вшивать нечего"
  SUBFILTER=""
fi

# --- Собираем видео-цепочку (стабилизация -> резкость -> тон -> [цвет] -> субтитры) ---
# ПРОТИВ «ОТЁКА ЛИЦА»: прошлый вариант сильно тянул unsharp (1.6+1.4) — вокруг
# лица появлялись светлые ореолы, из-за них лицо выглядело припухшим. Теперь:
#  - cas (Contrast Adaptive Sharpen) — резкость БЕЗ ореолов, если фильтр доступен;
#  - лёгкий unsharp — вернуть детали глаз/бровей, но без «пластики»;
#  - eq — чуть больше контраста и чуть темнее: лицо перестаёт быть «отёкшим»,
#    черты собираются, картинка выглядит чётче.
if ffmpeg -hide_banner -filters 2>/dev/null | awk '{print $2}' | grep -qx cas; then
  echo "Резкость: cas + лёгкий unsharp (без ореолов, против отёка)"
  SHARP="cas=strength=0.7,unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.9:chroma_amount=0.0"
else
  echo "Резкость: unsharp (cas недоступен)"
  SHARP="unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=1.1:chroma_amount=0.0,unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.9:chroma_amount=0.0"
fi
TONE="eq=contrast=1.07:saturation=1.05:brightness=-0.015"

VCHAIN="[0:v]vidstabtransform=input=$TRF:smoothing=30:optzoom=1:zoom=0:crop=black:interpol=bicubic,\
$SHARP,\
$TONE[pic];"

if [ -f "$LUT" ]; then
  echo "Цвет: применяю LUT $LUT (на 50%)"
  VCHAIN="$VCHAIN [pic]split[base][tolut];[tolut]lut3d=file='$LUT'[lutted];[base][lutted]blend=all_mode=normal:all_opacity=0.5,${SUBFILTER}format=yuv420p[vout]"
else
  echo "Цвет: LUT ещё не выбран ($LUT нет) — пропускаю цветокоррекцию"
  VCHAIN="$VCHAIN [pic]${SUBFILTER}format=yuv420p[vout]"
fi

echo "===== Проход 2: одна команда — вся картинка и звук ====="
if [ -f "$MUSIC" ]; then
  echo "Музыка: подмешиваю $MUSIC под голос"
  # Музыка чуть громче (Анна просила): базовый уровень -9 dB (было -12). Под голосом
  # она всё равно приглушается сайдчейном, в паузах слышна заметнее.
  ACHAIN="[0:a]${DENOISE},highpass=f=80,lowpass=f=12000[voice];\
[1:a]volume=-9dB[musicbase];\
[musicbase][voice]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=300:makeup=2:level_sc=1[duck];\
[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
  ffmpeg -y -i "$CUT" -stream_loop -1 -i "$MUSIC" \
    -filter_complex "$VCHAIN;$ACHAIN" \
    -map "[vout]" -map "[aout]" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k \
    "$FINAL"
else
  echo "Музыка: ещё не выбрана ($MUSIC нет) — обрабатываю только голос (громче и ближе)"
  # equalizer +5 dB ~3 кГц — «присутствие», голос ближе; acompressor выравнивает и
  # поднимает уровень; loudnorm I=-12 — заметно громче (было -14).
  ACHAIN="[0:a]${DENOISE},highpass=f=90,lowpass=f=12000,\
equalizer=f=3000:t=q:w=1.5:g=5,\
acompressor=threshold=0.08:ratio=3:attack=15:release=180:makeup=2,\
loudnorm=I=-12:TP=-1.5:LRA=9[aout]"
  ffmpeg -y -i "$CUT" \
    -filter_complex "$VCHAIN;$ACHAIN" \
    -map "[vout]" -map "[aout]" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k \
    "$FINAL"
fi

echo "02_process.sh: готово -> $FINAL"
