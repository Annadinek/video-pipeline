#!/usr/bin/env python3
# cleanup_bot.py — убирает из Telegram-бота ВСЕ прежние сообщения (музыка, старые
# ссылки/версии), оставляя только актуальные 3 обложки.
#
# Как: сначала шлём свежие обложки и запоминаем их message_id, потом удаляем все
# сообщения С МЕНЬШИМ id (то есть более старые). Telegram позволяет ботам удалять
# только сообщения не старше 48 часов — что старше, останется (удалить вручную).

import sys
import time

import requests

import config
import tg

TOKEN = config.require_env("TELEGRAM_BOT_TOKEN")
CHAT = config.TELEGRAM_ADMIN_CHAT
API = f"https://api.telegram.org/bot{TOKEN}/"


def delete(mid):
    try:
        r = requests.post(API + "deleteMessage",
                          data={"chat_id": CHAT, "message_id": mid}, timeout=20)
        j = r.json()
        if not j.get("ok") and j.get("error_code") == 429:
            time.sleep(j.get("parameters", {}).get("retry_after", 2) + 1)
            return delete(mid)
        return bool(j.get("ok"))
    except Exception:
        return False


def main():
    # 1) Свежие обложки — их оставляем.
    intro = tg.send_message("Актуальные обложки. Всё старое ниже сейчас уберу.")
    ids = [intro["message_id"]]
    caps = [
        "Обложка 1 — «ЧТО ТАКОЕ СВОБОДА»",
        "Обложка 2 — «ВЫХОД ТИШИНЫ»",
        "Обложка 3 — «ТЫ — НАБЛЮДАТЕЛЬ»",
    ]
    for i, c in enumerate(caps, 1):
        r = tg.send_photo(f"thumbs/thumb{i}.jpg", caption=c)
        ids.append(r["message_id"])

    first = min(ids)
    # 2) Удаляем всё, что старше (id меньше first).
    deleted = 0
    floor = max(1, first - 600)
    mid = first - 1
    while mid >= floor:
        if delete(mid):
            deleted += 1
        mid -= 1
        if mid % 30 == 0:
            time.sleep(0.2)

    tg.send_message(
        f"Готово: убрал старые сообщения ({deleted} шт.). Оставил только эти 3 обложки. "
        "Если что-то осталось — это сообщения старше 48 часов, их боту удалять нельзя, "
        "удали вручную."
    )
    print(f"cleanup_bot: удалено {deleted}")


if __name__ == "__main__":
    main()
