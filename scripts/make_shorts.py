#!/usr/bin/env python3
# make_shorts.py — ШАГ 7: нарезка вертикальных роликов 9:16 для Instagram/Shorts.
# Из исходного видео (work/00_raw.mp4) и расшифровки (work/transcript.json):
#   1) выбираем 5–6 законченных кусков по 32–75 c с сильным началом;
#   2) режем, кадрируем в вертикаль 9:16 с лицом по центру, масштаб 1080x1920;
#   3) вшиваем субтитры в твоём стиле — белые с ЗЕЛЁНОЙ подсветкой слова (караоке);
#   4) сверху первые 2.5 c — крупный крючок-фраза;
#   5) присылаем ролики Анне в Telegram (Круг 2 — проверка).
#
# Кадрирование: лицо в кадрах примерно по центру-справа. Берём вертикальную
# колонку, центрированную по доле FACE_FX. (Слежение MediaPipe добавим позже,
# если понадобится — здесь фикс-центр, надёжно и без тяжёлых зависимостей.)

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
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FACE_FX = float(os.environ.get("FACE_FX", "0.56"))   # доля ширины кадра — центр лица
N_CLIPS = int(os.environ.get("N_CLIPS", "5"))
JUNK = ("ну ", "вот ", "значит", "это самое", "как бы", "и вот", "а вот", "эээ", "ммм")


def _dims(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def load_segments():
    with open(TRANSCRIPT, encoding="utf-8") as f:
        return json.load(f).get("segments", [])


def pick_clips(segments, n=N_CLIPS, tmin=32, tmax=75):
    clips = []
    i, N = 0, len(segments)
    while i < N and len(clips) < n:
        t0 = segments[i]["start"]
        low = segments[i]["text"].strip().lower()
        if any(low.startswith(j.strip()) for j in JUNK):
            i += 1
            continue
        j = i
        end = segments[i]["end"]
        while j + 1 < N and (segments[j]["end"] - t0) < tmin:
            j += 1
            end = segments[j]["end"]
        while (j + 1 < N and (segments[j + 1]["end"] - t0) <= tmax
               and not segments[j]["text"].strip().endswith((".", "!", "?"))):
            j += 1
            end = segments[j]["end"]
        dur = end - t0
        if tmin - 4 <= dur <= tmax:
            clips.append((t0, end, i, j))
            i = j + 2
        else:
            i += 1
    return clips


def clip_words(segments, i, j, t0, end):
    words = []
    for seg in segments[i:j + 1]:
        for w in seg.get("words", []):
            s, e = float(w["start"]), float(w["end"])
            t = (w.get("word") or "").strip()
            if t and s >= t0 - 0.2 and e <= end + 0.2:
                words.append({"t": t, "s": max(0.0, s - t0), "e": max(0.0, e - t0)})
    return words


def hook_text(segments, i):
    t = re.sub(r"\s+", " ", segments[i]["text"]).strip()
    words = t.split()
    h = " ".join(words[:4]).rstrip(".,!?;:—")
    return h.upper()


def _ts(sec):
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,74,&H0000FF00,&H00FFFFFF,&H00202020,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,300,1
Style: Hook,DejaVu Sans,86,&H0000F0FF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,8,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words, hook, out_ass):
    ev = []
    # Крючок сверху первые 2.5 c.
    ev.append(f"Dialogue: 0,{_ts(0)},{_ts(2.5)},Hook,,0,0,0,,{hook}")
    # Субтитры караоке по 3 слова.
    per = 3
    for k in range(0, len(words), per):
        grp = words[k:k + per]
        start, end = grp[0]["s"], grp[-1]["e"]
        parts = []
        for m, w in enumerate(grp):
            dur = (grp[m + 1]["s"] - w["s"]) if m < len(grp) - 1 else (w["e"] - w["s"])
            parts.append("{\\k%d}%s " % (max(1, int(round(dur * 100))), w["t"]))
        ev.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Sub,,0,0,0,,{''.join(parts).strip()}")
    with open(out_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEAD + "\n".join(ev) + "\n")


def cut_clip(idx, t0, end, ass, w, h):
    crop_w = int(round(h * 9 / 16))
    if crop_w > w:
        crop_w = w
    x = int(round(FACE_FX * w - crop_w / 2))
    x = max(0, min(x, w - crop_w))
    out = os.path.join(OUT_DIR, f"short_{idx}.mp4")
    vf = f"crop={crop_w}:{h}:{x}:0,scale=1080:1920,subtitles={ass}"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t0:.3f}", "-to", f"{end:.3f}", "-i", RAW,
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", out],
        check=True)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    segments = load_segments()
    w, h = _dims(RAW)
    clips = pick_clips(segments)
    if not clips:
        tg.send_message("Нарезка: не удалось выбрать куски из расшифровки.")
        return
    print(f"Выбрано кусков: {len(clips)} (видео {w}x{h})")
    tg.send_message(f"Нарезка готова — {len(clips)} вертикальных ролика. "
                    "Под каждым: первая фраза и крючок. Скажи по каждому «ок» / «убрать» / «исправить».")
    for n, (t0, end, i, j) in enumerate(clips, 1):
        words = clip_words(segments, i, j, t0, end)
        hook = hook_text(segments, i)
        ass = os.path.join(OUT_DIR, f"clip_{n}.ass")
        build_ass(words, hook, ass)
        try:
            out = cut_clip(n, t0, end, ass, w, h)
        except subprocess.CalledProcessError as e:
            print(f"ролик {n}: ошибка ffmpeg ({e})")
            continue
        size = os.path.getsize(out) / 1e6
        first = re.sub(r"\s+", " ", segments[i]["text"]).strip()[:80]
        cap = f"Ролик {n} • {int(end - t0)} c\nКрючок: {hook}\nНачало: {first}"
        if size <= 49:
            tg.send_video(out, caption=cap)
        else:
            tg.send_message(cap + f"\n(файл {size:.0f} МБ — великоват для бота, лежит в Releases позже)")
        print(f"ролик {n}: {out} ({size:.1f} МБ)")
    print("make_shorts: готово")


if __name__ == "__main__":
    main()
