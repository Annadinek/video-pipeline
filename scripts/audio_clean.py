#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика. РОВНО ДВЕ ВЕЩИ:
  1) шумодав  (afftdn, самый мягкий — верх не срезаем)
  2) громкость (loudnorm, 2 прохода, до целевой LUFS как у эталона)

Больше НИЧЕГО. Ни компрессии, ни лимитера, ни деэссера, ни выреза гула,
ни highpass, ни резки пауз — всё убрано по решению Анны (эталон
https://youtu.be/wXXP9Bzh5bg: -9.3 LUFS, пик 0.1 dBTP, полоса 4–8 кГц -31 dB).

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)

Цепочка ffmpeg — две строки:
    afftdn=nr=<gentle>          # шумодав, самый мягкий
    loudnorm=I=<lufs>:TP=<tp>   # громкость до эталона (2 прохода)

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  denoise           on/off      — включать ли шумодав
  denoise_strength  gentle/low/medium — сила afftdn (по умолчанию самый мягкий)
  target_lufs                   — целевая громкость (эталон -9.3)
  true_peak_db                  — потолок пика (эталон ~0.1; безопасно -1)

Отчёт цифрами: громкость и пик до/после, полоса 4–8 кГц до/после (яркость),
уровень шумодава. Исходник не трогаем. Только Python 3, ffmpeg, стандартная
библиотека.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "audio.json")

DEFAULTS = {
    "denoise": "on",
    "denoise_strength": "gentle",  # самый мягкий — верх не режем
    "target_lufs": -9.3,           # как у эталона Анны
    "true_peak_db": -1,            # эталон ~0.1; безопасно -1 (0 клиппит)
}
LOUDNORM_LRA = 11  # целевой разброс громкости для loudnorm

# Шумодав afftdn от самого мягкого. nr — сила подавления (меньше = мягче),
# nf — порог шума в дБ (ниже/отрицательнее = трогаем только совсем тихое).
# gentle: минимальная срезка верха, только убрать ровный фон.
STRENGTH = {
    "gentle": "afftdn=nr=3:nf=-32",
    "low":    "afftdn=nr=6:nf=-30",
    "medium": "afftdn=nr=10:nf=-28",
}


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
    cfg["_config_status"] = status
    return cfg


def denoise_level(cfg):
    """Итоговый уровень шумодава: 'off' | 'gentle' | 'low' | 'medium'."""
    if str(cfg["denoise"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["denoise_strength"]).strip().lower()
    return lv if lv in STRENGTH else "gentle"


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
    return float(out.strip())


def loudnorm_measure(ffmpeg, src, pre, cfg):
    """1-й проход loudnorm: измерить. pre — шумодав до loudnorm ('' = без шумодава)."""
    chain = (pre + "," if pre else "") + (
        f"loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']}:LRA={LOUDNORM_LRA}"
        ":print_format=json"
    )
    _, _, err = run([ffmpeg, "-hide_banner", "-i", src, "-af", chain, "-f", "null", "-"])
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"loudnorm не отдал измерения: {err.strip()[-400:]}")
    return json.loads(err[start:end + 1])


def loudnorm_apply(ffmpeg, src, pre, measured, out, cfg):
    """2-й проход loudnorm: применить измеренное. Видео копируем, меняем только звук."""
    chain = (pre + "," if pre else "") + (
        f"loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']}:LRA={LOUDNORM_LRA}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-af", chain,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"loudnorm (2-й проход) не удался: {err.strip()[-400:]}")


def measure_loudness(ffmpeg, path, cfg):
    """Вернуть (I, TP, LRA) файла — для отчёта до/после."""
    m = loudnorm_measure(ffmpeg, path, "", cfg)
    return (round(float(m["input_i"]), 1),
            round(float(m["input_tp"]), 1),
            round(float(m["input_lra"]), 1))


def band_level(ffmpeg, path, lo=4000, hi=8000):
    """Средняя громкость в полосе [lo,hi] Гц (dB) — яркость/звонкость голоса."""
    _, _, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-af", f"highpass=f={lo},lowpass=f={hi},volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    return round(float(m.group(1)), 1) if m else None


def process_one(ffmpeg, src, out, level, cfg):
    """Две строки: [afftdn(level)] → loudnorm (2 прохода). Больше ничего."""
    pre = "" if level == "off" else STRENGTH[level]
    measured = loudnorm_measure(ffmpeg, src, pre, cfg)
    loudnorm_apply(ffmpeg, src, pre, measured, out, cfg)


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


def run_normal(ffmpeg, ffprobe, args, cfg):
    """Один файл: шумодав (по конфигу) + громкость до эталона."""
    out_clean = args.output
    level = denoise_level(cfg)

    i0, tp0, lra0 = measure_loudness(ffmpeg, args.input, cfg)
    band0 = band_level(ffmpeg, args.input)
    dur = ffprobe_duration(ffprobe, args.input)

    process_one(ffmpeg, args.input, out_clean, level, cfg)

    i1, tp1, lra1 = measure_loudness(ffmpeg, out_clean, cfg)
    band1 = band_level(ffmpeg, out_clean)

    audio = {
        "duration": round(dur, 2),
        "loudness_before": i0, "loudness_after": i1,
        "peak_before": tp0, "peak_after": tp1,
        "band_4_8k_before": band0, "band_4_8k_after": band1,
        "lra_after": lra1,
        "denoise": level,
    }
    wrote = write_pipeline(out_clean, audio)

    filt = "нет" if level == "off" else STRENGTH[level]
    print("=== ОТЧЁТ audio_clean (шумодав + громкость, две строки) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Цепочка:     {filt} → loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']} (2 прохода)")
    print(f"Громкость:   {i0} → {i1} LUFS   (цель {cfg['target_lufs']})")
    print(f"Пик:         {tp0} → {tp1} dBTP  (потолок {cfg['true_peak_db']})")
    print(f"Яркость 4–8кГц: {band0} → {band1} dB  (эталон ~-31; чем выше, тем звонче)")
    print(f"Разброс LRA: {lra0} → {lra1} LU")
    print(f"Шумодав:     {level}" + (f"  ({STRENGTH[level]})" if level != 'off' else ""))
    print(f"Файл:        {out_clean}")
    print(f"pipeline.json: {'обновлён (current.audio)' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("===========================================================")


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

    run_normal(ffmpeg, ffprobe, args, cfg)


if __name__ == "__main__":
    main()
