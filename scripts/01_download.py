#!/usr/bin/env python3
# 01_download.py — скачивает сырое видео с YouTube через yt-dlp.
# Берём в работу ТОЛЬКО видео из приватного плейлиста «Клауд сырое».
# Всё, что вне плейлиста, не трогаем никогда.
#
# Если ID не передан — сам берём САМОЕ СТАРОЕ ещё не обработанное видео
# из плейлиста (через 00_pick_video.py). Аргумент-ID остаётся как ручной обход.

import importlib
import os
import subprocess
import sys

import config
import state

WORK_DIR = config.WORK_DIR

# Модуль называется 00_pick_video (начинается с цифры) — импортируем через importlib.
pick_mod = importlib.import_module("00_pick_video")


def download(video_id):
    """Скачивает одно видео в максимальном доступном качестве в work/00_raw.mp4"""
    os.makedirs(WORK_DIR, exist_ok=True)
    out_template = os.path.join(WORK_DIR, "00_raw.%(ext)s")

    if state.is_done(video_id, "01_download"):
        print(f"01_download: видео {video_id} уже скачано, пропускаем")
        return

    # Разрешение исходника всегда 1080p30 (решение из CLAUDE.md: съёмка через QuickTake).
    # -f bv*+ba/b — лучшее видео+звук. Качество не уменьшаем.
    # Видео «по ссылке» (unlisted) качается по URL без авторизации.
    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", out_template,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    # Запасной вариант против блокировки с IP GitHub: если задан секрет YT_COOKIES,
    # setup.sh кладёт его в work/cookies.txt, и мы передаём его yt-dlp.
    cookies = os.path.join(WORK_DIR, "cookies.txt")
    if os.path.exists(cookies):
        cmd += ["--cookies", cookies]

    print("Запускаю:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    state.mark_done(video_id, "01_download")
    print("01_download: готово")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1]:
        vid = sys.argv[1]
    else:
        it = pick_mod.pick_oldest()
        if not it:
            print("Новых видео в плейлисте «Клауд сырое» нет.")
            sys.exit(0)
        vid = it["video_id"]
        print(f"Взял самое старое видео: {it['title']} ({vid})")
    download(vid)
