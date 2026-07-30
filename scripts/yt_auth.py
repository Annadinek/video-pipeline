#!/usr/bin/env python3
# yt_auth.py — вход в YouTube от имени Анны.
# Собирает «ключ доступа» из трёх секретов и возвращает готовый YouTube API-сервис.
# Эти секреты Анна кладёт в GitHub Secrets один раз (см. НАСТРОЙКА-ДЛЯ-АННЫ.md):
#   YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

import config

# Области доступа: загрузка видео + всё остальное (плейлисты, субтитры, обложки, публикация).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_service():
    """Вернуть авторизованный YouTube Data API v3 сервис."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,  # access-токен обновится автоматически по refresh-токену
        refresh_token=config.require_env("YT_REFRESH_TOKEN"),
        client_id=config.require_env("YT_CLIENT_ID"),
        client_secret=config.require_env("YT_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    # cache_discovery=False — чтобы не сыпало предупреждениями на Actions.
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


if __name__ == "__main__":
    # Быстрая проверка доступа: печатает имя канала.
    yt = get_service()
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise SystemExit("Доступ есть, но канал не найден — проверь, тем ли аккаунтом выдан токен.")
    print("Доступ к каналу:", items[0]["snippet"]["title"])
