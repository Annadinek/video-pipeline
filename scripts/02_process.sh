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

echo "===== Шаг 2: вырезка пауз ВРЕМЕННО ОТКЛЮЧЕНА ====="
# ВРЕМЕННО: auto-editor делает медленное полное пересжатие и зависал у 100%.
# Пока пропускаем — работаем на исходнике, конвейер становится быстрым и надёжным.
# TODO: вернуть вырезку пауз как список кусков внутри одной команды ffmpeg (без
# отдельного пересжатия) — так и требует CLAUDE.md (одно пересжатие).
CUT="$RAW"

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

# --- Собираем видео-цепочку (стабилизация -> резкость -> [цвет] -> субтитры) ---
# Ретушь кожи (smartblur) убрана — она замыливала лицо. Резкость усилена.
VCHAIN="[0:v]vidstabtransform=input=$TRF:smoothing=30:optzoom=1:zoom=0:crop=black:interpol=linear,\
unsharp=luma_msize_x=7:luma_msize_y=7:luma_amount=2.2:chroma_amount=0.0[pic];"

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
  ACHAIN="[0:a]${DENOISE},highpass=f=80,lowpass=f=12000[voice];\
[1:a]volume=-12dB[musicbase];\
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
