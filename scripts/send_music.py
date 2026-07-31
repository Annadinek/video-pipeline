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
DARK = [
    ("Тёмный 1 — Ossuary 5: Rest (мрачный эмбиент)", "Ossuary 5 - Rest"),
    ("Тёмный 2 — Dark Times (зловещий)", "Dark Times"),
    ("Тёмный 3 — Anguish (напряжение, хоррор)", "Anguish"),
    ("Тёмный 4 — Darkling (тревожный)", "Darkling"),
]
CALM = [
    ("Спокойный 5 — Healing (тихий, обволакивающий)", "Healing"),
    ("Спокойный 6 — Meditation Impromptu 03", "Meditation Impromptu 03"),
    ("Спокойный 7 — Peace of Mind", "Peace of Mind"),
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
        "Музыкальные кандидаты — слушай прямо тут, нажимай ▶ на каждом файле.\n"
        "Музыка бесплатная (Kevin MacLeod), YouTube при загрузке её НЕ блокирует. "
        "Нужную строчку-подпись автора в описание я добавлю сам — тебе ничего делать не надо.\n\n"
        "Тёмные/страшные — 1–4. Спокойные — 5–7.\n\n"
        "Напиши номера, которые понравились (можно несколько из обеих групп) — "
        "их и буду накладывать. Больше про музыку спрашивать не буду."
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
