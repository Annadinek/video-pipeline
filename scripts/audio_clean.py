#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика.

Причина «бочки» у Анны — НЕ шум, а ПЕРЕКОС СПЕКТРА (эффект близости микрофона):
горб на низах (~120 Гц) громче голоса, верх провален на ~14 дБ. highpass не
помогал: он режет НИЖЕ точки среза, а горб на 120 Гц выше неё. Шумодавы тоже —
ровного шума нет. Лечим ПОЛОЧНЫМИ фильтрами (bass/treble shelf): давят/поднимают
всю область за частотой.

Команда (решение Анны, проверено ею по спектру):
  bass=g=-12:f=200:width_type=o:width=1.2  → treble=g=8:f=3500  → loudnorm

bass shelf −12 дБ ниже 200 Гц — гасит раздутый горб (низ −8 дБ, не −1).
treble shelf +8 дБ выше 3.5 кГц — поднимает верх (+7 дБ). Голос (~800 Гц) не тронут.
loudnorm I=-14:LRA=11:TP=-1 — громкость.

Никаких highpass, arnndn, узкого equalizer. Модели arnndn (presets/rnnoise/) не
удалены, но не используются.

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)

Отчёт: спектр по 7 полосам (40/120/300/800/2000/5000/10000 Гц) до и после —
видно, как полки давят низ и поднимают верх, а голос стоит на месте.

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  bass_gain, bass_hz, bass_width   низкая полка (умолч. -12 / 200 Гц / 1.2 октавы)
  treble_gain, treble_hz           высокая полка (умолч. +8 / 3500 Гц)
  target_lufs, lra, true_peak_db   loudnorm (умолч. -14 / 11 / -1)

Только Python 3, ffmpeg и стандартная библиотека.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "audio.json")

DEFAULTS = {
    "bass_gain": -12,      # низкая полка: гасим раздутый низ, дБ
    "bass_hz": 200,        # частота низкой полки, Гц
    "bass_width": 1.2,     # ширина перехода, октавы
    "treble_gain": 8,      # высокая полка: поднимаем верх, дБ
    "treble_hz": 3500,     # частота высокой полки, Гц
    "target_lufs": -14,    # целевая громкость
    "lra": 11,             # целевой разброс loudnorm
    "true_peak_db": -1,    # потолок пика
}
NUMERIC_KEYS = ["bass_gain", "bass_hz", "bass_width", "treble_gain", "treble_hz",
                "target_lufs", "lra", "true_peak_db"]
