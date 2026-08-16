#!/usr/bin/env python3
# publish_video.py — выложить видео на канал: заголовок + описание + обложка +
# сделать public. Метаданные меняем у уже загруженного видео (дёшево, без пере-заливки).
#
# Env: VIDEO_ID, VIDEO_TITLE, DESC_FILE (файл с описанием), THUMB (jpg),
#      PRIVACY (public/unlisted), TAGS (через запятую).

import os

import tg
import yt_ops

VIDEO_ID = os.environ["VIDEO_ID"].strip()
TITLE = os.environ.get("VIDEO_TITLE", "").strip()
DESC_FILE = os.environ.get("DESC_FILE", "").strip()
THUMB = os.environ.get("THUMB", "").strip()
PRIVACY = os.environ.get("PRIVACY", "public").strip()
TAGS = [t.strip() for t in os.environ.get("TAGS", "").split(",") if t.strip()]


def main():
    with open(DESC_FILE, encoding="utf-8") as f:
        description = f.read()

    if TITLE:
        yt_ops.update_snippet(VIDEO_ID, TITLE, description, tags=TAGS)
        print("заголовок и описание обновлены")

    if THUMB and os.path.exists(THUMB):
        try:
            yt_ops.set_thumbnail(VIDEO_ID, THUMB)
            print("обложка поставлена")
        except Exception as e:  # noqa: BLE001
            print(f"обложку не поставил: {e}")
            tg.send_message(f"Не смог поставить обложку ({e}). Возможно, нужна "
                            "телефон-верификация канала на youtube.com/verify.")

    yt_ops.set_privacy(VIDEO_ID, PRIVACY)
    link = f"https://youtu.be/{VIDEO_ID}"
    print(f"статус: {PRIVACY} | {link}")
    tg.send_message(f"Видео выложено на канал ({PRIVACY}): {link}\n"
                    "Заголовок, описание (с ботом для записи) и обложка на месте.")


if __name__ == "__main__":
    main()
