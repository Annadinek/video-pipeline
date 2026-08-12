#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика.

Причина «бочки» у Анны — НЕ шум, а ПЕРЕКОС СПЕКТРА (эффект близости микрофона):
низ раздут (громче голоса), верх провален на ~14 дБ. Шумодавы не помогали —
ровного шума нет, им нечего искать. Лечим эквалайзером, не шумодавом.

Две команды (решение Анны, без нейросетей):
  hp:     highpass=100 → loudnorm=I=-14:LRA=11:TP=-1
  hp_eq:  highpass=100 → equalizer=f=3000:width_type=o:width=2:g=5 → loudnorm=...

highpass 100 — срезает раздутый низ; equalizer +5 дБ на 3 кГц (ширина 2 октавы) —
поднимает верх, возвращает разборчивость; loudnorm — громкость до -14 LUFS.

Модели arnndn (presets/rnnoise/) НЕ удалены, но из цепочки убраны — шумодава нет.

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)
Режим --variants: два файла clip_hp.mp4 и clip_hp_eq.mp4 на сравнение.

Отчёт: спектр по 7 полосам (40/120/300/800/2000/5000/10000 Гц) до и после —
видно, как highpass режет низ, а equalizer поднимает верх.

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  highpass_hz       срез низов, Гц (умолч. 100)
  top_boost         on/off — поднимать ли верх (вариант hp_eq против hp)
  top_boost_hz      центр подъёма верха, Гц (умолч. 3000)
  top_boost_width   ширина подъёма, октавы (умолч. 2)
  top_boost_gain    величина подъёма, дБ (умолч. 5)
  target_lufs       целевая громкость (умолч. -14)
  lra               целевой разброс loudnorm (умолч. 11)
  true_peak_db      потолок пика (умолч. -1)

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
    "highpass_hz": 100,       # срез раздутого низа (эффект близости)
    "top_boost": "on",        # поднимать верх (hp_eq); off = только highpass (hp)
    "top_boost_hz": 3000,     # центр подъёма верха
    "top_boost_width": 2,     # ширина, октавы
    "top_boost_gain": 5,      # величина подъёма, дБ
    "target_lufs": -14,       # целевая громкость
    "lra": 11,                # целевой разброс loudnorm
    "true_peak_db": -1,       # потолок пика
}
NUMERIC_KEYS = ["highpass_hz", "top_boost_hz", "top_boost_width",
                "top_boost_gain", "target_lufs", "lra", "true_peak_db"]
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
    return float(out.strip())


def build_chain(cfg, boost):
    """Цепочка фильтров: highpass → [equalizer верх] → loudnorm. boost=подъём верха."""
    parts = [f"highpass=f={cfg['highpass_hz']:g}"]
    if boost:
        parts.append(
            f"equalizer=f={cfg['top_boost_hz']:g}:width_type=o"
            f":width={cfg['top_boost_width']:g}:g={cfg['top_boost_gain']:g}"
        )
    parts.append(
        f"loudnorm=I={cfg['target_lufs']:g}:LRA={cfg['lra']:g}:TP={cfg['true_peak_db']:g}"
    )
    return ",".join(parts)


def apply_af(ffmpeg, src, out, af):
    """Собрать выход одним проходом: цепочка фильтров af, видео копируем."""
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-af", af,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"сборка звука не удалась ({af}): {err.strip()[-300:]}")


def band_level(ffmpeg, path, center):
    """Средняя громкость в полосе шириной 1 октава вокруг center (dB) — уровень полосы."""
    _, _, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-af", f"bandpass=f={center:g}:width_type=o:w=1,volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    return round(float(m.group(1)), 1) if m else None


def spectrum(ffmpeg, path):
    """Уровень по 7 полосам (dB). Метод: bandpass 1 октава + volumedetect."""
    return {c: band_level(ffmpeg, path, c) for c in BANDS}


def measure_loudness(ffmpeg, path, cfg):
    """(I, TP) файла — для отчёта громкости и пика до/после."""
    chain = (f"loudnorm=I={cfg['target_lufs']:g}:TP={cfg['true_peak_db']:g}"
             f":LRA={cfg['lra']:g}:print_format=json")
    _, _, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", chain, "-f", "null", "-"])
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, None
    try:
        m = json.loads(err[start:end + 1])
        i, tp = float(m["input_i"]), float(m["input_tp"])
    except (ValueError, KeyError):
        return None, None
    if not (math.isfinite(i) and math.isfinite(tp)):
        return None, None   # '-inf' на тишине → None (иначе -Infinity в JSON)
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


