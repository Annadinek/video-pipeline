#!/usr/bin/env python3
# config.py — общие постоянные значения конвейера (из CLAUDE.md).
# Здесь НЕТ секретов. Секреты (ключи) читаются из переменных окружения = GitHub Secrets.

import os

# --- YouTube: плейлисты канала «Анна Динэк» (из CLAUDE.md) ---
RAW_PLAYLIST_ID = "PLcnoqDt-qFI4"        # «Клауд сырое» (приватный) — берём отсюда
PROCESSED_PLAYLIST_ID = "PLBCzweWG6vA8"  # «Клауд обработанное»
SHORTS_PLAYLIST_ID = "PLSm9RiW6x7t8"     # «Клауд шортсы рилсы»

# Скрытый плейлист-кандидаты музыки (создаётся один раз music_playlist.py)
MUSIC_CANDIDATES_TITLE = "Клауд музыка кандидаты"

# --- Telegram (из CLAUDE.md) ---
# Личный чат Анны (admin). ID не секрет; токен бота — секрет (env TELEGRAM_BOT_TOKEN).
TELEGRAM_ADMIN_CHAT = int(os.environ.get("TELEGRAM_ADMIN_CHAT", "281187873"))

# --- Telegram РИЛС (Instagram/TikTok) — ОТДЕЛЬНЫЙ бот ---
# Бот @TiktokInstaloop_bot. Токен — секрет INSTA_BOT_TOKEN (Анна добавила в GitHub
# Secrets). Чат тот же admin. YouTube-бот (@dinekanna_bot, TELEGRAM_BOT_TOKEN) НЕ трогаем.
REELS_BOT_TOKEN_ENV = "INSTA_BOT_TOKEN"
REELS_BOT_USERNAME = "TiktokInstaloop_bot"
REELS_ADMIN_CHAT = int(os.environ.get("REELS_ADMIN_CHAT", "281187873"))

# --- Рабочие папки ---
WORK_DIR = os.environ.get("WORK_DIR", "work")     # промежуточные файлы (стираются)
REVIEW_DIR = os.environ.get("REVIEW_DIR", "review")  # мелкие артефакты для проверки (в репозитории)
STATE_FILE = os.environ.get("STATE_FILE", "state.json")


def require_env(name):
    """Вернуть секрет из окружения или понятно упасть, если его нет.
    Обрезаем пробелы/переносы строк — при вставке в GitHub Secrets они часто
    цепляются в конце и ломают ключ (например, Client ID перестаёт распознаваться)."""
    val = os.environ.get(name)
    if val:
        val = val.strip()
    if not val:
        raise SystemExit(
            f"Нет секрета {name}. Добавь его в GitHub Secrets "
            f"(Settings -> Secrets and variables -> Actions)."
        )
    return val
