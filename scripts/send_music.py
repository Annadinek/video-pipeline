#!/usr/bin/env python3
# send_music.py — присылает Анне в Telegram музыкальные КАНДИДАТЫ аудио-файлами,
# чтобы слушать прямо в боте.
#
# Источник — Scott Buckley (scottbuckley.com.au). Кинематографичная музыка в духе
# Ханса Циммера. Лицензия Creative Commons BY 4.0: бесплатно, в том числе для
# коммерции; условие — строка-подпись автора в описании (её конвейер добавит сам).
# Важно для Анны: YouTube эту музыку при загрузке НЕ блокирует и, в отличие от
# библиотек с Content ID, не вешает претензию.
#
# Почему не Pixabay/чужие треки: Pixabay блокирует скачивание с серверов (403),
# а «красивые» чужие треки (Zimmer, Øneheart и т.п.) — защищены авторским правом
# и ловят претензию/блок. Scott Buckley качается напрямую и чист по правам.

import os
import tempfile

import requests

import tg

# Заголовки «как из браузера» — иначе сервон отдаёт 406 на прямую ссылку mp3.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124 Safari/537.36"),
    "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.scottbuckley.com.au/library/",
}

# (подпись, прямая ссылка на mp3)
TRACKS = [
    ("Кино 1 — Echoes of Home (эмоциональная, кинематографичная)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2025/05/EchoesOfHome.mp3"),
    ("Кино 2 — With These Hands (драматичная, нарастает — дух Zimmer)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2026/06/WithTheseHands.mp3"),
    ("Кино 3 — Memories of Stone (эпичная, кинематографичная)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2026/02/MemoriesOfStone.mp3"),
    ("Кино 4 — Home Was You (печальная, тёплая)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2026/07/HomeWasYou.mp3"),
    ("Тёмный 5 — Penumbra (тёмная, атмосферная)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2025/07/Penumbra.mp3"),
    ("Тёмный 6 — Aphelion (холодный космический эмбиент)",
     "https://www.scottbuckley.com.au/library/wp-content/uploads/2026/04/Aphelion.mp3"),
]


def download(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".mp3")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


def main():
    tg.send_message(
        "Новая подборка — максимально близко к твоему звуку: кинематографично, "
        "эмоционально, в духе Циммера/Interstellar, плюс тёмное. Композитор Scott "
        "Buckley. Слушай прямо тут, ▶ на каждом файле.\n\n"
        "Это ГЛАВНОЕ: музыка бесплатная и для коммерции тоже, YouTube её НЕ блокирует "
        "и НЕ вешает претензию (в отличие от твоих треков и библиотек типа "
        "Soundridemusic). Строчку-подпись автора в описание добавлю сам.\n\n"
        "Кинематографичные/эмоциональные — 1–4. Тёмные/атмосферные — 5–6.\n\n"
        "Напиши номер того, что зашло, — наложу на видео."
    )
    ok = 0
    for title, url in TRACKS:
        try:
            path = download(url)
            tg.send_audio(path, title=title, caption=title)
            os.remove(path)
            print(f"{title}: отправлен аудио-файлом")
            ok += 1
        except Exception as e:
            print(f"{title}: не удалось ({e}) — шлю ссылку")
            tg.send_message(f"{title}\n{url}")
    print(f"send_music: аудио-файлами отправлено {ok} из {len(TRACKS)}")


if __name__ == "__main__":
    main()
