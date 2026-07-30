#!/usr/bin/env python3
# music_playlist.py — складывает КАНДИДАТОВ музыки в скрытый плейлист на канале Анны,
# чтобы она послушала на YouTube и выбрала (её решение из этапа настройки).
# И спокойные, и тёмные/страшные — оба настроения.
#
# Список треков берём из music/candidates.txt (по одному YouTube video ID в строке,
# после # — описание). НЕ используем search.list в цикле (дорого по квоте) —
# кладём известные, ПРОВЕРЕННЫЕ ID. Треки должны быть лицензированы так, чтобы
# YouTube не блокировал их при загрузке видео (Content-ID-safe / бесплатная фонотека).

import os

import config
import tg
import yt_auth

CANDIDATES_FILE = os.path.join("music", "candidates.txt")


def find_or_create_playlist(title):
    yt = yt_auth.get_service()
    # Ищем среди своих плейлистов по названию.
    page = None
    while True:
        resp = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page).execute()
        for it in resp.get("items", []):
            if it["snippet"]["title"] == title:
                return it["id"]
        page = resp.get("nextPageToken")
        if not page:
            break
    # Не нашли — создаём скрытый (unlisted).
    created = yt.playlists().insert(
        part="snippet,status",
        body={"snippet": {"title": title, "description": "Кандидаты фоновой музыки для роликов"},
              "status": {"privacyStatus": "unlisted"}},
    ).execute()
    return created["id"]


def read_candidates():
    ids = []
    if not os.path.exists(CANDIDATES_FILE):
        return ids
    for line in open(CANDIDATES_FILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line.split("#")[0].strip())
    return ids


def main():
    ids = read_candidates()
    if not ids:
        raise SystemExit(
            f"Список кандидатов пуст. Заполни {CANDIDATES_FILE} проверенными YouTube video ID "
            f"(спокойные + тёмные). Я это делаю на этапе настройки."
        )
    yt = yt_auth.get_service()
    pl = find_or_create_playlist(config.MUSIC_CANDIDATES_TITLE)
    for vid in ids:
        try:
            yt.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": pl,
                                  "resourceId": {"kind": "youtube#video", "videoId": vid}}},
            ).execute()
        except Exception as e:
            print(f"Не добавил {vid}: {e}")
    link = f"https://www.youtube.com/playlist?list={pl}"
    tg.send_message(
        "Музыка на выбор (и спокойная, и тёмная) — послушай в плейлисте:\n"
        f"{link}\n\nНапиши номера, которые нравятся (например: 2 и 5)."
    )
    print("Плейлист музыки:", link)


if __name__ == "__main__":
    main()
