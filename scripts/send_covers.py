#!/usr/bin/env python3
# send_covers.py — отправляет готовые обложки в бот Анне на выбор.
# Обложки уже собраны и просмотрены глазами; здесь только отправка.

import sys

import tg

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else "NtX08PXq7i8"
BASE = f"assets/{VIDEO_ID}"

COVERS = [
    ("cover_1.jpg", "Вариант 1 (кадр 35) — «В чём смысл жизни?»"),
    ("cover_2.jpg", "Вариант 2 (кадр 21) — «В чём смысл жизни?»"),
    ("cover_3.jpg", "Вариант 3 (кадр 38) — «В чём смысл жизни?»"),
]


def main():
    tg.send_message(
        "🖼 3 образца обложки (горизонтальные, из твоего видео, взгляд в камеру), "
        "тема «В чём смысл жизни?». Выбери номер (1 / 2 / 3)."
    )
    for name, cap in COVERS:
        tg.send_photo(f"{BASE}/{name}", caption=cap)
        print(f"{name} отправлена")
    tg.send_message("Жду твой выбор по обложке (1/2/3) и номер по музыке (1–5).")


if __name__ == "__main__":
    main()
