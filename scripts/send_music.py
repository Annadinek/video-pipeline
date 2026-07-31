#!/usr/bin/env python3
# send_music.py — присылает Анне в Telegram музыкальные КАНДИДАТЫ аудио-файлами,
# чтобы слушать прямо в боте.
#
# Источник — incompetech.com (Kevin MacLeod). Лицензия Creative Commons BY 4.0:
# бесплатно, в том числе для коммерции; условие — строка-подпись автора в описании
# видео (её конвейер добавит сам, Анне делать ничего не нужно). Главное для Анны:
# такую музыку YouTube при загрузке НЕ блокирует.
#
# Почему не Pixabay: Pixabay отдаёт mp3 только через свою кнопку и блокирует
# скачивание с серверов (403), поэтому автоматика не может достать даже финальный
# выбранный трек. incompetech качается напрямую — подходит для конвейера.

import os
import tempfile
import urllib.parse

import requests

import tg

BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}

# (номер-подпись, имя файла на incompetech)
# Подборка в стиле треков Анны: кинематографично/эмоционально (Zimmer, secrets)
# и тёмно/атмосферно (dark ambient). Все — Kevin MacLeod, CC BY, качаются, YouTube не блокирует.
DARK = [
    ("Кино 1 — Reawakening (эмоциональная, нарастает)", "Reawakening"),
    ("Кино 2 — Ascending the Vale (эпичная, в духе Zimmer)", "Ascending the Vale"),
    ("Кино 3 — Crossing the Chasm (драматичная, нарастает)", "Crossing the Chasm"),
    ("Кино 4 — Heartbreaking (печальная, фортепиано)", "Heartbreaking"),
]
CALM = [
    ("Тёмный 5 — Lightless Dawn (тёмная кинематографичная)", "Lightless Dawn"),
    ("Тёмный 6 — The Descent (тёмная, атмосферная)", "The Descent"),
]


def url_for(name):
    return BASE + urllib.parse.quote(name) + ".mp3"


def download(name):
    r = requests.get(url_for(name), headers=UA, timeout=90)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".mp3")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


def main():
    tg.send_message(
        "Новая подборка — в твоём стиле: кинематографично, эмоционально, темно "
        "(близко к Zimmer / secrets / dark ambient). Слушай прямо тут, ▶ на каждом файле.\n"
        "Всё бесплатное (Kevin MacLeod), YouTube при загрузке НЕ блокирует. "
        "Строчку-подпись автора в описание добавлю сам — тебе делать ничего не надо.\n\n"
        "Кинематографичные/эмоциональные — 1–4. Тёмные/атмосферные — 5–6.\n\n"
        "Напиши номер того, что зашло, — наложу на видео."
    )
    ok = 0
    for title, name in DARK + CALM:
        try:
            path = download(name)
            tg.send_audio(path, title=title, caption=title)
            os.remove(path)
            print(f"{title}: отправлен аудио-файлом")
            ok += 1
        except Exception as e:
            print(f"{title}: не удалось ({e}) — шлю ссылку")
            tg.send_message(f"{title}\n{url_for(name)}")
    print(f"send_music: аудио-файлами отправлено {ok} из {len(DARK) + len(CALM)}")


if __name__ == "__main__":
    main()
