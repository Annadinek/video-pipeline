#!/usr/bin/env python3
# reels_send.py — отправить один готовый ролик в бот рилс (@TiktokInstaloop_bot).
# Путь и подпись берём из окружения (SEND_FILE / SEND_CAPTION), чтобы не подставлять
# пользовательский ввод в командную строку. Запускается из воркфлоу reels-bot-send.
#
# Печатает username бота (подтверждение, что это именно бот рилс), затем шлёт
# короткое сообщение и сам видео-файл.

import os
import sys

import reels_tg


def main():
    path = os.environ.get("SEND_FILE") or (sys.argv[1] if len(sys.argv) > 1 else "")
    caption = os.environ.get("SEND_CAPTION") or (sys.argv[2] if len(sys.argv) > 2 else "")
    if not path:
        raise SystemExit("Не задан SEND_FILE (путь к видео в репозитории).")
    if not os.path.exists(path):
        raise SystemExit(f"Файл не найден: {path}")

    me = reels_tg.get_me()
    print("БОТ РИЛС:", me.get("username"), "| id:", me.get("id"))

    # Шлём ФАЙЛОМ-ВЛОЖЕНИЕМ (sendDocument) — Telegram не пережимает, качество как есть.
    reels_tg.send_document(path, caption=caption)
    print(f"Отправлено в бот (вложением): {os.path.basename(path)}")


if __name__ == "__main__":
    main()
