#!/usr/bin/env python3
# subtitles_only.py — скачивает видео, распознаёт речь и ВШИВАЕТ только субтитры
# (эталонный стиль: белый текст, произносимое слово зелёным, по центру снизу).
# Никакой другой обработки (без стабилизации/цвета/музыки). Заливает черновиком
# unlisted и шлёт ссылку в бот на проверку. Ключи только из секретов.
import os
import re
import subprocess

import config
import tg
import yt_ops

VID = os.environ.get("VIDEO_ID", "").strip()
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
MAX_WORDS = 6
JUNK = re.compile(r"^\s*[\[(]?\s*(музык\w*|аплодисмент\w*|смех|вдох|субтитры[^)]*|"
                  r"продолжение следует)\s*[\])]?\s*$", re.IGNORECASE)


def download(vid):
    url = vid if "://" in vid else f"https://www.youtube.com/watch?v={vid}"
    os.makedirs("work", exist_ok=True)
    raw = "work/raw.mp4"
    cmd = ["yt-dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "--remote-components", "ejs:github", "--retries", "5", "--fragment-retries", "5",
           "-o", "work/raw.%(ext)s", url]
    if os.path.exists("work/cookies.txt"):
        cmd += ["--cookies", "work/cookies.txt"]
    for a in range(1, 4):
        if subprocess.run(cmd).returncode == 0 and os.path.exists(raw):
            return raw
        print(f"скачивание: попытка {a} не удалась")
    tg.send_message(f"Не смог скачать видео {vid} для субтитров. Проверь ссылку/доступ.")
    raise SystemExit("download failed")


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
                       capture_output=True, text=True).stdout.strip()
    w, h = r.split("x")
    return int(w), int(h)


def transcribe(path):
    from faster_whisper import WhisperModel
    m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(path, language="ru", word_timestamps=True,
                           vad_filter=True, vad_parameters={"min_silence_duration_ms": 400})
    words = []
    for s in segs:
        if JUNK.match(s.text or ""):
            continue
        for w in (s.words or []):
            t = (w.word or "").strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words, path, W, H):
    fs = max(int(H * 0.055), 22)          # шрифт ~5.5% высоты кадра
    mv = int(H * 0.06)                    # отступ снизу
    ml = int(W * 0.06)
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
              "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
              "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Def,DejaVu Sans,{fs},&H0000FF00,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,"
              f"100,100,0,0,1,2,1,2,{ml},{ml},{mv},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = [words[i:i + MAX_WORDS] for i in range(0, len(words), MAX_WORDS)]
    ev = []
    for g in lines:
        if not g:
            continue
        parts = [f"{{\\kf{max(int((we-ws)*100),1)}}}{t}" for (ws, we, t) in g]
        ev.append(f"Dialogue: 0,{ass_time(g[0][0])},{ass_time(g[-1][1])},Def,,0,0,0,,{' '.join(parts)}")
    open(path, "w", encoding="utf-8").write(header + "\n".join(ev) + "\n")
    return path


def main():
    if not VID:
        raise SystemExit("Не задан VIDEO_ID.")
    raw = download(VID)
    W, H = probe(raw)
    print(f"видео {W}x{H}, распознаю речь ({WHISPER_MODEL})...")
    words = transcribe(raw)
    if not words:
        tg.send_message("Речь не распозналась — субтитры не вшил. Проверь звук видео.")
        raise SystemExit("no words")
    ass = build_ass(words, "work/subs.ass", W, H)
    out = "work/subbed.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-vf", f"ass={ass}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "160k", out], check=True)
    print("субтитры вшиты, заливаю на YouTube (unlisted)...")
    yt = yt_ops.upload_video(out, "Черновик — только субтитры",
                             "Черновик на проверку. Длинное видео, только субтитры.",
                             privacy="unlisted")
    tg.send_message("Новое видео обработано — ТОЛЬКО субтитры (без стабилизации, цвета и музыки), "
                    f"на проверку:\nhttps://youtu.be/{yt}\n\n"
                    "Как одобришь — это же видео БЕЗ субтитров отдам в Vizard на нарезку, "
                    "и он наложит субтитры сам.")
    print(f"ГОТОВО: https://youtu.be/{yt} | слов: {len(words)}")


if __name__ == "__main__":
    main()
