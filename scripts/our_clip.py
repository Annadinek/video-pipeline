#!/usr/bin/env python3
"""
our_clip.py — сквозная сборка ОДНОГО ролика полностью НАШИМИ средствами.

Цепочка (решение Анны, 2026-08-16):
    скачать → color.py → vertical_cut.py → субтитры → вставки

ЗВУК В КОНВЕЙЕРЕ НЕ ОБРАБАТЫВАЕМ. Анна чистит звук вручную в CapCut ДО загрузки.
Здесь звук копируется ПОТОКОМ с начала до конца — каждый шаг идёт с `-c:a copy`,
исходная дорожка доходит до финала нетронутой. `audio_clean.py` из цепочки убран
(сам скрипт остаётся в коде, но тут не вызывается; presets/audio.json не трогаем).

Шаги:
  1. скачать      fetch_youtube.py → clip.mp4          (--url ...; либо --source готовый файл)
  2. цвет         color.py         → clip_color.mp4
  3. вертикаль    vertical_cut.py  → clip_vertical.mp4  (9:16, слежение за лицом)
  4. субтитры     subtitles.py     → clip_subs.mp4      (--transcribe: расшифровка find_dupes.py)
  5. вставки      inserts.py       → clip_inserts.mp4   (переиспользует transcript.json)

Итог: clip_inserts.mp4 — готовый вертикальный клип.

Запуск:
  scripts/our_clip.py --url https://youtu.be/XXXX --out-dir outputs/run [--start 0:00 --seconds 120]
  scripts/our_clip.py --source путь/к/clip.mp4 --out-dir outputs/run
  (--face-tracking false — вертикаль по центру; --no-inserts / --no-subs — пропустить шаг)
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
LOG_DIR = os.path.join(ROOT, "logs")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"our_clip: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def step(title, cmd):
    """Запустить шаг цепочки, показать время и статус. Падает — останавливаем цепочку."""
    print(f"\n=== {title} ===")
    print("   " + " ".join(cmd))
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    out = (p.stdout or "").strip()
    if out:
        print("   " + out.replace("\n", "\n   "))
    if p.returncode != 0:
        err = (p.stderr or "").strip()[-600:]
        die(f"{title}: шаг упал ({dt:.0f} c)\n{err}", 2)
    print(f"   [✓] {dt:.0f} c")


def audio_line(ffprobe, path):
    """Короткая строка про звук (для отчёта, что дорожка не менялась)."""
    p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name,sample_rate,channels",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    vals = [x for x in (p.stdout or "").split() if x.strip()]
    if len(vals) >= 3:
        return f"{vals[0]} {vals[1]} Гц, каналов {vals[2]}"
    return "нет аудиодорожки" if p.returncode == 0 else "?"


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="ссылка YouTube (скачиваем через fetch_youtube.py)")
    src.add_argument("--source", help="готовый горизонтальный файл (пропустить скачивание)")
    ap.add_argument("--out-dir", required=True, help="папка сборки (сюда все clip_*.mp4)")
    ap.add_argument("--start", default="0:00", help="начало отрезка при скачивании")
    ap.add_argument("--seconds", type=int, default=120, help="длина отрезка при скачивании")
    ap.add_argument("--face-tracking", default="true", help="слежение за лицом в vertical_cut")
    ap.add_argument("--no-subs", action="store_true", help="пропустить субтитры")
    ap.add_argument("--no-inserts", action="store_true", help="пропустить вставки")
    args = ap.parse_args()

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        die("нет ffprobe → blocked", 3)
    os.makedirs(args.out_dir, exist_ok=True)
    py = sys.executable

    clip = os.path.join(args.out_dir, "clip.mp4")
    clip_color = os.path.join(args.out_dir, "clip_color.mp4")
    clip_vertical = os.path.join(args.out_dir, "clip_vertical.mp4")
    clip_subs = os.path.join(args.out_dir, "clip_subs.mp4")
    clip_inserts = os.path.join(args.out_dir, "clip_inserts.mp4")

    print("=== our_clip: сквозная сборка (звук НЕ обрабатываем, копия потока) ===")

    # 1) скачать / взять готовый файл
    if args.url:
        step("1) Скачать (fetch_youtube.py)", [
            py, os.path.join(SCRIPTS, "fetch_youtube.py"),
            "--url", args.url, "--output", clip,
            "--start", args.start, "--seconds", str(args.seconds),
        ])
    else:
        if not os.path.exists(args.source):
            die(f"нет исходного файла: {args.source}", 2)
        if os.path.abspath(args.source) != os.path.abspath(clip):
            shutil.copy(args.source, clip)
        print(f"\n=== 1) Источник (готовый файл) ===\n   {args.source} → {clip}\n   [✓]")

    src_audio = audio_line(ffprobe, clip)
    print(f"   звук источника: {src_audio}")

    # 2) цвет
    step("2) Цвет (color.py)", [
        py, os.path.join(SCRIPTS, "color.py"), "--input", clip, "--output", clip_color,
    ])

    # 3) вертикаль 9:16
    step("3) Вертикаль 9:16 (vertical_cut.py)", [
        py, os.path.join(SCRIPTS, "vertical_cut.py"),
        "--input", clip_color, "--output", clip_vertical,
        "--face-tracking", args.face_tracking,
    ])

    last = clip_vertical
    # 4) субтитры
    if not args.no_subs:
        step("4) Субтитры (subtitles.py --transcribe)", [
            py, os.path.join(SCRIPTS, "subtitles.py"),
            "--input", clip_vertical, "--output", clip_subs, "--transcribe",
        ])
        last = clip_subs
    else:
        print("\n=== 4) Субтитры — пропущено (--no-subs) ===")

    # 5) вставки
    if not args.no_inserts:
        step("5) Вставки (inserts.py)", [
            py, os.path.join(SCRIPTS, "inserts.py"),
            "--input", last, "--output", clip_inserts,
        ])
        last = clip_inserts
    else:
        print("\n=== 5) Вставки — пропущено (--no-inserts) ===")

    final_audio = audio_line(ffprobe, last)
    print("\n=== ГОТОВО ===")
    print(f"Итог:   {last}")
    print(f"Звук:   источник [{src_audio}] → финал [{final_audio}] "
          f"({'без изменений — копия потока' if final_audio == src_audio else 'ВНИМАНИЕ: дорожка изменилась'})")


if __name__ == "__main__":
    main()
