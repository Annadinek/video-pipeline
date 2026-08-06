#!/usr/bin/env python3
"""
fetch_youtube.py — скачать отрезок видео с YouTube через yt-dlp.

Куки берутся из секрета YT_COOKIES (GitHub Secrets):
  - пишутся во временный файл с правами 600,
  - передаются в yt-dlp через --cookies,
  - файл удаляется после работы (в любом случае),
  - содержимое кук в логи и в чат не выводится никогда.

Протухшие/невалидные куки обрабатываются без трассировки:
поймав отказ доступа / требование входа / "confirm you're not a bot",
скрипт печатает ровно строку
    YT_COOKIES expired или невалидны
и пошаговую инструкцию по обновлению кук, затем выходит с кодом 2.

Запуск:
    python scripts/fetch_youtube.py --url <ссылка> --output outputs/ready/ID/clip.mp4 \
        [--start 0:00] [--seconds 120]

Только yt-dlp + стандартная библиотека. Ничего лишнего не качает.
"""

import argparse
import os
import subprocess
import sys
import tempfile

# Признаки, что дело в куках/доступе (протухли или не тот аккаунт).
AUTH_PATTERNS = [
    "confirm you", "not a bot", "sign in to confirm", "sign in if",
    "login required", "this video is private", "private video",
    "members-only", "join this channel", "http error 401",
    "http error 403", "account", "cookies",
]
# DRM — куки не помогут, это отдельный случай.
DRM_PATTERNS = ["drm protected", "drm-protected"]


def refresh_instructions():
    """Пошагово, как Анне самой обновить YT_COOKIES. Печатается при протухших куках."""
    return (
        "\n"
        "Как обновить куки (делается в браузере, без программирования):\n"
        "1. Открой YouTube в Chrome под своим аккаунтом (тем, где лежит плейлист).\n"
        "2. Поставь расширение «Get cookies.txt LOCALLY» из Chrome Web Store.\n"
        "3. На открытой вкладке youtube.com нажми это расширение → Export → cookies.txt.\n"
        "4. Открой скачанный cookies.txt в Блокноте и скопируй ВЕСЬ текст.\n"
        "5. GitHub → репозиторий video-pipeline → Settings → Secrets and variables →\n"
        "   Actions → секрет YT_COOKIES → Update (или New) → вставь текст → Save.\n"
        "6. Запусти этот workflow заново кнопкой Run workflow.\n"
        "Куки живут недолго — если снова протухнут, повтори шаги.\n"
    )


def write_cookie_file():
    """Записать YT_COOKIES во временный файл 600. Вернуть путь или None, если секрета нет."""
    raw = os.environ.get("YT_COOKIES")
    if not raw or not raw.strip():
        return None
    fd, path = tempfile.mkstemp(prefix="ytc_", suffix=".txt")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw)
    return path


def classify_error(text):
    """Вернуть 'auth' | 'drm' | 'other' по тексту ошибки yt-dlp."""
    low = text.lower()
    if any(p in low for p in DRM_PATTERNS):
        return "drm"
    if any(p in low for p in AUTH_PATTERNS):
        return "auth"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--output", required=True, help="куда сохранить clip.mp4")
    ap.add_argument("--start", default="0:00", help="начало отрезка, напр. 0:00")
    ap.add_argument("--seconds", type=int, default=120, help="длина отрезка, сек")
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)

    # конец отрезка = start + seconds (start в секундах или M:SS)
    def to_sec(s):
        parts = [float(x) for x in s.split(":")]
        r = 0.0
        for p in parts:
            r = r * 60 + p
        return r

    start_s = to_sec(args.start)
    end_s = start_s + args.seconds
    section = f"*{start_s:.0f}-{end_s:.0f}"

    cookie_path = write_cookie_file()
    if cookie_path is None:
        print("Секрета YT_COOKIES нет — добавь его в GitHub Secrets.")
        print(refresh_instructions())
        sys.exit(2)

    cmd = [
        "yt-dlp", "--no-warnings", "--no-playlist",
        "--cookies", cookie_path,
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", args.output,
        args.url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        # куки удаляем всегда, что бы ни случилось
        try:
            os.remove(cookie_path)
        except OSError:
            pass

    if proc.returncode == 0 and os.path.exists(args.output):
        print(f"Скачан отрезок {args.seconds} с → {args.output}")
        return

    # разбор ошибки — без сырой трассировки, содержимое кук нигде не участвует
    err = (proc.stderr or "") + (proc.stdout or "")
    kind = classify_error(err)
    if kind == "auth":
        print("YT_COOKIES expired или невалидны")
        print(refresh_instructions())
        sys.exit(2)
    if kind == "drm":
        print("Видео защищено DRM — его нельзя скачать никакими куками.")
        print("Возьми ролик без DRM или дай прямой файл.")
        sys.exit(3)
    # прочее (сеть, неверная ссылка, недоступный формат) — короткая понятная строка
    tail = err.strip().splitlines()[-1] if err.strip() else "неизвестная ошибка"
    print(f"Не удалось скачать видео: {tail}")
    sys.exit(1)


if __name__ == "__main__":
    main()
