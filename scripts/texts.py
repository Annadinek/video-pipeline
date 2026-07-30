#!/usr/bin/env python3
# texts.py — заголовок, описание и фразы-крючки из расшифровки.
# ВРЕМЕННО: это простые заготовки по тексту расшифровки. Живые «человеческие»
# тексты (по правилам de-ai из CLAUDE.md) пишутся отдельно на этапе согласования;
# здесь — черновая основа, чтобы конвейер работал end-to-end.

import re


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def load_segments(transcript_path):
    import json
    with open(transcript_path, "r", encoding="utf-8") as f:
        return json.load(f).get("segments", [])


def strongest_segments(segments, n=3):
    """Самые «содержательные» куски: длиннее по словам, не начинаются с мусора."""
    junk_start = ("ну", "вот", "значит", "это самое", "как бы")
    scored = []
    for seg in segments:
        t = _clean(seg.get("text"))
        if not t:
            continue
        low = t.lower()
        if low.startswith(junk_start):
            continue
        scored.append((len(t.split()), t))
    scored.sort(reverse=True)
    return [t for _, t in scored[:n]] or [_clean(s.get("text")) for s in segments[:n]]


def make_title(segments):
    """Заголовок до 60 знаков, чтобы не обрезался на телефоне (ВРЕМЕННО)."""
    cand = strongest_segments(segments, 1)
    title = cand[0] if cand else "Без названия"
    title = title.rstrip(".!,;:— ")
    if len(title) > 60:
        title = title[:57].rstrip() + "…"
    return title


def make_description(segments):
    """Короткое описание для YouTube из первых мыслей (ВРЕМЕННО)."""
    body = " ".join(_clean(s.get("text")) for s in segments[:5])
    if len(body) > 400:
        body = body[:397].rstrip() + "…"
    return body


def make_hooks(segments, n=3):
    """Короткие фразы-крючки для обложек: 3–6 слов из сильной мысли (ВРЕМЕННО)."""
    hooks = []
    for t in strongest_segments(segments, n):
        words = t.split()
        hooks.append(" ".join(words[:5]).rstrip(".!,;:— "))
    while len(hooks) < n:
        hooks.append("Смотри до конца")
    return hooks[:n]
