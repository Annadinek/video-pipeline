#!/usr/bin/env bash
# setup.sh — готовит машину GitHub Actions к работе.
# Ставит ffmpeg и python-инструменты конвейера. Запускается один раз в начале задачи.
set -euo pipefail

echo "===== Проверка свободного места ДО установки ====="
df -h . | tail -1

echo "===== Ставим ffmpeg ====="
sudo apt-get update -qq
# ffmpeg из репозитория Ubuntu собран с libvidstab (стабилизация) — проверим ниже.
sudo apt-get install -y -qq ffmpeg

echo "===== Проверка ffmpeg и libvidstab ====="
ffmpeg -hide_banner -version | head -1
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q vidstabdetect; then
  echo "OK: стабилизация (libvidstab) на месте"
else
  echo "ВНИМАНИЕ: в этой сборке ffmpeg НЕТ libvidstab — стабилизация работать не будет"
fi

echo "===== Ставим python-инструменты ====="
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet \
  yt-dlp \
  auto-editor \
  faster-whisper

# ВРЕМЕННО: mediapipe нужен только для нарезок (Часть Б). В Части А не ставим,
# чтобы не тратить время и место. Раскомментируем, когда дойдём до Shorts.
# python3 -m pip install --quiet mediapipe

echo "===== Свободное место ПОСЛЕ установки ====="
df -h . | tail -1
echo "setup.sh: готово"
