#!/usr/bin/env python3
# yt_hide.py — скрывает (делает приватными) указанные видео на YouTube.
# Нужно, чтобы быстро убрать с канала бракованные ролики, не удаляя их
# насовсем (private обратимо). Список ID — в переменной HIDE_IDS через запятую.

import os

import tg
import yt_ops


def main():
    ids = [x.strip() for x in os.environ.get("HIDE_IDS", "").split(",") if x.strip()]
    if not ids:
        raise SystemExit("Не задан список HIDE_IDS (через запятую).")
    done, failed = [], []
    for vid in ids:
        try:
            yt_ops.set_privacy(vid, "private")
            done.append(vid)
            print("скрыт (private):", vid)
        except Exception as e:
            failed.append((vid, str(e)))
            print("ошибка скрытия", vid, e)
    msg = f"Скрыл с канала (сделал приватными) {len(done)} роликов."
    if failed:
        msg += f" Не вышло у {len(failed)}: " + ", ".join(v for v, _ in failed)
    tg.send_message(msg)
    print(f"ГОТОВО: скрыто {len(done)}, ошибок {len(failed)}.")


if __name__ == "__main__":
    main()
