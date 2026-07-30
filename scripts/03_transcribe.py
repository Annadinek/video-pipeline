#!/usr/bin/env python3
# 03_transcribe.py — расшифровка речи в текст (faster-whisper).
# Русский язык, с временем каждого слова, обязательно с VAD-фильтром
# (иначе Whisper выдумывает слова на тишине, а съёмка уличная — много шума).
# Результат: transcript.json. Используется трижды (субтитры, нарезка, текст
# для Telegram) — второй раз не пересчитываем.

import json
import os
import re
import sys

WORK_DIR = os.environ.get("WORK_DIR", "work")
OUT = os.path.join(WORK_DIR, "transcript.json")

# ВРЕМЕННО: размер модели. large-v3 точнее, но на процессоре без видеокарты
# медленно. Финальный выбор (large-v3 vs medium) сделаем по замеру времени на
# живом видео, чтобы уложиться в лимит 6 часов.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3")

# Мусорные фразы, которые Whisper придумывает на шуме/тишине — вычищаем.
GARBAGE = [
    "музыка", "[музыка]", "[аплодисменты", "аплодисменты",
    "вдох", "смех", "субтитры сделал", "субтитры создавал",
    "продолжение следует",
]


def is_garbage(text):
    t = text.strip().lower().strip(".…!?()[]")
    if not t:
        return True
    for g in GARBAGE:
        if g in t:
            return True
    return False


def transcribe(audio_path):
    from faster_whisper import WhisperModel

    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        language="ru",
        word_timestamps=True,
        vad_filter=True,  # ОБЯЗАТЕЛЬНО: без него Whisper выдумывает слова на тишине
    )

    result = []
    prev_text = None
    for seg in segments:
        text = seg.text.strip()
        if is_garbage(text):
            continue
        if text == prev_text:  # повтор одной фразы подряд — пропускаем
            continue
        prev_text = text
        words = [
            {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
            for w in (seg.words or [])
        ]
        result.append(
            {"start": round(seg.start, 3), "end": round(seg.end, 3),
             "text": text, "words": words}
        )

    os.makedirs(WORK_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"language": "ru", "segments": result}, f,
                  ensure_ascii=False, indent=2)
    print(f"03_transcribe: {len(result)} сегментов -> {OUT}")


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else os.path.join(WORK_DIR, "03_final.mp4")
    transcribe(audio)
