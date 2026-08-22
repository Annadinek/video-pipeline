#!/usr/bin/env python3
# notify.py — отправляет содержимое текстового файла в Telegram-бот Анны.
# Нужно, чтобы слать в бот тексты (например, новые темы), а не в чат.
# Telegram не принимает сообщение длиннее 4096 знаков — режем на части.
#
# Запуск: scripts/notify.py <путь-к-файлу.txt|.md>

import os
import sys

import tg

LIMIT = 3900  # запас до предела Telegram 4096
# Медиа-файлы шлём как видео/документ, а не как текст.
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def chunks(text, size):
    """Режем по абзацам, чтобы не рвать посреди строки."""
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size:
            if buf:
                parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        raise SystemExit("Не задан путь к файлу.")
    path = sys.argv[1]
    # Видео-файл → шлём в бот как видео (второй аргумент — подпись, иначе имя файла).
    if os.path.splitext(path)[1].lower() in MEDIA_EXT:
        caption = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(path)
        tg.send_video(path, caption=caption)
        print(f"ГОТОВО: отправил видео в бот: {path}")
        return
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise SystemExit("Файл пустой — нечего отправлять.")
    parts = chunks(text, LIMIT)
    for i, part in enumerate(parts, 1):
        tag = f" ({i}/{len(parts)})" if len(parts) > 1 else ""
        tg.send_message(part + tag)
    print(f"ГОТОВО: отправил в бот {len(parts)} сообщение(й).")


if __name__ == "__main__":
    main()
