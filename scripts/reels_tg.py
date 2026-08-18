#!/usr/bin/env python3
# reels_tg.py — отправка в Telegram-бот РИЛС (@TiktokInstaloop_bot).
# ОТДЕЛЬНЫЙ бот от YouTube: токен в секрете INSTA_BOT_TOKEN, чат тот же (admin).
# YouTube-бот (@dinekanna_bot, TELEGRAM_BOT_TOKEN) здесь НЕ трогаем.
# Значение токена читается ТОЛЬКО из окружения (GitHub Secrets в момент запуска
# Action) — в код секрет не попадает.

import requests

import config

API = "https://api.telegram.org/bot{token}/{method}"


def _token():
    return config.require_env(config.REELS_BOT_TOKEN_ENV)


def _call(method, **params):
    url = API.format(token=_token(), method=method)
    r = requests.post(url, data=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data["result"]


def get_me():
    """getMe — проверить, что это именно бот рилс (@TiktokInstaloop_bot)."""
    return _call("getMe")


def send_message(text, chat_id=None):
    return _call("sendMessage", chat_id=chat_id or config.REELS_ADMIN_CHAT, text=text)


def send_video(path, caption="", chat_id=None):
    """Отправить вертикальный ролик файлом. Лимит Bot API на upload — ~50 МБ."""
    url = API.format(token=_token(), method="sendVideo")
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": chat_id or config.REELS_ADMIN_CHAT, "caption": caption,
                  "supports_streaming": True},
            files={"video": f},
            timeout=300,
        )
    r.raise_for_status()
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(f"Telegram sendVideo: {js}")
    return js["result"]


if __name__ == "__main__":
    me = get_me()
    print("БОТ РИЛС:", me.get("username"), "| id:", me.get("id"))
    send_message("Проверка связи: бот рилс на месте ✅")
    print("Отправлено.")
