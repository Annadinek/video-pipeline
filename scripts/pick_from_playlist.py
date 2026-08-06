#!/usr/bin/env python3
"""
pick_from_playlist.py — выбрать следующее необработанное видео из плейлиста «СЫРОЕ».

Читает плейлист через YouTube Data API (авторизация — готовый yt_auth.py,
секреты YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN).
Сортирует по дате публикации, берёт самое старое, ещё не попавшее в
state/history.json. Обработанные второй раз не берёт.

Пока — ТОЛЬКО чтение и вывод списка. Ссылку/ID плейлиста передаём так:
    python scripts/pick_from_playlist.py --playlist <PLAYLIST_ID или ссылка>
или через переменную окружения RAW_PLAYLIST_ID. Если не задано —
берётся config.RAW_PLAYLIST_ID.

Секреты в вывод не печатаются. Результаты не выдумываются: нет доступа —
честно об этом сообщаем и выходим.
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, "state", "history.json")


def parse_playlist_id(value):
    """Из ссылки или голого ID вытащить ID плейлиста."""
    if not value:
        return None
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    return value.strip()


def load_processed_blob(history_path):
    """
    Вернуть строку со всем содержимым history.json (для поиска videoId внутри).
    Обработанным считаем видео, чей id встречается где-либо в записях истории
    (в поле id, source, youtube_id и т.п.). Нет файла — пустая строка.
    """
    if not os.path.exists(history_path):
        return ""
    try:
        with open(history_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    return json.dumps(data, ensure_ascii=False)


def is_processed(video_id, processed_blob):
    """Видео обработано, если его id встречается в истории."""
    return bool(video_id) and video_id in processed_blob


def sort_oldest_first(items):
    """Самое старое — первым. Сортируем по дате публикации (ISO сортируется лексически)."""
    return sorted(items, key=lambda v: v.get("published") or "")


def pick_next(items, processed_blob):
    """Первое по возрастанию даты видео, которого ещё нет в истории."""
    for v in sort_oldest_first(items):
        if not is_processed(v["id"], processed_blob):
            return v
    return None


def fetch_playlist_items(service, playlist_id):
    """Прочитать все видео плейлиста. Вернуть [{id, title, published}]."""
    items = []
    page = None
    while True:
        resp = service.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page,
        ).execute()
        for it in resp.get("items", []):
            cd = it.get("contentDetails", {})
            sn = it.get("snippet", {})
            items.append({
                "id": cd.get("videoId") or sn.get("resourceId", {}).get("videoId"),
                "title": sn.get("title", ""),
                # дата публикации самого видео (не даты добавления в плейлист)
                "published": cd.get("videoPublishedAt") or sn.get("publishedAt", ""),
            })
        page = resp.get("nextPageToken")
        if not page:
            break
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", default=os.environ.get("RAW_PLAYLIST_ID"),
                    help="ID плейлиста или ссылка со ?list=")
    args = ap.parse_args()

    playlist_id = parse_playlist_id(args.playlist)
    if not playlist_id:
        try:
            import config
            playlist_id = config.RAW_PLAYLIST_ID
        except Exception:
            playlist_id = None
    if not playlist_id:
        print("Не задан плейлист. Пришли ссылку на плейлист СЫРОЕ "
              "или ID (аргумент --playlist / переменная RAW_PLAYLIST_ID).")
        sys.exit(2)

    # авторизация — готовый yt_auth.py (секреты не печатаем)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        import yt_auth
        service = yt_auth.get_service()
    except SystemExit:
        raise            # require_env уже сказал, какого секрета нет
    except Exception as e:
        print(f"Нет доступа к YouTube API: {e}")
        sys.exit(1)

    try:
        items = fetch_playlist_items(service, playlist_id)
    except Exception as e:
        print(f"Не удалось прочитать плейлист {playlist_id}: {e}")
        sys.exit(1)

    if not items:
        print(f"Плейлист {playlist_id} пуст или недоступен.")
        sys.exit(0)

    processed = load_processed_blob(HISTORY_PATH)
    ordered = sort_oldest_first(items)

    print(f"Плейлист {playlist_id}: видео {len(ordered)} (от старых к новым)")
    print("-" * 60)
    for v in ordered:
        mark = "обработано" if is_processed(v["id"], processed) else "новое"
        print(f"{v['published'][:10] or '----------'}  {v['id']:<12}  [{mark}]  {v['title']}")
    print("-" * 60)

    nxt = pick_next(items, processed)
    if nxt:
        print(f"Следующее в работу: {nxt['id']}  {nxt['title']}")
        print(f"Ссылка: https://youtu.be/{nxt['id']}")
    else:
        print("Новых необработанных видео нет.")


if __name__ == "__main__":
    main()
