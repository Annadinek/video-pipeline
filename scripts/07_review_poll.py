#!/usr/bin/env python3
# 07_review_poll.py — проверяет ответ Анны в Telegram (Круг 1).
# Запускается коротким расписанием каждые 5 минут (poll.yml).
# Читает новые сообщения бота, понимает «ОК» или правку, продвигает состояние.
#
# Печатает решение для workflow:
#   PUBLISH:<номер_обложки>  — Анна одобрила, публикуем
#   CORRECTION               — Анна просит исправить (передаём в работу)
#   NONE                     — ответа пока нет / нечего проверять

import re

import config
import state
import tg

OK_WORDS = ("ок", "ok", "окей", "хорошо", "публикуй", "выкладывай", "да")


def parse_decision(text):
    """Вернуть ('publish', номер) или ('correction', None)."""
    low = text.strip().lower()
    first = low.split()[0] if low.split() else ""
    if first in OK_WORDS:
        m = re.search(r"\b([1-3])\b", low)  # номер обложки, если указан
        return "publish", int(m.group(1)) if m else 1
    return "correction", None


def main():
    review = state.get_review()
    if review.get("state") != "awaiting_review":
        print("NONE")
        return

    offset = review.get("last_update_id", 0) + 1
    updates = tg.get_updates(offset=offset)

    decision, thumb, corr_text = "NONE", None, None
    max_update = review.get("last_update_id", 0)
    for u in updates:
        max_update = max(max_update, u["update_id"])
        msg = u.get("message") or u.get("edited_message")
        if not msg or "text" not in msg:
            continue
        if msg.get("chat", {}).get("id") != config.TELEGRAM_ADMIN_CHAT:
            continue
        kind, num = parse_decision(msg["text"])
        if kind == "publish":
            decision, thumb = "publish", num
        else:
            decision, corr_text = "correction", msg["text"]

    # Сдвигаем «прочитано до» всегда, даже если решения не было.
    state.set_review(last_update_id=max_update)

    if decision == "publish":
        state.set_review(state="publishing", chosen_thumb=thumb)
        print(f"PUBLISH:{thumb}")
    elif decision == "correction":
        # ВРЕМЕННО: правки картинки/звука по свободному тексту нельзя применить
        # автоматически (это редактура). Фиксируем правку, сообщаем Анне, передаём
        # в ручную/сессионную обработку. «ОК» — автопубликация работает без меня.
        state.set_review(state="reprocess", correction=corr_text)
        tg.send_message("Приняла правку, передаю в работу. Пришлю исправленную версию.")
        print("CORRECTION")
    else:
        print("NONE")


if __name__ == "__main__":
    main()
