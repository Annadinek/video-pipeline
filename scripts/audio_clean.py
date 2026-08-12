#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика. РОВНО ДВЕ ВЕЩИ (решение Анны):
  1) DeepFilterNet — нейросетевой шумодав РЕЧИ (обучен на речи в реальном шуме);
  2) loudnorm I=-14:LRA=11:TP=-1 — громкость.

Ни bass/treble, ни equalizer, ни afftdn/arnndn (RNNoise — телефонное качество,
даёт «колодец»), ни настройки под тип шума. DeepFilterNet сам разбирает любой фон.

DeepFilterNet — не ffmpeg-фильтр, а отдельная программа (`deepFilter`), поэтому
проход такой:
  1. извлекаем звук в wav 48 кГц;
  2. deepFilter -m presets/deepfilternet/DeepFilterNet3 → чистый wav;
  3. loudnorm + возврат дорожки в mp4 (видео копируем).

Зависимости (совместимые версии, ставятся в workflow): deepfilternet==0.5.6,
torch==2.0.1, torchaudio==2.0.2 (свежий torchaudio убрал torchaudio.backend и
ломает импорт). CPU, быстро: 30 с звука ≈ 2 с.

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)

Отчёт цифрами: фон в тихом месте до/после, уровень голоса до/после, сколько
секунд считал DeepFilterNet.

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  denoise_model   папка модели в presets/deepfilternet/ (умолч. DeepFilterNet3)
  target_lufs, lra, true_peak_db   loudnorm (умолч. -14 / 11 / -1)
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "audio.json")
MODELS_DIR = os.path.join(ROOT, "presets", "deepfilternet")

DEFAULTS = {
    "denoise_model": "DeepFilterNet3",
    "target_lufs": -14,
    "lra": 11,
    "true_peak_db": -1,
}
NUMERIC_KEYS = ["target_lufs", "lra", "true_peak_db"]
FLOOR_WIN_SEC = 0.5
FLOOR_SILENCE_DB = -120


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
    rc, out, err = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path])
    if rc != 0 or not out.strip():
        raise RuntimeError(f"ffprobe не смог прочитать длительность {path}: {err.strip()}")
    try:
        return float(out.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe отдал нечисловую длительность '{out.strip()}' для {path}")


def ffprobe_sample_rate(ffprobe, path):
    _, out, _ = run([ffprobe, "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=sample_rate", "-of", "csv=p=0", path])
    try:
        sr = int(out.strip())
        return sr if sr > 0 else 48000
    except (ValueError, AttributeError):
        return 48000


def measure_loudness(ffmpeg, path, cfg):
    """(I, TP) файла. Тишина/-inf → (None, None)."""
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


def noise_floor(ffmpeg, ffprobe, path):
    """Фон в самом тихом окне ~0.5 с (dB): RMS по окнам (astats), минимум реального."""
    win = max(1, round(FLOOR_WIN_SEC * ffprobe_sample_rate(ffprobe, path)))
    _, out, err = run([
        ffmpeg, "-hide_banner", "-i", path, "-vn",
        "-af", (f"asetnsamples=n={win}:p=0,astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"),
        "-f", "null", "-",
    ])
    vals = []
    for x in re.findall(r"RMS_level=(\S+)", out + "\n" + err):
        try:
            v = float(x)
        except ValueError:
            continue
        if v > FLOOR_SILENCE_DB:
            vals.append(v)
    return round(min(vals), 1) if vals else None


def extract_wav(ffmpeg, src, out_wav):
    """Звук из mp4 в wav 48 кГц моно (для DeepFilterNet)."""
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-i", src, "-vn",
                      "-ar", "48000", "-ac", "1", out_wav])
    if rc != 0:
        raise RuntimeError(f"не извлёк звук: {err.strip()[-300:]}")


