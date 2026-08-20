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
    # Один файл: SEND_FILE / SEND_CAPTION. Несколько: SEND_FILES (через запятую) и
    # SEND_CAPTIONS (через '||', по порядку). Необязательно: SEND_INTRO — текст перед файлами.
    files_env = os.environ.get("SEND_FILES", "").strip()
    if files_env:
        paths = [p.strip() for p in files_env.split(",") if p.strip()]
        caps = [c.strip() for c in os.environ.get("SEND_CAPTIONS", "").split("||")]
    else:
        one = os.environ.get("SEND_FILE") or (sys.argv[1] if len(sys.argv) > 1 else "")
        paths = [one] if one else []
        caps = [os.environ.get("SEND_CAPTION") or (sys.argv[2] if len(sys.argv) > 2 else "")]
    if not paths:
        raise SystemExit("Не задан SEND_FILE/SEND_FILES.")
    for p in paths:
        if not os.path.exists(p):
            raise SystemExit(f"Файл не найден: {p}")

    me = reels_tg.get_me()
    print("БОТ РИЛС:", me.get("username"), "| id:", me.get("id"))

    intro = os.environ.get("SEND_INTRO", "").strip()
    if intro:
        reels_tg.send_message(intro)

    # Шлём ФАЙЛОМ-ВЛОЖЕНИЕМ (sendDocument) — Telegram не пережимает, качество как есть.
    for i, p in enumerate(paths):
        cap = caps[i] if i < len(caps) else ""
        reels_tg.send_document(p, caption=cap)
        print(f"Отправлено в бот (вложением): {os.path.basename(p)}")


if __name__ == "__main__":
    main()
