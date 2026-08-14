#!/usr/bin/env python3
# send_covers.py — отправляет готовые обложки в бот Анне на выбор.
# Обложки уже собраны и просмотрены глазами; здесь только отправка.

import sys

import tg

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else "NtX08PXq7i8"
BASE = f"assets/{VIDEO_ID}"

COVERS = [
    ("cover_1.jpg", "Обложка 1: «Зачем мы живём?»"),
    ("cover_2.jpg", "Обложка 2: «В чём смысл жизни?»"),
    ("cover_3.jpg", "Обложка 3: «Опыт — то, что до ума»"),
]


def main():
    tg.send_message(
        "🖼 Три обложки к видео «Зачем мы живём? В чём смысл жизни?».\n"
        "Текст — из твоего названия. Выбери, какая нравится (1 / 2 / 3), "
        "или скажи, что поправить (кадр, текст, крупнее/мельче лицо)."
    )
    for name, cap in COVERS:
        tg.send_photo(f"{BASE}/{name}", caption=cap)
        print(f"{name} отправлена")
    tg.send_message("Жду твой выбор по обложке (1/2/3) и номер по музыке (1–5).")


if __name__ == "__main__":
    main()
