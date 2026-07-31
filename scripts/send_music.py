#!/usr/bin/env python3
# send_music.py — присылает Анне в Telegram музыкальные КАНДИДАТЫ, аудио-файлами,
# чтобы можно было слушать прямо в боте. Источник — Pixabay (лицензия Pixabay
# Content License: бесплатно для коммерции, треков НЕТ в системе Content ID —
# YouTube их не блокирует при загрузке).
#
# Как работает: со страницы трека на Pixabay достаём прямую ссылку на mp3
# (тег og:audio) и отправляем через Telegram sendAudio по URL (Telegram сам
# скачивает файл). Если mp3 достать не удалось — присылаем обычную ссылку.

import re

import requests

import tg

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

DARK = [
    ("Тёмный 1 — Eerie Dark Ambience (напряжение)",
     "https://pixabay.com/music/horror-scene-eerie-dark-ambience-for-tension-and-suspense-303395/"),
    ("Тёмный 2 — Dark Horror Ambient (Dark Room)",
     "https://pixabay.com/music/mystery-dark-horror-ambient-dark-room-133815/"),
    ("Тёмный 3 — Horror Tension Background",
     "https://pixabay.com/music/mystery-horror-tension-background-171540/"),
    ("Тёмный 4 — Dark ambient (Horror Soundtrack)",
     "https://pixabay.com/music/horror-scene-dark-ambient-horror-soundtrack-494700/"),
]

CALM = [
    ("Спокойный 5 — Calm Meditation Music",
     "https://pixabay.com/music/ambient-calm-meditation-music-for-relaxation-153307/"),
    ("Спокойный 6 — Mindfulness Relaxation & Meditation",
     "https://pixabay.com/music/ambient-mindfulness-relaxation-amp-meditation-music-22174/"),
    ("Спокойный 7 — Relaxing Ambient Meditation",
     "https://pixabay.com/music/meditationspiritual-relaxing-ambient-meditation-130460/"),
]


def resolve_mp3(page_url):
    """Достаём прямую ссылку на mp3 со страницы трека Pixabay."""
    r = requests.get(page_url, headers=UA, timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(
        r'<meta[^>]+property=["\']og:audio["\'][^>]+content=["\']([^"\']+?\.mp3[^"\']*)',
        html)
    if m:
        return m.group(1).replace("&amp;", "&")
    m = re.search(
        r'(https://cdn\.pixabay\.com/(?:audio|download/audio)/[^"\']+?\.mp3[^"\']*)',
        html)
    if m:
        return m.group(1).replace("&amp;", "&")
    return None


def main():
    tg.send_message(
        "Музыкальные кандидаты. Послушай прямо тут — нажимай ▶ на каждом файле.\n"
        "Все треки с Pixabay: бесплатные, и YouTube их не блокирует при загрузке.\n\n"
        "Тёмные/страшные — 1–4. Спокойные — 5–7.\n\n"
        "Напиши мне номера, которые понравились (можно несколько, из обеих групп) — "
        "я их скачаю и буду накладывать. Меня об этом больше спрашивать не буду."
    )
    ok = 0
    for title, page in DARK + CALM:
        mp3 = None
        try:
            mp3 = resolve_mp3(page)
        except Exception as e:
            print(f"{title}: не смог открыть страницу ({e})")
        if mp3:
            try:
                tg.send_audio(mp3, title=title, caption=title)
                print(f"{title}: отправлен аудио-файлом")
                ok += 1
                continue
            except Exception as e:
                print(f"{title}: sendAudio по URL не прошёл ({e}) — шлю ссылку")
        tg.send_message(f"{title}\n{page}")
    print(f"send_music: аудио-файлами отправлено {ok} из {len(DARK) + len(CALM)}")


if __name__ == "__main__":
    main()
