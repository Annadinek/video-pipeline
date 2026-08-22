#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# reels_process.py — обработка вертикального ролика (Instagram Reels / TikTok).
# ОТДЕЛЬНАЯ ветка конвейера. Vizard тут НЕ используется. YouTube-файлы не трогаем.
# Все числа — в presets/reels_process.json (приняты Анной 2026-08-15).
#
# Порядок (см. stages/reels-process/PROMPT.md):
#   A. Нормализация: автоповорот телефона → вертикаль 1080×1920, 30 fps.
#   B. Звук: DeepFilterNet + подмес сухого 0.15 → loudnorm −14 LUFS, TP −1.0
#      (та же цепочка, что принята для YouTube в audio_clean.py).
#   C. Вырез пауз: silencedetect (−35 dB, пауза ≥0.35 с), склейки с полями 0.1 с.
#   D. Субтитры: faster-whisper (large-v3, ru, word_timestamps) → ASS пословно
#      с подсветкой текущего слова. Проверка по brain/FORBIDDEN.md.
#   E. Финал: зум по лицу + вжигание субтитров + музыка (−22 LUFS) →
#      1080×1920, 30 fps, H.264, 8 Мбит/с.
#
# Зависимости (ставит workflow): ffmpeg+ffprobe, deepfilternet==0.5.6 torch==2.0.1
#   torchaudio==2.0.2, faster-whisper, opencv-python (4.x), numpy.
#
# Запуск: reels_process.py --input <файл> --output <файл.mp4> [--music <трек>]

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "presets", "reels_process.json")
MODELS_DIR = os.path.join(ROOT, "presets", "deepfilternet")
FORBIDDEN_PATH = os.path.join(ROOT, "brain", "FORBIDDEN.md")

W, H, FPS = 1080, 1920, 30


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def need(tool):
    path = shutil.which(tool)
    if not path:
        sys.exit(f"reels_process: нет {tool} в PATH — поставь зависимости (см. шапку).")
    return path


def ffprobe_duration(ffprobe, path):
    rc, out, err = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path])
    if rc != 0 or not out.strip():
        raise RuntimeError(f"ffprobe не прочитал длительность {path}: {err.strip()[-200:]}")
    return float(out.strip())


# ---------- A. Нормализация в вертикаль ----------
def normalize(ffmpeg, src, out):
    """Автоповорот (ffmpeg сам применяет display-matrix телефона) → вписать в
    1080×1920 (доложить чёрным, если пропорции другие), 30 fps. Звук — исходный."""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},setsar=1")
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-i", src, "-vf", vf,
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                      "-c:a", "aac", "-b:a", "192k", "-metadata:s:v:0", "rotate=0", out])
    if rc != 0:
        raise RuntimeError(f"нормализация не удалась: {err.strip()[-300:]}")


# ---------- B. Звук: DeepFilterNet + подмес + громкость ----------
def clean_audio(ffmpeg, deepfilter, src_video, cfg, work):
    """Возвращает путь к очищенному моно-wav (48 кГц) под всю длину ролика."""
    a = cfg["audio"]
    model_dir = os.path.join(MODELS_DIR, "DeepFilterNet3")
    if not os.path.isdir(model_dir):
        raise RuntimeError(f"нет модели шумодава {model_dir}")
    in_wav = os.path.join(work, "in.wav")
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-i", src_video, "-vn",
                      "-ar", "48000", "-ac", "1", in_wav])
    if rc != 0:
        raise RuntimeError(f"не извлёк звук: {err.strip()[-200:]}")
    dfn_dir = os.path.join(work, "dfn")
    os.makedirs(dfn_dir, exist_ok=True)
    rc, _, err = run([deepfilter, "-m", model_dir, "-o", dfn_dir, in_wav])
    if rc != 0:
        raise RuntimeError(f"DeepFilterNet упал: {err.strip()[-300:]}")
    wavs = sorted(f for f in os.listdir(dfn_dir) if f.endswith(".wav"))
    if not wavs:
        raise RuntimeError("DeepFilterNet не создал wav")
    enhanced = os.path.join(dfn_dir, wavs[0])
    # подмес сухого + возврат верха (4 кГц) + громкость → чистый wav
    dry = float(a.get("dry_mix", 0.15))
    wet = 1.0 - dry
    lufs = a.get("loudnorm_i", -14)
    tp = a.get("true_peak_db", -1.0)
    out_wav = os.path.join(work, "clean.wav")
    fc = (f"[0:a][1:a]amix=inputs=2:weights={wet:g} {dry:g}:normalize=0,"
          f"equalizer=f=4000:width_type=o:width=1.5:g=4,"
          f"loudnorm=I={lufs:g}:LRA=11:TP={tp:g}[a]")
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-i", enhanced, "-i", in_wav,
                      "-filter_complex", fc, "-map", "[a]", "-ar", "48000", out_wav])
    if rc != 0:
        raise RuntimeError(f"подмес/громкость не удались: {err.strip()[-300:]}")
    return out_wav


