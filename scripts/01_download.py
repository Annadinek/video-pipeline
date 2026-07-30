#!/usr/bin/env python3
# 01_download.py — скачивает сырое видео с YouTube через yt-dlp.
# Берём в работу ТОЛЬКО видео из приватного плейлиста «Клауд сырое».
# Всё, что вне плейлиста, не трогаем никогда.

import os
import subprocess
import sys

import state

# Плейлист исходников «Клауд сырое» (приватный) — из CLAUDE.md.
RAW_PLAYLIST_ID = "PLcnoqDt-qFI4"

WORK_DIR = os.environ.get("WORK_DIR", "work")


def download(video_id):
    """Скачивает одно видео в максимальном доступном качестве в work/00_raw.*"""
    os.makedirs(WORK_DIR, exist_ok=True)
    out_template = os.path.join(WORK_DIR, "00_raw.%(ext)s")

    if state.is_done(video_id, "01_download"):
        print(f"01_download: видео {video_id} уже скачано, пропускаем")
        return

    # Разрешение исходника всегда 1080p30 (решение из CLAUDE.md: съёмка через QuickTake).
    # -f bv*+ba/b — лучшее видео+звук. Качество не уменьшаем.
    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", out_template,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    # ВРЕМЕННО: приватный плейлист требует авторизации (cookies или oauth).
    # Пока запускаем по одному video_id вручную/из бота. Автоматический обход
    # плейлиста «Клауд сырое» подключим на живом тесте, когда будут ключи.
    print("Запускаю:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    state.mark_done(video_id, "01_download")
    print("01_download: готово")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: 01_download.py <youtube_video_id>")
        sys.exit(2)
    download(sys.argv[1])
