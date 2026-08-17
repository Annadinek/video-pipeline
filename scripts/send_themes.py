#!/usr/bin/env python3
# send_themes.py — отправляет файл с еженедельными темами Анне в бот кусками
# (Telegram ограничивает сообщение ~4096 символами). Режем по блокам-разделителям
# «═══» и по пустым строкам, чтобы каждый блок пришёл целым и читаемым.
#
# Env: THEMES_FILE (путь к .md с темами).

import os

import tg

LIMIT = 3800  # запас под лимит Telegram 4096


def chunks(text):
    # Сначала делим по крупным блокам (разделители из «═»), потом при нужде дробим.
    blocks, buf = [], []
    for line in text.splitlines():
        if set(line.strip()) == {"═"} and buf:
            blocks.append("\n".join(buf).strip())
            buf = []
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf).strip())

    out = []
    for b in blocks:
        if not b:
            continue
        if len(b) <= LIMIT:
            out.append(b)
            continue
        # блок велик — режем по абзацам (пустая строка), затем по строкам
        cur = ""
        for para in b.split("\n\n"):
            piece = (cur + "\n\n" + para).strip() if cur else para
            if len(piece) <= LIMIT:
                cur = piece
            else:
                if cur:
                    out.append(cur)
                if len(para) <= LIMIT:
                    cur = para
                else:
                    for ln in para.splitlines():
                        if len(cur) + len(ln) + 1 > LIMIT:
                            out.append(cur); cur = ln
                        else:
                            cur = (cur + "\n" + ln).strip() if cur else ln
        if cur:
            out.append(cur)
    return [c for c in out if c.strip()]


def main():
    path = os.environ.get("THEMES_FILE", "content/weekly_themes.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    parts = chunks(text)
    for i, part in enumerate(parts, 1):
        tg.send_message(part)
        print(f"отправлен кусок {i}/{len(parts)} ({len(part)} символов)")
    print(f"ГОТОВО: {len(parts)} сообщений в бот.")


if __name__ == "__main__":
    main()
