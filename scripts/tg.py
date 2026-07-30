#!/usr/bin/env python3
# tg.py — связь с Анной через Telegram-бота @dinekanna_bot.
# Токен бота — секрет (env TELEGRAM_BOT_TOKEN). Вебхук НЕ используем
# (он требует сервера), только обычные HTTP-запросы к Bot API.

import requests

import config

API = "https://api.telegram.org/bot{token}/{method}"


def _token():
    return config.require_env("TELEGRAM_BOT_TOKEN")


def _call(method, **params):
    url = API.format(token=_token(), method=method)
    r = requests.post(url, data=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data["result"]


def send_message(text, chat_id=None):
    return _call("sendMessage", chat_id=chat_id or config.TELEGRAM_ADMIN_CHAT, text=text)


def send_photo(path, caption="", chat_id=None):
    url = API.format(token=_token(), method="sendPhoto")
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": chat_id or config.TELEGRAM_ADMIN_CHAT, "caption": caption},
            files={"photo": f},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()["result"]


def get_updates(offset=None, timeout=0):
    """Новые сообщения боту. offset = last_update_id + 1 (чтобы не читать старые)."""
    return _call("getUpdates", offset=offset or 0, timeout=timeout)


if __name__ == "__main__":
    # Проверка связи: пишет тестовое сообщение Анне.
    send_message("Проверка связи: бот на месте ✅")
    print("Отправлено.")
