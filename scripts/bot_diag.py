#!/usr/bin/env python3
# bot_diag.py — диагностика бота: какой это бот и кто его «занимает».
# Печатает getMe и getWebhookInfo (это НЕ конфликтует с getUpdates).
# Нужно, чтобы понять, можно ли читать сообщения из существующего бота.

import json

import tg


def main():
    me = tg._call("getMe")
    print("БОТ:", me.get("username"), "| id:", me.get("id"))
    wh = tg._call("getWebhookInfo")
    print("WEBHOOK_INFO:", json.dumps(wh, ensure_ascii=False))
    url = wh.get("url") or ""
    if url:
        print(f"ВЫВОД: на боте стоит ВЕБХУК → {url}")
        print("Пока вебхук стоит, читать через getUpdates нельзя (это его и занимает).")
    else:
        print("ВЫВОД: вебхука НЕТ. Значит бота занимает другой опрос (getUpdates) "
              "какого-то приложения, запущенного постоянно.")
    print("pending_update_count:", wh.get("pending_update_count"))
    print("last_error_message:", wh.get("last_error_message"))


if __name__ == "__main__":
    main()
