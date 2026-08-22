#!/usr/bin/env python3
# send_photo.py — отправить одну картинку из репозитория в бот Анне.
# Запуск: send_photo.py <путь> ["подпись"]

import sys

import tg


def main():
    path = sys.argv[1]
    caption = sys.argv[2] if len(sys.argv) > 2 else ""
    tg.send_photo(path, caption=caption)
    print("отправлено:", path)


if __name__ == "__main__":
    main()
