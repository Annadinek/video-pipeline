#!/usr/bin/env python3
# fetch_source.py — проверка: можем ли мы скачать большое видео с YouTube
# (через cookies) и где на кадре сидят вшитые субтитры.
# Качает видео, вынимает 3 кадра в source_frames/ и печатает разрешение.
# Нужен секрет YT_COOKIES (экспорт cookies.txt из браузера, где открыт YouTube).

import os
import subprocess

URL = os.environ.get("SRC_URL", "https://www.youtube.com/watch?v=CuAcjA0lWr4")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("$", " ".join(cmd))
    if r.stdout:
        print(r.stdout[-1500:])
    if r.stderr:
        print(r.stderr[-1500:])
    return r


def main():
    os.makedirs("source_frames", exist_ok=True)
    cookies = os.environ.get("YT_COOKIES", "").strip()
    cookie_args = []
    if cookies:
        with open("cookies.txt", "w") as f:
            f.write(cookies if cookies.endswith("\n") else cookies + "\n")
        cookie_args = ["--cookies", "cookies.txt"]
        print("YT_COOKIES найден.")
    else:
        print("YT_COOKIES ПУСТ — вероятен 403.")

    # Сначала показываем, какие форматы вообще доступны (для диагностики).
    run(["yt-dlp", "--no-warnings", *cookie_args, "--list-formats", URL])

    # Перебираем клиентов, которые отдают ПРЯМЫЕ ссылки (web сейчас часто SABR).
    fmt = "bestvideo[height<=1440]+bestaudio/best[height<=1440]/bv*+ba/b/18"
    # creator-клиенты часто отдают потоки для СВОИХ (владельца) видео
    for client in ("web_creator", "android_creator", "ios", "android",
                   "tv_embedded", "mweb", "web"):
        print(f"--- пробую клиент {client} ---")
        r = run(["yt-dlp", "--no-warnings", *cookie_args,
                 "--extractor-args", f"youtube:player_client={client}",
                 "-f", fmt, "--merge-output-format", "mp4",
                 "-o", "src.mp4", URL])
        if os.path.exists("src.mp4"):
            print(f"СКАЧАЛОСЬ клиентом {client}.")
            break
    if not os.path.exists("src.mp4"):
        raise SystemExit("НЕ СКАЧАЛОСЬ ни одним клиентом. Возможно, cookies устарели.")

    # разрешение
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,duration",
                 "-of", "default=noprint_wrappers=1", "src.mp4"])
    # 3 кадра — начало/треть/середина, чтобы поймать субтитры
    dur = 0.0
    for line in (probe.stdout or "").splitlines():
        if line.startswith("duration="):
            try:
                dur = float(line.split("=", 1)[1])
            except ValueError:
                pass
    marks = [max(dur * 0.2, 5), max(dur * 0.4, 10), max(dur * 0.6, 15)] if dur else [30, 90, 150]
    for i, ss in enumerate(marks, 1):
        run(["ffmpeg", "-y", "-ss", f"{ss:.1f}", "-i", "src.mp4",
             "-frames:v", "1", "-q:v", "3", f"source_frames/f{i}.jpg"])
    print("ГОТОВО: кадры в source_frames/.")


if __name__ == "__main__":
    main()
