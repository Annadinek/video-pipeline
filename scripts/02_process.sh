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
set -euo pipefail

WORK_DIR="${WORK_DIR:-work}"
RAW="$WORK_DIR/00_raw.mp4"
CUT="$WORK_DIR/02_cut.mp4"
TRF="$WORK_DIR/transforms.trf"
FINAL="$WORK_DIR/03_final.mp4"

# ---- ВРЕМЕННО: заглушки этапа настройки (ещё не выбраны Анной) ----
# Музыка: Анна выбирает из 5 присланных ссылок, я скачаю в music/.
MUSIC="${MUSIC:-music/ФОНОВАЯ.mp3}"          # ВРЕМЕННО, заменить на выбранный трек
# Цвет: выберем на тестовом видео Анны (3 варианта .cube -> она выберет 1).
LUT="${LUT:-luts/ТЁПЛЫЙ.cube}"                # ВРЕМЕННО, заменить на выбранную таблицу
# Шумодав: по правилу ШАГ 3 прогоняем afftdn/arnndn/DeepFilterNet3 и берём лучший
# по объективному замеру. Пока по умолчанию afftdn.
DENOISE="${DENOISE:-afftdn=nf=-25}"           # ВРЕМЕННО, выбрать лучший на живом тесте
# ------------------------------------------------------------------

echo "===== Шаг 2: вырезаем паузы (auto-editor) ====="
# ВРЕМЕННО: auto-editor рендерит отдельный файл. Порог --silent-threshold
# подберём на живом видео так, чтобы не резало дыхание внутри фразы
# (проверка средней длины куска >= 1.5 c — добавим на тесте).
auto-editor "$RAW" --silent-threshold 0.04 --frame-margin 6 \
  --no-open --output "$CUT"

echo "===== Проход 1: замер тряски (vidstabdetect) ====="
# shakiness=8 — под сильную тряску ходьбы; accuracy=15 — максимум.
ffmpeg -y -i "$CUT" \
  -vf "vidstabdetect=shakiness=8:accuracy=15:result=$TRF" \
  -f null -

echo "===== Проход 2: одна команда — вся картинка и звук ====="
# Порядок строгий (нельзя менять): стабилизация -> ретушь -> резкость -> цвет.
# Слежение за лицом на длинном видео НЕ включаем (исходник 1080p, решение из CLAUDE.md).
ffmpeg -y \
  -i "$CUT" \
  -stream_loop -1 -i "$MUSIC" \
  -filter_complex "
    [0:v]vidstabtransform=input=$TRF:smoothing=30:optzoom=1:zoom=0:crop=black:interpol=linear,
         smartblur=luma_radius=3.0:luma_strength=0.40:luma_threshold=20:chroma_radius=3.0:chroma_strength=0.20:chroma_threshold=20,
         unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.80:chroma_amount=0.0[pic];
    [pic]split[base][tolut];
    [tolut]lut3d=file='$LUT'[lutted];
    [base][lutted]blend=all_mode=normal:all_opacity=0.5,format=yuv420p[vout];
    [0:a]${DENOISE},highpass=f=80,lowpass=f=12000[voice];
    [1:a]volume=-12dB[musicbase];
    [musicbase][voice]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=300:makeup=2:level_sc=1[duck];
    [voice][duck]amix=inputs=2:duration=first:normalize=0,
         loudnorm=I=-14:TP=-1.5:LRA=11[aout]
  " \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k \
  "$FINAL"

echo "02_process.sh: готово -> $FINAL"
