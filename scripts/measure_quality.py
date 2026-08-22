#!/usr/bin/env python3
# measure_quality.py — ИЗМЕРЯЕТ и ПОКАЗЫВАЕТ цифры (ничего не «подбирает»).
# Сравнивает по качеству: исходник (до Vizard), клип Vizard, наш vertical_cut.py.
#
# Метрики:
#   РЕЗКОСТЬ — средняя дисперсия Лапласа по N кадрам (как мерила Анна: 130 → 33).
#   ЗВУК     — LUFS (интегральная громкость), истинный пик (dBTP),
#              частота дискретизации, энергия в полосе ~6 кГц (mean_volume после bandpass).
#   КАРТИНКА — разрешение (ffprobe).
#
# Запуск: measure_quality.py <label1> <file1> [<label2> <file2> ...]
# Пишет отчёт в assets/measure/report.md и печатает в лог.

import glob
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image


def probe_resolution(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name",
         "-of", "json", path],
        capture_output=True, text=True).stdout
    try:
        s = json.loads(out)["streams"][0]
        return f'{s.get("width")}x{s.get("height")}', s.get("codec_name"), s.get("r_frame_rate")
    except Exception:
        return "?", "?", "?"


def probe_audio_basic(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,codec_name,channels",
         "-of", "json", path],
        capture_output=True, text=True).stdout
    try:
        s = json.loads(out)["streams"][0]
        return s.get("sample_rate"), s.get("codec_name"), s.get("channels")
    except Exception:
        return "?", "?", "?"


def _laplacian_var(gray):
    """Дисперсия Лапласа (резкость) через numpy — без OpenCV."""
    a = gray.astype("float64")
    lap = (-4 * a
           + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    lap = lap[1:-1, 1:-1]  # убираем края (артефакты roll)
    return float(lap.var())


def sharpness(path, n=24):
    """Средняя дисперсия Лапласа по n равномерно взятым кадрам (ffmpeg + numpy)."""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip() or 0)
    if dur <= 0:
        dur = n  # запас
    vals = []
    with tempfile.TemporaryDirectory() as d:
        # равномерно берём n кадров по времени (fps = n/dur)
        fps = max(n / dur, 0.1)
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-vf", f"fps={fps}",
             "-frames:v", str(n), "-q:v", "2",
             os.path.join(d, "f_%03d.jpg"), "-loglevel", "error"],
            check=False)
        for fp in sorted(glob.glob(os.path.join(d, "f_*.jpg"))):
            gray = np.asarray(Image.open(fp).convert("L"))
            vals.append(_laplacian_var(gray))
    return round(sum(vals) / len(vals), 1) if vals else None


def loudness(path):
    """LUFS (input_i) и истинный пик (input_tp) через loudnorm print_format=json."""
    r = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    txt = r.stderr
    # берём последний JSON-блок
    start = txt.rfind("{")
    end = txt.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            j = json.loads(txt[start:end + 1])
            return j.get("input_i"), j.get("input_tp")
        except Exception:
            pass
    return None, None


def mean_volume(path, af=None):
    """mean_volume (dB) через volumedetect; af — доп. фильтр (например bandpass)."""
    chain = (af + ",volumedetect") if af else "volumedetect"
    r = subprocess.run(["ffmpeg", "-i", path, "-af", chain, "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return line.split("mean_volume:")[1].strip()
    return None


def measure(label, path):
    res, vcodec, fps = probe_resolution(path)
    sr, acodec, ch = probe_audio_basic(path)
    sharp = sharpness(path)
    lufs, tp = loudness(path)
    band6k = mean_volume(path, "bandpass=f=6000:width_type=q:w=2")
    full = mean_volume(path)
    return {
        "label": label, "file": os.path.basename(path), "res": res,
        "vcodec": vcodec, "fps": fps, "sharp": sharp,
        "sr": sr, "acodec": acodec, "ch": ch,
        "lufs": lufs, "tp": tp, "band6k": band6k, "full": full,
    }


def main():
    args = sys.argv[1:]
    pairs = list(zip(args[0::2], args[1::2]))
    rows = []
    for label, path in pairs:
        if not os.path.exists(path):
            print(f"НЕТ ФАЙЛА: {label} → {path}")
            continue
        m = measure(label, path)
        rows.append(m)
        print(f"[{label}] {m['res']} {m['vcodec']} | резкость {m['sharp']} | "
              f"LUFS {m['lufs']} пик {m['tp']} | {m['sr']}Гц {m['acodec']} | "
              f"6кГц {m['band6k']} | общий {m['full']}")

    os.makedirs("assets/measure", exist_ok=True)
    with open("assets/measure/report.md", "w", encoding="utf-8") as f:
        f.write("# Замер качества: исходник vs Vizard vs наш vertical_cut\n\n")
        f.write("Резкость — средняя дисперсия Лапласа по 24 кадрам (как мерила Анна).\n")
        f.write("Больше = чётче. Звук: LUFS (громкость), пик dBTP, энергия ~6 кГц.\n\n")
        f.write("| Что | Разрешение | Видео | Резкость | LUFS | Пик dBTP | Частота | 6 кГц | Общий |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for m in rows:
            f.write(f"| {m['label']} | {m['res']} | {m['vcodec']} | {m['sharp']} | "
                    f"{m['lufs']} | {m['tp']} | {m['sr']} | {m['band6k']} | {m['full']} |\n")
    print("отчёт: assets/measure/report.md")


if __name__ == "__main__":
    main()
