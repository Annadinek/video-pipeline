#!/usr/bin/env python3
# send_audio_from_video.py — берёт ЗВУКОВУЮ ДОРОЖКУ видео КАК ЕСТЬ (без пересжатия,
# без обработки) и шлёт её в бот на прослушку. Аудио не трогаем.
# Запуск: send_audio_from_video.py <url или id>

import glob
import os
import subprocess
import sys

import tg

ARG = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AUDIO_VIDEO", "").strip()


def main():
    if not ARG:
        raise SystemExit("Не задан URL/ID видео.")
    url = ARG if "://" in ARG else f"https://www.youtube.com/watch?v={ARG}"
    os.makedirs("work", exist_ok=True)
    base = ["yt-dlp", "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=web_safari,web,tv",
            "--retries", "5", "--fragment-retries", "5"]
    if os.path.exists("work/cookies.txt"):
        base += ["--cookies", "work/cookies.txt"]
    # только аудио, готовый m4a — БЕЗ пересжатия. Если m4a нет — любой bestaudio.
    cmd = base + ["-f", "ba[ext=m4a]/ba", "-o", "work/audio.%(ext)s", url]
    ok = False
    for a in range(1, 4):
        if subprocess.run(cmd).returncode == 0:
            ok = True
            break
        print(f"скачивание аудио: попытка {a} не удалась")
    files = glob.glob("work/audio.*")
    if not ok or not files:
        tg.send_message("Не смог забрать аудио из видео. Проверь ссылку/доступ.")
        raise SystemExit("audio download failed")
    path = files[0]
    size = os.path.getsize(path) / 1e6
    print(f"аудио: {path} ({size:.1f} МБ)")
    if size > 49:
        tg.send_message(f"Аудиодорожка {size:.0f} МБ — велика для бота. Скажи — пришлю кусок.")
        raise SystemExit("audio too big")
    tg.send_audio(path, title="Образец звука (голос + музыка)",
                  caption="Звук видео как есть, без обработки. Послушай микс: голос + твоё пиано.")
    print("отправлено в бот")


if __name__ == "__main__":
    main()
