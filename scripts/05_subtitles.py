#!/usr/bin/env python3
# 05_subtitles.py — делает субтитры из transcript.json:
#   1) .srt для YouTube (по нему YouTube ищет) — review/subtitles.srt
#   2) .ass для ВШИВАНИЯ в видео — стильные, по центру, без чёрной плашки,
#      крупные, с подсветкой каждого произносимого слова (караоке) — review/subs.ass

import json
import os

import config

WORK_DIR = config.WORK_DIR
TRANSCRIPT = os.path.join(WORK_DIR, "transcript.json")
OUT_SRT = os.path.join(config.REVIEW_DIR, "subtitles.srt")
OUT_ASS = os.path.join(config.REVIEW_DIR, "subs.ass")

WORDS_PER_LINE = 5  # сколько слов показываем на экране за раз


def _srt_ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_ts(seconds):
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _load_segments():
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        return json.load(f).get("segments", [])


def build_srt(segments):
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg['start'])} --> {_srt_ts(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    os.makedirs(config.REVIEW_DIR, exist_ok=True)
    with open(OUT_SRT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"05_subtitles: SRT — {len(segments)} строк -> {OUT_SRT}")


# --- Стиль ASS ---
# PrimaryColour — цвет ПОДСВЕЧЕННОГО (уже произнесённого) слова: ярко-жёлтый.
# SecondaryColour — цвет ещё не произнесённого слова: белый.
# BorderStyle=1 + Outline=3 — тонкая тёмная ОБВОДКА, а не чёрная плашка.
# Alignment=2 — по центру снизу; MarginV=140 — приподнято от нижнего края.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,DejaVu Sans,60,&H0000F0FF,&H00FFFFFF,&H00202020,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,120,120,140,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _flatten_words(segments):
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            t = (w.get("word") or "").strip()
            if t:
                words.append({"t": t, "start": float(w["start"]), "end": float(w["end"])})
    return words


def build_ass(segments):
    os.makedirs(config.REVIEW_DIR, exist_ok=True)
    words = _flatten_words(segments)
    events = []

    if words:
        # Разбиваем на строки по WORDS_PER_LINE слов, внутри строки — караоке \k.
        for i in range(0, len(words), WORDS_PER_LINE):
            group = words[i:i + WORDS_PER_LINE]
            start = group[0]["start"]
            end = group[-1]["end"]
            parts = []
            for j, w in enumerate(group):
                # \k = сколько сотых секунды слово «горит» до перехода к следующему.
                if j < len(group) - 1:
                    dur = group[j + 1]["start"] - w["start"]
                else:
                    dur = w["end"] - w["start"]
                k = max(1, int(round(dur * 100)))
                parts.append("{\\k%d}%s " % (k, w["t"]))
            text = "".join(parts).strip()
            events.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Main,,0,0,0,,{text}")
    else:
        # Нет пословных таймингов — показываем целыми сегментами, без караоке.
        for seg in segments:
            text = seg["text"].strip().replace("\n", " ")
            events.append(
                f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},Main,,0,0,0,,{text}"
            )

    with open(OUT_ASS, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + "\n".join(events) + "\n")
    print(f"05_subtitles: ASS — {len(events)} строк -> {OUT_ASS}")


if __name__ == "__main__":
    segs = _load_segments()
    build_srt(segs)
    build_ass(segs)