# ---------- C. Вырез пауз ----------
def detect_silences(ffmpeg, wav, silence_db, min_pause):
    rc, out, err = run([ffmpeg, "-hide_banner", "-i", wav, "-af",
                        f"silencedetect=noise={silence_db}dB:d={min_pause}",
                        "-f", "null", "-"])
    log = out + "\n" + err
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", log)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def keep_segments(silences, duration, edge):
    """По интервалам тишины строим интервалы РЕЧИ (что оставляем), с полями edge."""
    segs, cur = [], 0.0
    for s, e in silences:
        s = max(0.0, s - 0.0)
        if s > cur:
            segs.append((max(0.0, cur - edge), min(duration, s + edge)))
        cur = e if e is not None else duration
    if cur < duration:
        segs.append((max(0.0, cur - edge), duration))
    # склеить перекрытия
    merged = []
    for a, b in segs:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a > 0.05]


def cut_pauses(ffmpeg, video, clean_wav, segs, work, out):
    """Оставить только интервалы речи: обрезать video+clean_wav по segs и склеить."""
    if not segs:
        run([ffmpeg, "-y", "-hide_banner", "-i", video, "-i", clean_wav,
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", out])
        return
    parts = []
    for i, (a, b) in enumerate(segs):
        p = os.path.join(work, f"seg_{i:03d}.mp4")
        rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                          "-i", video, "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", clean_wav,
                          "-map", "0:v", "-map", "1:a",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                          "-c:a", "aac", "-b:a", "192k", "-avoid_negative_ts", "make_zero", p])
        if rc != 0:
            raise RuntimeError(f"нарезка сегмента {i} не удалась: {err.strip()[-200:]}")
        parts.append(p)
    lst = os.path.join(work, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-f", "concat", "-safe", "0",
                      "-i", lst, "-c", "copy", out])
    if rc != 0:  # запасной путь — пересобрать
        rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-f", "concat", "-safe", "0",
                          "-i", lst, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                          "-c:a", "aac", out])
        if rc != 0:
            raise RuntimeError(f"склейка не удалась: {err.strip()[-200:]}")


