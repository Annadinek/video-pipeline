#!/usr/bin/env python3
# music_samples.py — присылает Анне в бот несколько вариантов фоновой музыки
# (сэмплы по 45 сек), чтобы она выбрала. Музыка: драматичная, на низких тонах,
# спокойная. Источник — Kevin MacLeod (incompetech.com), лицензия CC-BY:
# бесплатно, при использовании указываем автора в описании ролика.
#
# Ничего сам не выкладываю. Задача — только прислать варианты на выбор.

import os
import subprocess
import sys

import requests

import tg

WORK = "work"
BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree"

# (номер, название файла у автора, настроение по-русски)
TRACKS = [
    (1, "Long Note Two",          "тёмный низкий дрон, очень спокойный, ровный фон"),
    (2, "Ossuary 1 - A Beginning", "медленный, тёмный, низкий, минимальный — под голос"),
    (3, "Anguish",                "тревожные низкие струны, драматичный, но спокойный"),
    (4, "Echoes of Time v2",      "атмосферный, драматичный, глубокий"),
    (5, "Impact Prelude",         "нарастающее напряжение, низкий, драматичный"),
]


def download(name):
    import urllib.parse
    url = f"{BASE}/{urllib.parse.quote(name)}.mp3"
    dst = os.path.join(WORK, f"src_{name}.mp3".replace(" ", "_"))
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(dst, "wb") as f:
        f.write(r.content)
    return dst


def make_sample(src, num):
    """45-сек сэмпл с 20-й секунды, фейды по краям, ровная громкость."""
    dst = os.path.join(WORK, f"sample_{num}.mp3")
    cmd = [
        "ffmpeg", "-y", "-ss", "20", "-t", "45", "-i", src,
        "-af", "afade=t=in:st=0:d=1.5,afade=t=out:st=43.5:d=1.5,loudnorm=I=-16:TP=-1.5",
        "-c:a", "libmp3lame", "-b:a", "192k", dst, "-loglevel", "error",
    ]
    subprocess.run(cmd, check=True)
    return dst


def main():
    os.makedirs(WORK, exist_ok=True)
    tg.send_message(
        "🎵 Варианты фоновой музыки к видео (сэмплы по 45 сек).\n"
        "Настроение: драматичная, на низких тонах, спокойная.\n"
        "Послушай и ответь НОМЕРОМ (1–5), какой брать. Можно выбрать один.\n"
        "Музыка бесплатная (Kevin MacLeod, лицензия CC-BY — укажем автора в описании)."
    )
    for num, name, mood in TRACKS:
        try:
            src = download(name)
            sample = make_sample(src, num)
            tg.send_audio(
                sample,
                title=f"Вариант {num}",
                caption=f"Вариант {num}: {mood}",
            )
            print(f"вариант {num} ({name}) отправлен")
        except Exception as e:
            print(f"вариант {num} ({name}) не удался: {e}")
    tg.send_message("Готово. Жду твой номер (1–5).")


if __name__ == "__main__":
    sys.exit(main())