def run_deepfilter(deepfilter, model_dir, in_wav, out_dir):
    """DeepFilterNet: чистит in_wav → wav в out_dir. Возвращает (путь, секунды)."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.monotonic()
    rc, _, err = run([deepfilter, "-m", model_dir, "-o", out_dir, in_wav])
    dt = round(time.monotonic() - t0, 1)
    if rc != 0:
        raise RuntimeError(f"DeepFilterNet не отработал: {err.strip()[-400:]}")
    wavs = sorted(f for f in os.listdir(out_dir) if f.endswith(".wav"))
    if not wavs:
        raise RuntimeError("DeepFilterNet не создал выходной wav")
    return os.path.join(out_dir, wavs[0]), dt


def loudnorm_remux(ffmpeg, video_src, audio_wav, out, cfg):
    """Громкость на очищенной дорожке + возврат видео из оригинала (видео копируем)."""
    af = f"loudnorm=I={cfg['target_lufs']:g}:LRA={cfg['lra']:g}:TP={cfg['true_peak_db']:g}"
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", video_src, "-i", audio_wav,
        "-map", "0:v:0", "-map", "1:a:0", "-af", af,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"громкость/сборка не удалась: {err.strip()[-300:]}")


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


def run_normal(ffmpeg, ffprobe, deepfilter, args, cfg, work_dir):
    out = args.output
    model_dir = os.path.join(MODELS_DIR, str(cfg["denoise_model"]))
    if not os.path.isdir(model_dir):
        die(f"нет модели {model_dir} → blocked", 2)

    dur = ffprobe_duration(ffprobe, args.input)
    in_wav = os.path.join(work_dir, "in.wav")
    extract_wav(ffmpeg, args.input, in_wav)

    # до
    floor0 = noise_floor(ffmpeg, ffprobe, in_wav)
    voice0, _ = measure_loudness(ffmpeg, in_wav, cfg)

    # DeepFilterNet
    enhanced, dfn_sec = run_deepfilter(deepfilter, model_dir, in_wav,
                                       os.path.join(work_dir, "dfn"))

    # после (шумодав, до громкости)
    floor1 = noise_floor(ffmpeg, ffprobe, enhanced)
    voice1, _ = measure_loudness(ffmpeg, enhanced, cfg)

    # громкость + сборка
    loudnorm_remux(ffmpeg, args.input, enhanced, out, cfg)
    i2, tp2 = measure_loudness(ffmpeg, out, cfg)

    audio = {
        "duration": round(dur, 2),
        "denoise": str(cfg["denoise_model"]),
        "dfn_seconds": dfn_sec,
        "noise_floor_before": floor0, "noise_floor_after": floor1,
        "voice_before": voice0, "voice_after": voice1,
        "loudness_final": i2, "peak_final": tp2,
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (DeepFilterNet → громкость) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Модель:      {cfg['denoise_model']}")
    print(f"DeepFilterNet считал: {dfn_sec} с (звука {round(dur, 1)} с)")
    print(f"ФОН в тихом месте:  {_fmt(floor0)} → {_fmt(floor1)} dB   ({_delta(floor0, floor1)} dB)")
    print(f"УРОВЕНЬ ГОЛОСА:     {_fmt(voice0)} → {_fmt(voice1)} LUFS  ({_delta(voice0, voice1)} dB)")
    print(f"Громкость (итог):   {_fmt(i2)} LUFS  (цель {cfg['target_lufs']:g})")
    print(f"Пик (итог):         {_fmt(tp2)} dBTP  (потолок {cfg['true_peak_db']:g})")
    if tp2 is not None and tp2 > cfg["true_peak_db"]:
        print(f"⚠️ Пик итога {tp2} выше потолка {cfg['true_peak_db']:g} — возможен клиппинг.")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=" * 55)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    args = ap.parse_args()

    cfg = load_config()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    deepfilter = shutil.which("deepFilter")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)
    if not deepfilter:
        die("нет deepFilter (pip install deepfilternet==0.5.6 torch==2.0.1 torchaudio==2.0.2) → blocked", 3)
    if not os.path.exists(args.input):
        die(f"нет входного файла: {args.input}", 2)

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    try:
        run_normal(ffmpeg, ffprobe, deepfilter, args, cfg, work_dir)
    except RuntimeError as e:
        die(f"{e} → blocked", 2)


if __name__ == "__main__":
    main()
