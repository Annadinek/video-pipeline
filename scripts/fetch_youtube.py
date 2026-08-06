#!/usr/bin/env python3
"""
fetch_youtube.py — скачать отрезок видео с YouTube через yt-dlp.

Куки берутся из секрета YT_COOKIES (GitHub Secrets):
  - пишутся во временный файл с правами 600,
  - передаются в yt-dlp через --cookies,
  - файл удаляется после работы (в любом случае),
  - содержимое кук в логи и в чат не выводится никогда
    (печатаем только размер, число строк и похоже ли на формат Netscape).

При неудаче показываем ПОЛНЫЙ вывод yt-dlp (stdout и stderr, последние 40 строк)
и запускаем yt-dlp в подробном режиме (--verbose), чтобы было видно, на каком
шаге отказ. Классификатор ошибок смотрит на ВЕСЬ вывод, а не на последнюю строку.

Различаем два случая:
  - куки не читаются / неверный формат  → одно сообщение;
  - YouTube отказал в доступе (протухли / не тот аккаунт / бот-проверка)
    → строка "YT_COOKIES expired или невалидны" и инструкция по обновлению.

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

# YouTube отказал в доступе: протухшие куки, не тот аккаунт, бот-проверка.
AUTH_PATTERNS = [
    "confirm you", "not a bot", "sign in to confirm", "sign in if",
    "sign in to", "login required", "this video is private", "private video",
    "members-only", "join this channel", "http error 401", "http error 403",
]
# Куки не читаются или в неверном формате (это про сам файл кук, не про доступ).
COOKIE_FORMAT_PATTERNS = [
    "does not look like a netscape", "netscape format", "could not be read",
    "error importing cookies", "unable to load cookies", "no such file",
    "malformed", "cookies file",
]
# DRM — куки не помогут, это отдельный случай.
DRM_PATTERNS = ["drm protected", "drm-protected"]

TAIL_LINES = 40


def refresh_instructions():
    """Пошагово, как Анне самой обновить YT_COOKIES."""
    return (
        "\n"
        "Как обновить куки (в браузере, без программирования):\n"
        "1. Открой YouTube в Chrome под своим аккаунтом (тем, где лежит плейлист).\n"
        "2. Поставь расширение «Get cookies.txt LOCALLY» из Chrome Web Store.\n"
        "3. На вкладке youtube.com нажми расширение → Export → cookies.txt.\n"
        "4. Открой скачанный cookies.txt в Блокноте и скопируй ВЕСЬ текст.\n"
        "5. GitHub → репозиторий video-pipeline → Settings → Secrets and variables →\n"
        "   Actions → секрет YT_COOKIES → Update → вставь текст → Save.\n"
        "6. Запусти workflow заново кнопкой Run workflow.\n"
        "Файл должен начинаться со строки «# Netscape HTTP Cookie File» и содержать\n"
        "строки с полями через табуляцию — расширение выше даёт ровно такой формат.\n"
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


def cookie_report(path):
    """
    Диагностика файла кук БЕЗ вывода содержимого:
    (размер_байт, число_строк, похоже_на_netscape, есть_заголовок, строк_с_табами).
    """
    size = os.path.getsize(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    header = any(
        ln.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File"))
        for ln in lines[:3]
    )
    # строки данных Netscape: 7 полей через табуляцию (6 табов), не комментарий
    tab_lines = sum(1 for ln in lines if not ln.startswith("#") and ln.count("\t") >= 6)
    looks_netscape = header or tab_lines > 0
    return size, len(lines), looks_netscape, header, tab_lines


def tail(text, n=TAIL_LINES):
    """Последние n непустых-в-целом строк текста."""
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:]) if lines else "(пусто)"


def classify_error(text):
    """Вернуть 'cookie_format' | 'drm' | 'auth' | 'other' по ВСЕМУ выводу."""
    low = text.lower()
    if any(p in low for p in COOKIE_FORMAT_PATTERNS):
        return "cookie_format"
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

    # --- пункт 4: диагностика файла кук (без содержимого) ---
    size, nlines, looks_netscape, header, tab_lines = cookie_report(cookie_path)
    print(
        f"Куки: файл создан, {size} байт, {nlines} строк; "
        f"формат Netscape: {'да' if looks_netscape else 'НЕТ'} "
        f"(заголовок: {'есть' if header else 'нет'}, строк с 7 полями: {tab_lines})"
    )

    # пустой/битый/не-Netscape файл кук — это формат, а не отказ доступа.
    # Ловим сразу, не гоняя yt-dlp: пункт 5 — формат ≠ отказ YouTube.
    if size == 0 or nlines == 0 or not looks_netscape:
        try:
            os.remove(cookie_path)
        except OSError:
            pass
        print("YT_COOKIES не читается или в неверном формате "
              "(не похоже на Netscape cookies.txt) — это про сам файл кук, не отказ YouTube.")
        print(refresh_instructions())
        sys.exit(2)

    cmd = [
        "yt-dlp", "--verbose", "--no-playlist",
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
        try:
            os.remove(cookie_path)   # куки удаляем всегда
        except OSError:
            pass

    if proc.returncode == 0 and os.path.exists(args.output):
        print(f"Скачан отрезок {args.seconds} с → {args.output}")
        return

    # --- пункт 1: полный вывод yt-dlp, последние 40 строк каждого потока ---
    print(f"\n=== yt-dlp STDERR (последние {TAIL_LINES} строк) ===")
    print(tail(proc.stderr))
    print(f"\n=== yt-dlp STDOUT (последние {TAIL_LINES} строк) ===")
    print(tail(proc.stdout))
    print(f"\n(yt-dlp завершился с кодом {proc.returncode})")

    # --- пункты 2 и 5: классификация по ВСЕМУ выводу; формат ≠ отказ доступа ---
    whole = (proc.stderr or "") + "\n" + (proc.stdout or "")
    kind = classify_error(whole)
    print()
    if kind == "cookie_format":
        print("YT_COOKIES не читается или в неверном формате (yt-dlp не смог разобрать файл кук).")
        print(refresh_instructions())
        sys.exit(2)
    if kind == "auth":
        print("YT_COOKIES expired или невалидны")
        print(refresh_instructions())
        sys.exit(2)
    if kind == "drm":
        print("Видео защищено DRM — его нельзя скачать никакими куками.")
        print("Возьми ролик без DRM или дай прямой файл.")
        sys.exit(3)
    print("Не удалось скачать видео. Причина — в полном выводе yt-dlp выше "
          "(не из кук и не DRM; смотри строки ERROR).")
    sys.exit(1)


if __name__ == "__main__":
    main()
