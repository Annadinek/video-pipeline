#!/usr/bin/env python3
"""
fetch_youtube.py — скачать отрезок видео с YouTube через yt-dlp.

Куки берутся из секрета YT_COOKIES (GitHub Secrets):
  - пишутся во временный файл с правами 600,
  - передаются в yt-dlp через --cookies,
  - файл удаляется после работы (в любом случае),
  - содержимое кук в логи и в чат не выводится никогда
    (печатаем только размер, число строк и похоже ли на формат Netscape).

Скачивание:
  - запасной выбор качества: bv*[height<=1080]+ba / b[height<=1080] / bv*+ba / b —
    по очереди, не падаем на первом отказе;
  - --remote-components ejs:github — чтобы yt-dlp дотягивал скрипты решателя EJS,
    если их не хватает (n-challenge). JS-движок (Deno) ставится в workflow.

После скачивания файл проверяется через ffprobe: внутри должны быть и видео-,
и аудиодорожка. Файл без звука для обработки бесполезен — тогда завершаемся с ошибкой.

При неудаче:
  - печатается ПОЛНЫЙ вывод yt-dlp (stdout и stderr, последние 40 строк) + код,
    yt-dlp работает в --verbose (виден шаг отказа);
  - классификатор смотрит на ВЕСЬ вывод, различает «куки не читаются/неверный
    формат» и «YouTube отказал в доступе» (YT_COOKIES expired или невалидны);
  - печатается --list-formats, но только строки полного формата (видео+звук).

Только yt-dlp + стандартная библиотека.
"""

import argparse
import os
import shutil
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

# Запасной выбор качества: пробуем по очереди, не падаем на первом отказе.
FORMAT_FALLBACK = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
# EJS: разрешаем дотягивать скрипты решателя с github, если не хватает своих.
REMOTE_COMPONENTS = "ejs:github"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


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
    """Диагностика файла кук БЕЗ содержимого."""
    size = os.path.getsize(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    header = any(
        ln.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File"))
        for ln in lines[:3]
    )
    tab_lines = sum(1 for ln in lines if not ln.startswith("#") and ln.count("\t") >= 6)
    looks_netscape = header or tab_lines > 0
    return size, len(lines), looks_netscape, header, tab_lines


def tail(text, n=TAIL_LINES):
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


def verify_streams(path):
    """
    Проверить готовый файл через ffprobe: нужны и видео-, и аудиодорожка.
    Вернуть None, если всё на месте, иначе — понятную строку про то, чего нет.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None  # проверить нечем — не блокируем (в Actions ffprobe есть)

    def has(kind):
        _, out, _ = run([
            ffprobe, "-v", "error", "-select_streams", kind,
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", path,
        ])
        return bool(out.strip())

    has_v, has_a = has("v"), has("a")
    if not has_v and not has_a:
        return "В скачанном файле нет ни видео, ни звука — загрузка неполная."
    if not has_a:
        return "В скачанном файле НЕТ звуковой дорожки — для обработки звука бесполезен."
    if not has_v:
        return "В скачанном файле НЕТ видеодорожки."
    return None


def print_full_formats(url, cookie_path):
    """
    Напечатать --list-formats, но ТОЛЬКО строки полного формата (видео+звук),
    без «video only» / «audio only» / раскадровок.
    """
    _, out, err = run([
        "yt-dlp", "-F", "--remote-components", REMOTE_COMPONENTS,
        "--cookies", cookie_path, url,
    ])
    lines = (out or "").splitlines() + (err or "").splitlines()
    full = []
    for ln in lines:
        low = ln.lower()
        if not any(c.isalnum() for c in ln):
            continue                              # разделители таблицы
        if "video only" in low or "audio only" in low:
            continue                              # раздельные дорожки
        if "storyboard" in low or "images" in low:
            continue                              # раскадровки
        if low.startswith("["):
            continue                              # [info], [youtube] …
        if low.lstrip().startswith("id "):
            continue                              # заголовок таблицы
        full.append(ln)
    print("\n=== Полные форматы (видео+звук), доступные yt-dlp ===")
    print("\n".join(full) if full else "(полных форматов не нашлось — только раздельные дорожки)")


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

    # пункт 4 (диагностика кук): без содержимого
    size, nlines, looks_netscape, header, tab_lines = cookie_report(cookie_path)
    print(
        f"Куки: файл создан, {size} байт, {nlines} строк; "
        f"формат Netscape: {'да' if looks_netscape else 'НЕТ'} "
        f"(заголовок: {'есть' if header else 'нет'}, строк с 7 полями: {tab_lines})"
    )
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
        "--remote-components", REMOTE_COMPONENTS,
        "-f", FORMAT_FALLBACK,
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", args.output,
        args.url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode == 0 and os.path.exists(args.output):
            # пункт 6: проверяем дорожки готового файла
            problem = verify_streams(args.output)
            if problem:
                print(problem)
                print("Скачивание завершилось, но файл непригоден для обработки звука.")
                sys.exit(5)
            print(f"Скачан отрезок {args.seconds} с → {args.output} (видео+звук на месте)")
            return

        # --- пункт 1: полный вывод yt-dlp ---
        print(f"\n=== yt-dlp STDERR (последние {TAIL_LINES} строк) ===")
        print(tail(proc.stderr))
        print(f"\n=== yt-dlp STDOUT (последние {TAIL_LINES} строк) ===")
        print(tail(proc.stdout))
        print(f"\n(yt-dlp завершился с кодом {proc.returncode})")

        # --- пункты 2 и 5: классификация по всему выводу ---
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
        # прочее — не гадаем; показываем полные форматы (пункт 7) и полный вывод выше
        print("Не удалось скачать видео (не из кук и не DRM). Смотри строки ERROR в выводе выше.")
        print_full_formats(args.url, cookie_path)
        sys.exit(1)
    finally:
        try:
            os.remove(cookie_path)   # куки удаляем всегда
        except OSError:
            pass


if __name__ == "__main__":
    main()