def print_spectrum(before, after):
    """Спектр по 7 полосам до → после (dB)."""
    print("СПЕКТР ПО ПОЛОСАМ (dB, метод bandpass 1 октава), до → после:")
    for c in BANDS:
        b, a = before.get(c), after.get(c)
        mark = ""
        if c == 120:
            mark = "  ← был самый громкий (раздутый низ)"
        elif c == 800:
            mark = "  ← голос"
        elif c in (5000, 10000):
            mark = "  ← верх (поднимаем)"
        print(f"  {c:>5} Гц:  {_fmt(b):>7} → {_fmt(a):>7}   (Δ {_delta(b, a)} dB){mark}")


def process_variant(ffmpeg, src, out, cfg, boost):
    """Собрать один вариант, вернуть отчёт (спектр после, громкость, пик)."""
    chain = build_chain(cfg, boost)
    apply_af(ffmpeg, src, out, chain)
    after = spectrum(ffmpeg, out)
    i1, tp1 = measure_loudness(ffmpeg, out, cfg)
    return {"chain": chain, "after": after, "loudness": i1, "peak": tp1}


def run_normal(ffmpeg, ffprobe, args, cfg):
    """Один файл по конфигу (top_boost on → hp_eq, off → hp). Спектр до/после."""
    out = args.output
    dur = ffprobe_duration(ffprobe, args.input)
    boost = str(cfg["top_boost"]).strip().lower() not in ("off", "false", "no", "0", "")
    before = spectrum(ffmpeg, args.input)
    i0, tp0 = measure_loudness(ffmpeg, args.input, cfg)
    r = process_variant(ffmpeg, args.input, out, cfg, boost)

    audio = {
        "duration": round(dur, 2),
        "chain": "hp_eq" if boost else "hp",
        "spectrum_before": before, "spectrum_after": r["after"],
        "loudness_before": i0, "loudness_after": r["loudness"],
        "peak_before": tp0, "peak_after": r["peak"],
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (эквалайзер против перекоса спектра) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Цепочка:     {r['chain']}")
    print_spectrum(before, r["after"])
    print(f"Громкость:   {_fmt(i0)} → {_fmt(r['loudness'])} LUFS  (цель {cfg['target_lufs']:g})")
    print(f"Пик:         {_fmt(tp0)} → {_fmt(r['peak'])} dBTP  (потолок {cfg['true_peak_db']:g})")
    if r["peak"] is not None and r["peak"] > cfg["true_peak_db"]:
        print(f"⚠️ Пик итога {r['peak']} выше потолка {cfg['true_peak_db']:g} — возможен клиппинг.")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=" * 60)


def run_variants(ffmpeg, args, cfg):
    """Два файла: hp (только highpass) и hp_eq (highpass + подъём верха). Спектр до/после."""
    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    before = spectrum(ffmpeg, args.input)
    i0, tp0 = measure_loudness(ffmpeg, args.input, cfg)

    print("=== ДВА ВАРИАНТА (30 с, послушать и выбрать) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Исходник:    громкость {_fmt(i0)} LUFS, пик {_fmt(tp0)} dBTP")
    print("Спектр исходника (dB):", json.dumps(before, ensure_ascii=False))
    print("-" * 60)
    variants = [("hp", "только highpass 100", False),
                ("hp_eq", "highpass 100 + подъём верха 3кГц/+5", True)]
    rows = []
    for key, title, boost in variants:
        out = os.path.join(out_dir, f"clip_{key}.mp4")
        r = process_variant(ffmpeg, args.input, out, cfg, boost)
        print(f"[{title}]")
        print(f"  цепочка: {r['chain']}")
        print_spectrum(before, r["after"])
        print(f"  громкость {_fmt(i0)} → {_fmt(r['loudness'])} LUFS; пик {_fmt(tp0)} → {_fmt(r['peak'])} dBTP")
        if r["peak"] is not None and r["peak"] > cfg["true_peak_db"]:
            print(f"  ⚠️ пик {r['peak']} выше потолка {cfg['true_peak_db']:g} — возможен клиппинг")
        print(f"  файл: {out}")
        print("-" * 60)
        rows.append({"variant": key, "loudness": r["loudness"], "peak": r["peak"],
                     "spectrum_after": r["after"]})
    print("variants =", json.dumps({"spectrum_before": before, "results": rows}, ensure_ascii=False))
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    ap.add_argument("--variants", action="store_true",
                    help="два файла: hp и hp_eq (на сравнение)")
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

    if args.variants:
        run_variants(ffmpeg, args, cfg)
    else:
        run_normal(ffmpeg, ffprobe, args, cfg)


if __name__ == "__main__":
    main()
