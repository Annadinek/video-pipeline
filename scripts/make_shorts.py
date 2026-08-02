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


END = (".", "!", "?", "…")


def build_sentences(segments):
    """Склеиваем сегменты в целые предложения (до знака конца фразы).
    Нарезаем только по границам предложений — тогда ролик не обрывается на слове."""
    sents, cur = [], []
    for s in segments:
        cur.append(s)
        if s["text"].strip().endswith(END):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for grp in sents:
        text = " ".join(x["text"].strip() for x in grp).strip()
        words = []
        for x in grp:
            words += x.get("words", [])
        out.append({"start": grp[0]["start"], "end": grp[-1]["end"],
                    "text": text, "words": words})
    return out


def pick_clips(segments, n=N_CLIPS, tmin=22, tmax=75):
    """Кусок = одно или несколько ПОДРЯД идущих целых предложений, 22–75 c.
    Начинается с начала предложения, заканчивается концом предложения."""
    S = build_sentences(segments)
    clips, k, N = [], 0, len(S)
    while k < N and len(clips) < n:
        low = S[k]["text"].strip().lower()
        if len(low) < 12 or any(low.startswith(j.strip()) for j in JUNK):
            k += 1
            continue
        t0, end, j = S[k]["start"], S[k]["end"], k
        # добираем целыми предложениями, пока кусок короче tmin и влезает в tmax
        while (end - t0) < tmin and j + 1 < N and (S[j + 1]["end"] - t0) <= tmax:
            j += 1
            end = S[j]["end"]
        dur = end - t0
        if tmin - 6 <= dur <= tmax:
            words = []
            for m in range(k, j + 1):
                words += S[m]["words"]
            clips.append((t0, end, S[k]["text"], words))
            k = j + 2
        else:
            k += 1
    return clips


def speech_intervals(words, t0, end, pad=0.08, merge_gap=0.28):
    """Из таймингов слов строим отрезки речи: паузы длиннее ~0.4 c вырезаются,
    тишина в начале/конце тоже. Короткие естественные паузы остаются."""
    ws = sorted((w for w in words
                 if w.get("start") is not None and t0 - 0.1 <= w["start"] <= end + 0.1),
                key=lambda w: w["start"])
    iv = []
    for w in ws:
        s, e = max(t0, w["start"] - pad), min(end, w["end"] + pad)
        if e <= s:
            continue
        if iv and s <= iv[-1][1] + merge_gap:
            iv[-1][1] = max(iv[-1][1], e)
        else:
            iv.append([s, e])
    return iv or [[t0, end]]


# Короткие служебные слова, на которые крючок заканчиваться не должен (некрасиво).
TAIL_STOP = {"в", "во", "и", "а", "но", "на", "по", "о", "об", "что", "как", "с",
             "со", "к", "от", "до", "у", "за", "из", "не", "ни", "же", "бы", "ли",
             "то", "это", "я", "ты", "мы", "вы", "он", "она", "они", "мой", "моя"}


def hook_text(text):
    """Крючок — законченная короткая фраза из начала куска: без тире-маркеров
    реплик (дефис внутри слова сохраняем) и без висящего предлога/союза в конце."""
    t = " " + text + " "
    t = re.sub(r"\s[—–-]+\s", " ", t)          # тире-маркеры реплик (в окружении пробелов)
    t = re.sub(r"\s+", " ", t).strip(" —–-")   # ведущие/замыкающие тире
    first = re.split(r"[,.!?;:]", t)[0].strip()  # до первой паузы-запятой
    words = first.split()
    if not (2 <= len(words) <= 6):
        words = t.split()[:5]
    while len(words) > 2 and words[-1].lower().strip(".,!?;:") in TAIL_STOP:
        words.pop()
    return " ".join(words).rstrip(".,!?;:—–- ").upper().strip()


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


# Кадрируем КАЖДЫЙ кусок к одному портретному размеру 3:4 (как у ролика 1),
# а НЕ по доле ширины. Видео Анны склеено из кусков разной ширины: при кадрировании
# по доле широкие куски давали низкую картинку и большие полосы. Приводя каждый
# кусок к одному аспекту 3:4, получаем одинаково крупное лицо и одинаково маленькие
# полосы (~12%) во всех роликах — все смотрятся как первый. Субтитры Анны (по центру
# снизу) при этом помещаются целиком, низ/верх добираем мягким размытием.
CROP_AR = os.environ.get("CROP_AR", "3/4")  # ширина:высота портретного кадра


def cut_clip(idx, t0, end, ass, words):
    out = os.path.join(OUT_DIR, f"short_{idx}.mp4")
    iv = speech_intervals(words, t0, end)
    dur = sum(e - s for s, e in iv)                    # длительность после вырезки пауз
    sel = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in iv)
    # select/aselect оставляют только отрезки речи (убирают тишину в начале и паузы
    # между словами), setpts/asetpts склеивают их без дыр. Дальше — кадрирование 3:4,
    # размытый фон, крупный кадр и крючок.
    cw = f"min(iw\\,ih*{CROP_AR})"
    fc = (f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB,"
          f"crop={cw}:ih:(iw-{cw})/2:0[c];"
          "[c]split[bg][fg];"
          "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
          "crop=1080:1920,boxblur=22:2[bgb];"
          "[fg]scale=1080:-2[fgs];"
          "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vv];"
          f"[vv]subtitles={ass}[v];"
          f"[0:a]aselect='{sel}',asetpts=N/SR/TB[a]")
    subprocess.run(
        ["ffmpeg", "-y", "-i", RAW,
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", out], check=True)
    # Два кадра-превью (начало и середина) — проверить глазами, что субтитр Анны
    # нигде не обрезан и лицо крупное.
    for tag, ss in (("a", "1.0"), ("b", f"{max(dur / 2, 1.5):.1f}")):
        prev = os.path.join(PREVIEW_DIR, f"short_{idx}{tag}.jpg")
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", out, "-frames:v", "1",
                        "-q:v", "3", prev], check=True, capture_output=True)
    return out, dur


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
    for n, (t0, end, text, words) in enumerate(clips, 1):
        hook = hook_text(text)
        ass = os.path.join(OUT_DIR, f"clip_{n}.ass")
        hook_ass(hook, ass)
        try:
            out, dur = cut_clip(n, t0, end, ass, words)
        except subprocess.CalledProcessError as e:
            print(f"ролик {n}: ошибка ffmpeg ({e})")
            continue
        first = re.sub(r"\s+", " ", text).strip()[:80]
        made.append((n, out, int(dur), hook, first))
        print(f"ролик {n}: {out} ({os.path.getsize(out)/1e6:.1f} МБ, {dur:.0f} c) крючок={hook}")

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
