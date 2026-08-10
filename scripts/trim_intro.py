#!/usr/bin/env python3
# trim_intro.py — скачивает видео с YouTube, вырезает начало (0..START сек) и
# присылает результат Анне в бот на проверку. Если файл больше лимита бота —
# заливает черновиком unlisted и шлёт ссылку. Ключи только из секретов.
import os
import subprocess

import config
import tg
import yt_ops

VID = os.environ.get("TRIM_VIDEO", "").strip()
START = os.environ.get("TRIM_START", "22").strip()  # сколько секунд отрезать с начала


def main():
    if not VID:
        raise SystemExit("Не задан TRIM_VIDEO.")
    url = VID if "://" in VID else f"https://www.youtube.com/watch?v={VID}"
    os.makedirs("work", exist_ok=True)
    raw = "work/in.mp4"
    cmd = ["yt-dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "--remote-components", "ejs:github",
           "--retries", "5", "--fragment-retries", "5",
           "-o", "work/in.%(ext)s", url]
    if os.path.exists("work/cookies.txt"):
        cmd += ["--cookies", "work/cookies.txt"]
    print("Скачиваю:", " ".join(cmd))
    ok = False
    for attempt in range(1, 4):
        if subprocess.run(cmd).returncode == 0 and os.path.exists(raw):
            ok = True
            break
        print(f"попытка {attempt} не удалась, повтор…")
    if not ok:
        tg.send_message(f"Не смог скачать видео {VID} для обрезки. Проверь доступ по ссылке.")
        raise SystemExit("скачать не удалось")

    out = "work/trimmed.mp4"
    # Точная обрезка с перекодированием (чистое начало ровно с START сек).
    subprocess.run(["ffmpeg", "-y", "-ss", START, "-i", raw,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out],
                   check=True)
    mm = int(int(START) // 60); ss = int(int(START) % 60)
    cap = f"Видео с вырезанным началом (убрано 0:00–{mm}:{ss:02d}). Проверь, дальше скажешь «ок» или что поправить."
    size = os.path.getsize(out) / 1e6
    if size <= 49:
        tg.send_video(out, caption=cap)
        print("отправил файл в бот")
    else:
        vid = yt_ops.upload_video(out, "Черновик — обрезка вступления",
                                  "Черновик для проверки. Не публиковать.", privacy="unlisted")
        tg.send_message(cap + f"\nЧерновик (по ссылке): https://youtu.be/{vid}")
        print(f"залил unlisted https://youtu.be/{vid}")


if __name__ == "__main__":
    main()
