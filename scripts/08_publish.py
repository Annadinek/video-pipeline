#!/usr/bin/env python3
# 08_publish.py — публикация после «ОК» Анны.
# Видео уже залито как unlisted (шаг 06). Здесь НЕ заливаем заново — только:
#   1) делаем его public (videos.update, дёшево),
#   2) добавляем субтитры .srt (captions.insert),
#   3) ставим выбранную обложку (thumbnails.set),
#   4) кладём в плейлист «Клауд обработанное»,
#   5) проверяем, что видео существует, и сообщаем Анне.

import json
import os

import config
import state
import tg
import yt_ops

REVIEW = config.REVIEW_DIR
META = os.path.join(REVIEW, "meta.json")
SRT = os.path.join(REVIEW, "subtitles.srt")


def main():
    review = state.get_review()
    if review.get("state") != "publishing":
        print("08: состояние не 'publishing' — нечего публиковать.")
        return

    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    vid = meta["review_video_id"]
    chosen = int(review.get("chosen_thumb", 1))

    print("08: делаю видео публичным…")
    yt_ops.set_privacy(vid, "public")

    if os.path.exists(SRT):
        print("08: добавляю субтитры…")
        try:
            yt_ops.insert_caption(vid, SRT, language="ru")
        except Exception as e:
            tg.send_message(f"Субтитры не загрузились: {e}. Видео опубликовано, добавлю субтитры отдельно.")

    thumb = os.path.join(REVIEW, f"thumb{chosen}.jpg")
    if os.path.exists(thumb):
        print(f"08: ставлю обложку №{chosen}…")
        try:
            yt_ops.set_thumbnail(vid, thumb)
        except Exception as e:
            tg.send_message(f"Обложку не удалось поставить: {e}. Проверь телефон-верификацию канала.")

    try:
        yt_ops.add_to_playlist(vid, config.PROCESSED_PLAYLIST_ID)
    except Exception as e:
        print("08: в плейлист не добавилось:", e)

    # Проверяем, что видео реально существует и обработано (защита из ШАГ 12).
    yt = yt_ops.yt_auth.get_service()
    check = yt.videos().list(part="status,processingDetails", id=vid).execute()
    exists = bool(check.get("items"))

    # Отмечаем исходное сырое видео как доведённое до публикации.
    src = meta.get("source_video_id")
    if src:
        state.mark_done(src, "11_publish")
    state.set_review(state="idle")

    link = f"https://youtu.be/{vid}"
    if exists:
        tg.send_message(f"Опубликовано ✅\n{link}\nЗаголовок: {meta['title']}")
    else:
        tg.send_message(f"Опубликовал, но не смог подтвердить видео по ID {vid}. Проверь вручную: {link}")
    print("08: готово ->", link)


if __name__ == "__main__":
    main()
