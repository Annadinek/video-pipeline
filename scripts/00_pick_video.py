#!/usr/bin/env python3
# 00_pick_video.py — выбирает САМОЕ СТАРОЕ ещё не обработанное видео
# из приватного плейлиста «Клауд сырое». Приватный плейлист виден только
# через авторизованный YouTube API (yt-dlp его не покажет).

import sys

import config
import state
import yt_auth


def list_playlist_items(video_playlist_id):
    """Все элементы плейлиста с датой публикации видео (пагинация по 50)."""
    yt = yt_auth.get_service()
    items = []
    page_token = None
    while True:
        resp = yt.playlistItems().list(
            part="snippet,contentDetails,status",
            playlistId=video_playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for it in resp.get("items", []):
            vid = it["contentDetails"]["videoId"]
            # Дата публикации самого видео (по ней сортируем «от самого старого»).
            published = it["contentDetails"].get("videoPublishedAt") \
                or it["snippet"].get("publishedAt", "")
            title = it["snippet"].get("title", "")
            items.append({"video_id": vid, "published": published, "title": title})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def pick_oldest():
    """Самое старое видео, которое ещё не помечено обработанным. Или None."""
    items = list_playlist_items(config.RAW_PLAYLIST_ID)
    # По возрастанию даты — самое раннее первым.
    items.sort(key=lambda x: x["published"])
    for it in items:
        if not state.is_done(it["video_id"], "12_cleanup"):
            return it
    return None


if __name__ == "__main__":
    it = pick_oldest()
    if not it:
        print("", end="")  # нет новых видео — пустой вывод
        sys.exit(0)
    # Для bash: печатаем "video_id<TAB>title"
    print(f"{it['video_id']}\t{it['title']}")
