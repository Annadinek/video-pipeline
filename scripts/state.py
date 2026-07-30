#!/usr/bin/env python3
# state.py — «закладка» конвейера.
# Хранит в state.json, какие шаги уже сделаны для какого видео.
# После сбоя конвейер смотрит сюда и продолжает с места остановки,
# а не начинает час работы заново.

import json
import os
import sys

STATE_FILE = os.environ.get("STATE_FILE", "state.json")


def _load():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_done(video_id, step):
    """Шаг уже сделан для этого видео?"""
    data = _load()
    return step in data.get(video_id, {}).get("done", [])


def mark_done(video_id, step):
    """Отметить шаг как выполненный."""
    data = _load()
    entry = data.setdefault(video_id, {"done": []})
    if step not in entry["done"]:
        entry["done"].append(step)
    _save(data)


# --- Состояние проверки в Telegram (Круг 1) ---
# Отдельный раздел "_review" в том же state.json. Нужен, потому что машина
# GitHub Actions стирается между запусками, а «на какой стадии проверка» и
# «какое последнее сообщение бота мы прочитали» надо помнить между запусками.

_REVIEW_KEY = "_review"


def get_review():
    """Текущее состояние проверки. По умолчанию — idle (нечего проверять)."""
    data = _load()
    return data.get(_REVIEW_KEY, {
        "state": "idle",          # idle | awaiting_review | reprocess | publishing
        "source_video_id": None,  # исходное сырое видео
        "review_video_id": None,  # unlisted-копия на YouTube (её публикуем)
        "last_update_id": 0,      # последнее прочитанное сообщение Telegram
        "correction": None,       # текст правки от Анны, если попросила исправить
    })


def set_review(**fields):
    """Обновить поля состояния проверки."""
    data = _load()
    review = data.get(_REVIEW_KEY, {})
    review.update(fields)
    data[_REVIEW_KEY] = review
    _save(data)


if __name__ == "__main__":
    # Использование из bash:
    #   python3 state.py is-done <video_id> <step>   -> код выхода 0 если сделан
    #   python3 state.py mark-done <video_id> <step>
    #   python3 state.py review-state                -> печатает текущую стадию
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "is-done":
        sys.exit(0 if is_done(sys.argv[2], sys.argv[3]) else 1)
    elif cmd == "mark-done":
        mark_done(sys.argv[2], sys.argv[3])
    elif cmd == "review-state":
        print(get_review().get("state", "idle"))
    else:
        print("usage: state.py {is-done|mark-done|review-state} ...")
        sys.exit(2)