# ---------- D. Субтитры (whisper → ASS) ----------
def transcribe_words(video, model_name):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(video, language="ru", word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append({"start": w.start, "end": w.end, "text": t})
    return words


def check_forbidden(words):
    if not os.path.exists(FORBIDDEN_PATH):
        return []
    roots = []
    with open(FORBIDDEN_PATH, encoding="utf-8") as f:
        for line in f:
            for tok in re.findall(r"[А-Яа-яЁё]{4,}", line):
                roots.append(tok.lower())
    text = " ".join(w["text"].lower() for w in words)
    return sorted({r for r in set(roots) if r in text})


def _ass_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_ass(words, cfg, path, per_line=4):
    sub = cfg["subtitles"]
    fontsize = int(round(H * float(sub.get("size_frac_of_height", 0.0556))))
    outline = int(sub.get("outline_px", 3))
    margin_v = int(round(H * float(sub.get("margin_bottom_frac", 0.18))))
    hi = "&H0000FFFF"   # жёлтый (BGR) — текущее слово
    wht = "&H00FFFFFF"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: R,DejaVu Sans,{fontsize},{wht},&H00000000,&H00000000,1,{outline},0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [words[i:i + per_line] for i in range(0, len(words), per_line)]
    events = []
    for group in lines:
        for gi, cur in enumerate(group):
            parts = []
            for gj, w in enumerate(group):
                txt = w["text"].replace("{", "").replace("}", "")
                if gi == gj:
                    parts.append(f"{{\\c{hi}}}{txt}{{\\c{wht}}}")
                else:
                    parts.append(txt)
            start = cur["start"]
            end = cur["end"] if gi < len(group) - 1 else group[-1]["end"]
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},R,,0,0,0,,{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


# ---------- E. Зум по лицу + субтитры + музыка + финал ----------
def face_center(video):
    """Средний центр лица (доли кадра). Нет лица → центр кадра (0.5, 0.42)."""
    try:
        import cv2
    except ImportError:
        return 0.5, 0.42
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cxs, cys = [], []
    for i in range(0, max(n, 1), max(1, n // 12 or 1)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(120, 120))
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            cxs.append((x + w / 2) / frame.shape[1])
            cys.append((y + h / 2) / frame.shape[0])
    cap.release()
    if cxs:
        return sum(cxs) / len(cxs), sum(cys) / len(cys)
    return 0.5, 0.42


def finalize(ffmpeg, video, subs_ass, cfg, music, out, duration):
    """zoompan (мягкий наезд 1.00→1.12 за 2 с, центр по лицу) → вжечь ASS →
    смешать музыку (−22 LUFS под голосом) → 1080×1920/30/H.264/8M."""
    z = cfg["zoom"]
    cx, cy = face_center(video)
    zt, zs = float(z.get("to", 1.12)), float(z.get("seconds", 2))
    # z(t): растёт до zt за zs секунд, дальше держит
    zexpr = f"min({zt:g},1+({zt - 1:g})*(on/{FPS})/{zs:g})"
    xexpr = f"iw*{cx:.4f}-(iw/zoom/2)"
    yexpr = f"ih*{cy:.4f}-(ih/zoom/2)"
    ass_path = subs_ass.replace("\\", "/").replace(":", "\\:")
    vf = (f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d=1:s={W}x{H}:fps={FPS},"
          f"subtitles='{ass_path}'")
    cmd = [ffmpeg, "-y", "-hide_banner", "-i", video]
    music_lufs = cfg["music"].get("loudness_lufs", -22)
    if music and os.path.exists(music):
        cmd += ["-stream_loop", "-1", "-i", music]
        fc = (f"[0:v]{vf}[v];"
              f"[1:a]loudnorm=I={music_lufs:g}:TP=-1.5:LRA=11,atrim=0:{duration:.3f},"
              f"asetpts=N/SR/TB[m];"
              f"[0:a][m]amix=inputs=2:weights=1 0.9:normalize=0:duration=first[a]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "0:a"]
    cmd += ["-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-b:v", "8M",
            "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    rc, _, err = run(cmd)
    if rc != 0:
        raise RuntimeError(f"финальная сборка не удалась: {err.strip()[-400:]}")
    return cx, cy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--music", default=os.environ.get("REELS_MUSIC", ""))
    ap.add_argument("--work", default="")
    args = ap.parse_args()

    cfg = load_config()
    ffmpeg, ffprobe = need("ffmpeg"), need("ffprobe")
    deepfilter = need("deepFilter")
    if not os.path.exists(args.input):
        sys.exit(f"нет входного файла: {args.input}")

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work = args.work or os.path.join(out_dir, "reels_work")
    os.makedirs(work, exist_ok=True)

    print("A. нормализация в вертикаль…")
    base = os.path.join(work, "base.mp4")
    normalize(ffmpeg, args.input, base)

    print("B. звук: DeepFilterNet + подмес + громкость…")
    clean_wav = clean_audio(ffmpeg, deepfilter, base, cfg, work)

    print("C. вырез пауз…")
    cut = cfg["cut_pauses"]
    dur = ffprobe_duration(ffprobe, base)
    sil = detect_silences(ffmpeg, clean_wav, cut.get("silence_db", -35), cut.get("min_pause_sec", 0.35))
    segs = keep_segments(sil, dur, float(cut.get("keep_edge_sec", 0.1)))
    cut_mp4 = os.path.join(work, "cut.mp4")
    cut_pauses(ffmpeg, base, clean_wav, segs, work, cut_mp4)
    cut_dur = ffprobe_duration(ffprobe, cut_mp4)

    print("D. субтитры (faster-whisper)…")
    model_name = os.environ.get("WHISPER_MODEL", cfg["subtitles"].get("model", "large-v3"))
    words = transcribe_words(cut_mp4, model_name)
    bad = check_forbidden(words)
    if bad:
        msg = "СТОП: запрещённые слова в субтитрах: " + ", ".join(bad) + " → не выпускаю, пишу в бот."
        print(msg)
        sys.exit(3)
    subs = os.path.join(work, "subs.ass")
    build_ass(words, cfg, subs)

    print("E. зум по лицу + субтитры + музыка + финал…")
    cx, cy = finalize(ffmpeg, cut_mp4, subs, cfg, args.music, args.output, cut_dur)

    print("=== ОТЧЁТ reels_process ===")
    print(f"вход:        {args.input}")
    print(f"длина:       {dur:.1f} с → после выреза пауз {cut_dur:.1f} с (сегментов речи {len(segs)})")
    print(f"слов в субтитрах: {len(words)}")
    print(f"центр лица (зум): x={cx:.2f} y={cy:.2f}")
    print(f"музыка:      {'да' if args.music and os.path.exists(args.music) else 'нет (добавим трек из Фонотеки)'}")
    print(f"выход:       {args.output}  (1080×1920, {FPS} fps, H.264, 8 Мбит/с)")
    print("=" * 40)


if __name__ == "__main__":
    main()
