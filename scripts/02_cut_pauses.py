#!/usr/bin/env python3
# 02_cut_pauses.py — ШАГ 2: убираем длинные ПАУЗЫ/тишину между фразами.
# Вход:  work/00_raw.mp4
# Выход: work/02_cut.mp4  (то же видео, но без «пустот»)
#
# Как работает (простыми словами):
#   1) ffmpeg-фильтр silencedetect находит участки тишины;
#   2) речь оставляем с небольшим запасом (0.15 c), чтобы НЕ резать дыхание и
#      начало слов внутри фразы;
#   3) всё лишнее вырезаем ОДНОЙ командой ffmpeg (select/aselect) — один проход.
#
# Порог подбираем сам (по CLAUDE.md): если куски в среднем короче 1.5 c — режем
# слишком жёстко, ослабляем и повторяем. Ничего Анне не показываем.
#
# ВАЖНО: если подходящий порог не нашёлся (например, на улице фон громкий и
# «тишины» почти нет) — НЕ режем вообще, просто копируем исходник в 02_cut.mp4,
# чтобы конвейер шёл дальше единообразно. Лучше не тронуть, чем испортить.

import os
import re
import subprocess

import config

WORK = config.WORK_DIR
RAW = os.path.join(WORK, "00_raw.mp4")
CUT = os.path.join(WORK, "02_cut.mp4")

PAD = 0.15  # запас в секундах вокруг речи (не режем дыхание/начало слова)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def detect_silences(path, noise_db, min_sil):
    """Возвращает список (start, end) участков тишины."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = p.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    sil = []
    i = 0
    for s in starts:
        e = None
        while i < len(ends) and ends[i] <= s:
            i += 1
        if i < len(ends):
            e = ends[i]
            i += 1
        sil.append((s, e))
    return sil


def keep_intervals(total, silences):
    """Куски, которые ОСТАВЛЯЕМ = всё, кроме тишины (сжатой на PAD с двух сторон)."""
    cuts = []
    for s, e in silences:
        if e is None:
            e = total
        a = s + PAD
        b = e - PAD
        if b - a > 0.05:
            cuts.append((a, b))
    keeps = []
    prev = 0.0
    for a, b in cuts:
        if a > prev:
            keeps.append((prev, a))
        prev = max(prev, b)
    if prev < total:
        keeps.append((prev, total))
    return [(a, b) for a, b in keeps if b - a > 0.02]


def avg_len(keeps):
    return sum(b - a for a, b in keeps) / len(keeps) if keeps else 0.0


def cut(keeps):
    terms = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    vf = f"select='{terms}',setpts=N/FRAME_RATE/TB"
    af = f"aselect='{terms}',asetpts=N/SR/TB"
    subprocess.run(
        ["ffmpeg", "-y", "-i", RAW, "-vf", vf, "-af", af,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", CUT],
        check=True,
    )


def copy_raw():
    subprocess.run(["ffmpeg", "-y", "-i", RAW, "-c", "copy", CUT], check=True)


def main():
    total = duration(RAW)
    # Пробуем от мягкого к более терпимому. min_sil больше -> режем только длинные
    # паузы -> куски длиннее (безопаснее).
    for noise_db, min_sil in [(-30, 0.5), (-30, 0.7), (-28, 0.9), (-26, 1.2)]:
        sil = detect_silences(RAW, noise_db, min_sil)
        keeps = keep_intervals(total, sil)
        if not keeps:
            print(f"порог noise={noise_db}dB d={min_sil}: тишины не найдено")
            continue
        a = avg_len(keeps)
        kept = sum(b - c for c, b in keeps)
        print(f"порог noise={noise_db}dB d={min_sil}: кусков={len(keeps)}, "
              f"средний кусок={a:.2f}c, вырезаем {total - kept:.1f}c из {total:.1f}c")
        if a >= 1.5 or min_sil >= 1.2:
            print("Порог принят, режу паузы.")
            cut(keeps)
            print(f"02_cut_pauses: готово -> {CUT} "
                  f"({duration(CUT):.1f}c вместо {total:.1f}c)")
            return
    print("Безопасный порог не найден — оставляю без вырезки пауз (копия исходника).")
    copy_raw()


if __name__ == "__main__":
    main()
