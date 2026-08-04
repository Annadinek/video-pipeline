#!/usr/bin/env python3
# resub.py — заменяет большие вшитые субтитры на маленькие в готовых клипах Vizard.
#
# Зачем: клипы Vizard хорошие, но в них видны СТАРЫЕ вшитые субтитры Анны — крупные,
# края выходят за кадр. Стереть вшитые пиксели начисто нельзя, поэтому:
#   1) обрезаем нижнюю полосу кадра со старыми субтитрами и подтягиваем кадр обратно
#      в вертикаль 9:16 (без искажения, лёгкий зум);
#   2) распознаём речь клипа (faster-whisper, по словам) и рисуем НОВЫЕ маленькие
#      субтитры с зелёной караоке-подсветкой (стиль эталона: белый текст, зелёное
#      текущее слово, тонкая обводка, без плашки, по центру снизу);
#   3) отправляем в бот с подписью.
#
# Один проход ffmpeg на клип: crop → scale → crop → ass. Ключи только из секретов.
# Запуск в GitHub Actions (там есть VIZARDAI_API_KEY и TELEGRAM_BOT_TOKEN).

import os
import re
import subprocess

import requests

import config
import tg

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ.get("VIZARD_PROJECT_ID", "").strip()
# Какие клипы обрабатывать: 1-based номера через запятую (как в списке в боте).
INDICES = [int(x) for x in os.environ.get("INDICES", "1").split(",") if x.strip()]
# Доля кадра снизу, которую РАЗМЫВАЕМ, чтобы спрятать старые крупные субтитры
# (кадр НЕ обрезаем и НЕ искажаем — просто мажем полосу внизу).
BLUR_BAND = float(os.environ.get("BLUR_BAND", "0.18"))
# Размер новых субтитров (шрифт ASS). «Маленькие» — 44.
FONT_SIZE = int(os.environ.get("FONT_SIZE", "44"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
MAX_WORDS_PER_LINE = 4

# Мусор, который Whisper выдумывает на тишине/шуме — вычищаем.
JUNK = re.compile(r"^\s*[\[(]?\s*(музык\w*|аплодисмент\w*|смех|вдох|субтитры[^)]*|"
                  r"продолжение следует)\s*[\])]?\s*$", re.IGNORECASE)


def query_clips():
    key = config.require_env("VIZARDAI_API_KEY")
    r = requests.get(f"{API}/project/query/{PROJECT_ID}",
                     headers={"VIZARDAI_API_KEY": key}, timeout=60).json()
    if r.get("code") != 2000 or not r.get("videos"):
        raise RuntimeError(f"Vizard query: {r}")
    clips = r["videos"]

    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)
    return clips


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def transcribe_words(clip_path):
    """faster-whisper по словам, с VAD (у Анны уличный шум)."""
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        clip_path, language="ru", word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 400})
    words = []
    for seg in segments:
        if JUNK.match(seg.text or ""):
            continue
        for w in (seg.words or []):
            t = (w.word or "").strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words, path):
    """Маленькие субтитры с караоке: белый текст, текущее слово зелёное."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,DejaVu Sans,{FONT_SIZE},&H0000FF00,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,60,60,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Группируем слова по строкам (до MAX_WORDS_PER_LINE).
    lines = [words[i:i + MAX_WORDS_PER_LINE]
             for i in range(0, len(words), MAX_WORDS_PER_LINE)]
    events = []
    for group in lines:
        if not group:
            continue
        start = group[0][0]
        end = group[-1][1]
        parts = []
        for (ws, we, t) in group:
            dur_cs = max(int((we - ws) * 100), 1)  # длительность в сотых
            parts.append(f"{{\\kf{dur_cs}}}{t}")
        text = " ".join(parts)
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Def,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    return path


def render(clip_path, ass_path, out_path):
    """Один проход БЕЗ обрезки и искажения: размываем нижнюю полосу со старыми
    субтитрами и поверх вбиваем новые маленькие."""
    b = BLUR_BAND
    # split → нижнюю полосу сильно размыть → вернуть на место → новые субтитры
    fc = (
        f"[0:v]split=2[base][t];"
        f"[t]crop=iw:ih*{b}:0:ih-ih*{b},boxblur=luma_radius=40:luma_power=4[bl];"
        f"[base][bl]overlay=0:H-h[bg];"
        f"[bg]ass={ass_path}[v]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", out_path],
        check=True, capture_output=True)
    return out_path


def make_caption(clip, idx):
    """Короткая подпись к ролику (пока из заголовка; настоящие тексты — отдельно)."""
    title = (clip.get("title") or "").strip()
    title = re.sub(r"\s*\(\d+\)\s*$", "", title)  # убрать «(1)» и т.п.
    return f"Ролик {idx} • {title}"


def main():
    if not PROJECT_ID:
        raise SystemExit("Не задан VIZARD_PROJECT_ID.")
    clips = query_clips()
    os.makedirs("resub", exist_ok=True)
    done = 0
    for idx in INDICES:
        if idx < 1 or idx > len(clips):
            print(f"клип {idx}: вне диапазона 1..{len(clips)}")
            continue
        clip = clips[idx - 1]
        src = download(clip["videoUrl"], f"resub/src_{idx}.mp4")
        words = transcribe_words(src)
        if not words:
            tg.send_message(f"Ролик {idx}: речь не распозналась, пропускаю.")
            print(f"клип {idx}: нет слов")
            continue
        ass = build_ass(words, f"resub/subs_{idx}.ass")
        out = render(src, ass, f"resub/out_{idx}.mp4")
        cap = make_caption(clip, idx)
        # Режим предпросмотра: вынуть кадры (низ, где субтитры) и НЕ слать в бот —
        # чтобы Клод сам посмотрел глазами, что старых субтитров не видно.
        if os.environ.get("PREVIEW_ONLY", "0") == "1":
            os.makedirs("resub_preview", exist_ok=True)
            dur = (clip.get("videoMsDuration") or 0) / 1000
            for tag, ss in (("a", "1.5"), ("b", f"{max(dur/2, 2):.1f}")):
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", out, "-frames:v", "1",
                                "-q:v", "3", f"resub_preview/r{idx}{tag}.jpg"],
                               check=True, capture_output=True)
            print(f"клип {idx}: {len(words)} слов, кадры в resub_preview/ (в бот не слал)")
            done += 1
            continue
        size = os.path.getsize(out) / 1e6
        if size <= 49:
            tg.send_video(out, caption=cap)
        else:
            tg.send_message(cap + f"\n(файл {size:.0f} МБ великоват для бота)")
        for p in (src, out):
            try:
                os.remove(p)
            except OSError:
                pass
        done += 1
        print(f"клип {idx}: {len(words)} слов, отправлен")
    print(f"ГОТОВО: обработано {done} из {len(INDICES)}.")


if __name__ == "__main__":
    main()
