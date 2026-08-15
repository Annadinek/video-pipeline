#!/usr/bin/env python3
# reels_process.py — ОТДЕЛЬНАЯ ветка конвейера для вертикальных роликов
# (Reels / TikTok). НЕ пересекается с YouTube-конвейером: свои файлы с
# приставкой reels_. Vizard здесь НЕ используется — он остаётся только для YouTube.
#
# 🚧 СЕЙЧАС ЭТО ТОЛЬКО КАРКАС — ЖДУ ОБРАЗЦЫ ОТ АННЫ.
# Анна собирает папку с видео-образцами; точное задание с командами ffmpeg
# придёт после. Реальную обработку здесь НЕ пишем — только заготовка.

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET = os.path.join(ROOT, "presets", "reels_process.json")


def load_preset(path=PRESET):
    """Настройки обработки вертикали. Пока пусто — заполнится после образцов."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def process(src, out, preset):
    """Обработка вертикального ролика (Reels / TikTok).

    🚧 ПОКА НЕ РЕАЛИЗОВАНО — жду образцы от Анны и точное задание с командами
    ffmpeg. Здесь появятся конкретные шаги обработки, когда придёт задание.
    """
    raise NotImplementedError(
        "reels_process: обработка ещё не задана — жду образцы от Анны "
        "и точное задание с командами ffmpeg."
    )


def main():
    # Каркас: ничего не обрабатываем, только показываем, что заготовка на месте.
    preset = load_preset()
    print("reels_process: каркас. Жду образцы от Анны — обработка ещё не написана.")
    print(f"пресет: {PRESET} (полей: {len(preset)})")
    # Когда придёт задание: разобрать аргументы (вход/выход) и вызвать process().
    return 0


if __name__ == "__main__":
    sys.exit(main())
