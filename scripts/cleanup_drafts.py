#!/usr/bin/env python3
# cleanup_drafts.py — находит ЧЕРНОВИКИ (unlisted-копии), которые конвейер сам
# заливал Анне на проверку (Круг 1), и по команде удаляет старые, оставляя
# первую (самую раннюю) и, при желании, последнюю.
#
# ЗАЧЕМ. При каждой правке («исправляю») конвейер заливает новую unlisted-копию
# видео. За несколько итераций их накапливается много. Анна просила удалить
# старые, а самую первую оставить на всякий случай.
#
# БЕЗОПАСНОСТЬ (соблюдается строго):
#   - НИКОГДА не трогаем сырые видео из плейлиста «Клауд сырое».
#   - НИКОГДА не трогаем «Клауд обработанное» и «Клауд шортсы рилсы».
#   - Кандидат на удаление = загрузка на канале, которая:
#       (а) unlisted, (б) НЕ входит ни в один защищённый плейлист,
#       (в) совпадает по заголовку с известным черновиком (review_video_id).
#   - Режим по умолчанию — ТОЛЬКО показать список. Ничего не удаляем.
#   - Удаление включается явной командой:  cleanup_drafts.py delete
#     По умолчанию при удалении оставляем И первую, И последнюю копию.
#     Флаг --only-first оставляет ТОЛЬКО первую (удаляет и последнюю тоже).

import sys

import config
import state
import yt_auth

PROTECTED_PLAYLISTS = [
    config.RAW_PLAYLIST_ID,        # «Клауд сырое» — оригиналы Анны
    config.PROCESSED_PLAYLIST_ID,  # «Клауд обработанное»
    config.SHORTS_PLAYLIST_ID,     # «Клауд шортсы рилсы»
]


def _uploads_playlist_id(yt):
    r = yt.channels().list(part="contentDetails", mine=True).execute()
    return r["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _playlist_video_ids(yt, playlist_id):
    ids = set()
    page = None
    while True:
        r = yt.playlistItems().list(
            part="contentDetails", playlistId=playlist_id,
            maxResults=50, pageToken=page,
        ).execute()
        for it in r.get("items", []):
            ids.add(it["contentDetails"]["videoId"])
        page = r.get("nextPageToken")
        if not page:
            break
    return ids


def _all_uploads(yt, uploads_pl):
    items = []
    page = None
    while True:
        r = yt.playlistItems().list(
            part="snippet,contentDetails", playlistId=uploads_pl,
            maxResults=50, pageToken=page,
        ).execute()
        for it in r.get("items", []):
            items.append(it["contentDetails"]["videoId"])
        page = r.get("nextPageToken")
        if not page:
            break
    return items


def _details(yt, ids):
    """privacyStatus + title + дата публикации для списка id (пачками по 50)."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = yt.videos().list(part="status,snippet", id=",".join(chunk)).execute()
        for it in r.get("items", []):
            out[it["id"]] = {
                "privacy": it["status"]["privacyStatus"],
                "title": it["snippet"]["title"],
                "published": it["snippet"]["publishedAt"],
            }
    return out


def find_drafts(yt):
    known_id = state.get_review().get("review_video_id")
    if not known_id:
        print("Не знаю ни одного черновика (review_video_id пуст) — нечего искать.")
        return [], None

    uploads_pl = _uploads_playlist_id(yt)
    all_ids = _all_uploads(yt, uploads_pl)
    det = _details(yt, all_ids)

    known_title = det.get(known_id, {}).get("title")
    if not known_title:
        print(f"Не нашёл известный черновик {known_id} среди загрузок канала.")
        return [], None

    protected = set()
    for pl in PROTECTED_PLAYLISTS:
        protected |= _playlist_video_ids(yt, pl)

    drafts = []
    for vid in all_ids:
        d = det.get(vid, {})
        if vid in protected:
            continue
        if d.get("privacy") != "unlisted":
            continue
        if d.get("title") != known_title:
            continue
        drafts.append({"id": vid, "title": d["title"], "published": d["published"]})

    drafts.sort(key=lambda x: x["published"])  # от самой ранней к поздней
    return drafts, known_title


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    only_first = "--only-first" in sys.argv

    yt = yt_auth.get_service()
    drafts, title = find_drafts(yt)

    if not drafts:
        print("Черновиков для уборки не найдено.")
        return

    print(f"\nЗаголовок черновиков: «{title}»")
    print(f"Всего найдено черновиков (unlisted, не в защищённых плейлистах): {len(drafts)}\n")
    for i, d in enumerate(drafts):
        mark = ""
        if i == 0:
            mark = "  <- САМАЯ ПЕРВАЯ (оставляем)"
        elif i == len(drafts) - 1 and not only_first:
            mark = "  <- последняя (оставляем)"
        print(f"  {i+1}. {d['published']}  https://youtu.be/{d['id']}{mark}")

    # Кого удаляем.
    if only_first:
        to_delete = drafts[1:]
    else:
        to_delete = drafts[1:-1]

    print(f"\nК удалению помечено: {len(to_delete)}")
    for d in to_delete:
        print(f"     удалить -> https://youtu.be/{d['id']}  ({d['published']})")

    if mode != "delete":
        print("\nРежим просмотра. Ничего не удалено. "
              "Для удаления запусти с аргументом: delete")
        return

    if not to_delete:
        print("\nУдалять нечего.")
        return

    print("\n===== УДАЛЕНИЕ =====")
    for d in to_delete:
        try:
            yt.videos().delete(id=d["id"]).execute()
            print(f"удалено: {d['id']}")
        except Exception as e:
            print(f"НЕ удалось удалить {d['id']}: {e}")
    print("Готово.")


if __name__ == "__main__":
    main()
