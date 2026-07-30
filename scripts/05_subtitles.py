#!/usr/bin/env python3
# 05_subtitles.py — собирает файл субтитров .srt из transcript.json.
# Для длинного видео на YouTube (YouTube ищет по субтитрам).
# Нарезки получат отдельные ASS-субтитры позже (Часть Б).

import json
import os

import config

WORK_DIR = config.WORK_DIR
TRANSCRIPT = os.path.join(WORK_DIR, "transcript.json")
# Кладём в review/ — маленький файл, он должен пережить стирание машины
# (нужен на шаге публикации в отдельном запуске).
OUT = os.path.join(config.REVIEW_DIR, "subtitles.srt")


def _ts(seconds):
    """Секунды -> формат SRT 00:00:00,000"""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build():
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(config.REVIEW_DIR, exist_ok=True)
    lines = []
    for i, seg in enumerate(data.get("segments", []), start=1):
        lines.append(str(i))
        lines.append(f"{_ts(seg['start'])} --> {_ts(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"05_subtitles: {len(data.get('segments', []))} строк -> {OUT}")


if __name__ == "__main__":
    build()