BANDS = [40, 120, 300, 800, 2000, 5000, 10000]  # полосы замера спектра, Гц


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"audio_clean: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
    """presets/audio.json поверх умолчаний. Нет файла/сломан → умолчания, не падаем."""
    cfg = dict(DEFAULTS)
    status = "defaults (нет файла)"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if not isinstance(user, dict):
                raise ValueError("не объект JSON")
            for k in DEFAULTS:
                if k in user and user[k] is not None:
                    cfg[k] = user[k]
            status = "ok (presets/audio.json)"
        except Exception:
            cfg = dict(DEFAULTS)
            status = "corrupted, using defaults"
    for k in NUMERIC_KEYS:
        try:
            cfg[k] = float(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    cfg["_config_status"] = status
    return cfg


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def ffprobe_duration(ffprobe, path):
    rc, out, err = run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ])
    if rc != 0 or not out.strip():
        raise RuntimeError(f"ffprobe не смог прочитать длительность {path}: {err.strip()}")
    try:
        return float(out.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe отдал нечисловую длительность '{out.strip()}' для {path}")


def build_shelves(cfg):
    """Полки без громкости: низкая полка → высокая полка (чистый эффект спектра)."""
    return (f"bass=g={cfg['bass_gain']:g}:f={cfg['bass_hz']:g}"
            f":width_type=o:width={cfg['bass_width']:g},"
            f"treble=g={cfg['treble_gain']:g}:f={cfg['treble_hz']:g}")


def build_chain(cfg):
    """Полная цепочка: полки → loudnorm."""
    return (build_shelves(cfg) +
            f",loudnorm=I={cfg['target_lufs']:g}:LRA={cfg['lra']:g}:TP={cfg['true_peak_db']:g}")


def apply_af(ffmpeg, src, out, af):
    """Собрать выход одним проходом: цепочка фильтров af, видео копируем."""
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-af", af,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"сборка звука не удалась ({af}): {err.strip()[-300:]}")


def band_level(ffmpeg, path, center, pre=""):
    """Средняя громкость в полосе 1 октава вокруг center (dB). pre — фильтры до замера."""
    af = ((pre + ",") if pre else "") + f"bandpass=f={center:g}:width_type=o:w=1,volumedetect"
    _, _, err = run([ffmpeg, "-hide_banner", "-i", path, "-vn", "-af", af, "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    return round(float(m.group(1)), 1) if m else None


def spectrum(ffmpeg, path, pre=""):
    """Уровень по 7 полосам (dB). pre — фильтры до замера (напр. полки без loudnorm)."""
    return {c: band_level(ffmpeg, path, c, pre) for c in BANDS}


def measure_loudness(ffmpeg, path, cfg):
    """(I, TP) файла — для отчёта громкости и пика до/после. Тишина/-inf → (None, None)."""
    chain = (f"loudnorm=I={cfg['target_lufs']:g}:TP={cfg['true_peak_db']:g}"
             f":LRA={cfg['lra']:g}:print_format=json")
    _, _, err = run([ffmpeg, "-hide_banner", "-i", path, "-vn", "-af", chain, "-f", "null", "-"])
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, None
    try:
        m = json.loads(err[start:end + 1])
        i, tp = float(m["input_i"]), float(m["input_tp"])
    except (ValueError, KeyError):
        return None, None
    if not (math.isfinite(i) and math.isfinite(tp)):
        return None, None
    return round(i, 1), round(tp, 1)


def write_pipeline(out_path, audio):
    pipeline = os.path.join(ROOT, "state", "pipeline.json")
    if not os.path.exists(pipeline):
        return False
    vid = os.path.basename(os.path.dirname(os.path.abspath(out_path)))
    try:
        with open(pipeline, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    cur = data.get("current")
    if not isinstance(cur, dict) or cur.get("id") != vid:
        return False
    cur["audio"] = audio
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def _fmt(v):
    return "—" if v is None else f"{v}"


def _delta(a, b):
    if a is None or b is None:
        return "—"
    return f"{b - a:+.1f}"


def run_normal(ffmpeg, ffprobe, args, cfg):
    """Один файл: bass shelf → treble shelf → loudnorm. Спектр по 7 полосам до/после."""
    out = args.output
    dur = ffprobe_duration(ffprobe, args.input)
    chain = build_chain(cfg)
    before = spectrum(ffmpeg, args.input)
    # «после» меряем на полках БЕЗ loudnorm — иначе общий подъём громкости сместил бы
    # все полосы разом и замаскировал бы чистый эффект полок (голос «уехал» бы вместе).
    after = spectrum(ffmpeg, args.input, pre=build_shelves(cfg))
    i0, tp0 = measure_loudness(ffmpeg, args.input, cfg)
    apply_af(ffmpeg, args.input, out, chain)
    i1, tp1 = measure_loudness(ffmpeg, out, cfg)

    audio = {
        "duration": round(dur, 2),
        "chain": chain,
        "spectrum_before": before, "spectrum_after": after,
        "loudness_before": i0, "loudness_after": i1,
        "peak_before": tp0, "peak_after": tp1,
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (полки против перекоса спектра) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Цепочка:     {chain}")
    print("СПЕКТР ПО ПОЛОСАМ (dB, bandpass 1 октава), исходник → после полок (до loudnorm):")
    for c in BANDS:
        b, a = before.get(c), after.get(c)
        mark = ""
        if c == 120:
            mark = "  ← горб низа (давим полкой)"
        elif c == 800:
            mark = "  ← голос (не трогаем)"
        elif c in (5000, 10000):
            mark = "  ← верх (поднимаем полкой)"
        print(f"  {c:>5} Гц:  {_fmt(b):>7} → {_fmt(a):>7}   (Δ {_delta(b, a)} dB){mark}")
    print(f"Громкость:   {_fmt(i0)} → {_fmt(i1)} LUFS  (цель {cfg['target_lufs']:g})")
    print(f"Пик:         {_fmt(tp0)} → {_fmt(tp1)} dBTP  (потолок {cfg['true_peak_db']:g})")
    if tp1 is not None and tp1 > cfg["true_peak_db"]:
        print(f"⚠️ Пик итога {tp1} выше потолка {cfg['true_peak_db']:g} — возможен клиппинг.")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    args = ap.parse_args()

    cfg = load_config()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)
    if not os.path.exists(args.input):
        die(f"нет входного файла: {args.input}", 2)

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)

    try:
        run_normal(ffmpeg, ffprobe, args, cfg)
    except RuntimeError as e:
        die(f"{e} → blocked", 2)


if __name__ == "__main__":
    main()
