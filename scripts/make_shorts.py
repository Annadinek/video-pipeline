#!/usr/bin/env python3
# make_shorts.py — ШАГ 7: нарезка вертикальных роликов 9:16 из ТОГО ЖЕ видео,
# что и длинное (CuAcjA0lWr4). Кадр НЕ растягиваем: вписываем целиком, фон —
# мягкое размытие того же кадра. Субтитры у Анны уже вшиты в исходник — свои НЕ
# добавляем, только крючок-фразу сверху первые 2.5 c.
#
# Работает так:
#   1) из расшифровки выбираем 5–6 законченных кусков 32–75 c с сильным началом;
#   2) режем и собираем вертикаль 1080x1920 (вписанный кадр + размытый фон);
#   3) сверху крючок; 4) сохраняем ролик и кадр-превью.
# Отправка в бот — только если SEND=1. Иначе просто собираем и коммитим превью,
# чтобы Клод посмотрел глазами ПЕРЕД отправкой.

import json
import os
import re
import subprocess

import config
import tg

WORK = config.WORK_DIR
RAW = os.path.join(WORK, "00_raw.mp4")
TRANSCRIPT = os.path.join(WORK, "transcript.json")
OUT_DIR = "shorts"
PREVIEW_DIR = "shorts_preview"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
N_CLIPS = int(os.environ.get("N_CLIPS", "5"))
SEND = os.environ.get("SEND", "0") == "1"
JUNK = ("ну ", "вот ", "значит", "это самое", "как бы", "и вот", "а вот", "эээ", "ммм")


def load_segments():
    with open(TRANSCRIPT, encoding="utf-8") as f:
        return json.load(f).get("segments", [])


def pick_clips(segments, n=N_CLIPS, tmin=32, tmax=75):
    clips, i, N = [], 0, len(segments)
    while i < N and len(clips) < n:
        t0 = segments[i]["start"]
        low = segments[i]["text"].strip().lower()
        if any(low.startswith(j.strip()) for j in JUNK):
            i += 1
            continue
        j, end = i, segments[i]["end"]
        while j + 1 < N and (segments[j]["end"] - t0) < tmin:
            j += 1
            end = segments[j]["end"]
        while (j + 1 < N and (segments[j + 1]["end"] - t0) <= tmax
               and not segments[j]["text"].strip().endswith((".", "!", "?"))):
            j += 1
            end = segments[j]["end"]
        if tmin - 4 <= end - t0 <= tmax:
            clips.append((t0, end, i, j))
            i = j + 2
        else:
            i += 1
    return clips


def hook_text(segments, i):
    t = re.sub(r"\s+", " ", segments[i]["text"]).strip()
    return " ".join(t.split()[:4]).rstrip(".,!?;:—").upper()


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,DejaVu Sans,60,&H0000F0FF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,8,40,40,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def hook_ass(hook, out_ass):
    with open(out_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEAD + f"Dialogue: 0,0:00:00.00,0:00:02.50,Hook,,0,0,0,,{hook}\n")


# Доля ширины исходника, которую оставляем по центру. Исходник Анны почти
# квадратный (лицо по центру), а её субтитры вшиты широкой строкой. Если резать
# в упор 9:16 — субтитр обрежется по краям. Поэтому оставляем центральные 56%
# ширины (лицо крупное, широкий субтитр целиком помещается), низ/верх добираем
# мягким размытием того же кадра — полосы маленькие (~12%), не чёрные.
CROP_FRAC = float(os.environ.get("CROP_FRAC", "0.56"))


def cut_clip(idx, t0, end, ass):
    out = os.path.join(OUT_DIR, f"short_{idx}.mp4")
    cw = f"iw*{CROP_FRAC:.3f}"
    fc = (f"[0:v]crop={cw}:ih:(iw-{cw})/2:0[c];"
          "[c]split[bg][fg];"
          "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
          "crop=1080:1920,boxblur=22:2[bgb];"
          "[fg]scale=1080:-2[fgs];"
          "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vv];"
          f"[vv]subtitles={ass}[v]")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t0:.3f}", "-to", f"{end:.3f}", "-i", RAW,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", out], check=True)
    # Два кадра-превью (начало и середина) — проверить глазами, что субтитр Анны
    # нигде не обрезан и лицо крупное.
    for tag, ss in (("a", "1.5"), ("b", f"{(end - t0) / 2:.1f}")):
        prev = os.path.join(PREVIEW_DIR, f"short_{idx}{tag}.jpg")
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", out, "-frames:v", "1",
                        "-q:v", "3", prev], check=True, capture_output=True)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    for f in os.listdir(PREVIEW_DIR):
        os.remove(os.path.join(PREVIEW_DIR, f))
    segments = load_segments()
    clips = pick_clips(segments)
    if not clips:
        print("нет кусков")
        return
    print(f"Выбрано кусков: {len(clips)}")
    made = []
    for n, (t0, end, i, j) in enumerate(clips, 1):
        hook = hook_text(segments, i)
        ass = os.path.join(OUT_DIR, f"clip_{n}.ass")
        hook_ass(hook, ass)
        try:
            out = cut_clip(n, t0, end, ass)
        except subprocess.CalledProcessError as e:
            print(f"ролик {n}: ошибка ffmpeg ({e})")
            continue
        first = re.sub(r"\s+", " ", segments[i]["text"]).strip()[:80]
        made.append((n, out, int(end - t0), hook, first))
        print(f"ролик {n}: {out} ({os.path.getsize(out)/1e6:.1f} МБ) крючок={hook}")

    if not SEND:
        print(f"SEND=0 — собрано {len(made)} роликов, превью в {PREVIEW_DIR}/. Отправка отдельным запуском.")
        return

    tg.send_message(f"Нарезка по видео «Что такое свобода» — {len(made)} вертикальных ролика. "
                    "По каждому скажи «ок / убрать / исправить».")
    for n, out, dur, hook, first in made:
        size = os.path.getsize(out) / 1e6
        cap = f"Ролик {n} • {dur} c\nКрючок: {hook}\nНачало: {first}"
        if size <= 49:
            tg.send_video(out, caption=cap)
        else:
            tg.send_message(cap + f"\n(файл {size:.0f} МБ — великоват, ужму отдельно)")
    print("make_shorts: отправлено")


if __name__ == "__main__":
    main()
