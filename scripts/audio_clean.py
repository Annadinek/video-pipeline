#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика.

Вход:  outputs/ready/[id]/clip.mp4
Выход (обычный режим): два файла для сравнения на слух —
       outputs/ready/[id]/clip_audio_clean.mp4      (с шумоподавлением)
       outputs/ready/[id]/clip_audio_nodenoise.mp4  (без шумоподавления)

ПОРЯДОК ФИЛЬТРОВ (строгий, менять нельзя):
  1) highpass=f=80  — обрезка низов (уличный гул) ДО шумодава
  2) afftdn         — шумоподавление (уровень low/medium/high или off)
  3) compand        — компрессия
  4) alimiter       — потолок пика, чтобы loudnorm не сжимал звук второй раз
  5) loudnorm       — целевая громкость (2 прохода)
Резка пауз — ПОСЛЕ всех фильтров, чтобы метки времени не сбились.

Настройки — presets/audio.json (сломан/нет файла → умолчания, не падаем):
  denoise           on/off
  denoise_strength  low/medium/high   (сила afftdn от мягкого к сильному)
  pauses            on/off
  target_lufs, true_peak_db           (читаются отсюда, не зашиты в код)

Режим сравнения (--compare, включается только в audio-test.yml):
  берёт 30-секундный отрезок с речью и прогоняет его четырежды —
  без шумодава, low, medium, high — кладёт четыре файла с понятными именами.

Отчёт цифрами: громкость до/после, пик до/после, разброс LRA, уровень
шумодава, параметры компрессии.

Только Python 3, ffmpeg и стандартная библиотека. Исходник не трогает.
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
    "denoise_strength": "high",
    "pauses": "on",
    "silence_threshold_db": -35,
    "silence_min_sec": 0.45,
    "keep_edge_sec": 0.12,
    "target_lufs": -14,
    "true_peak_db": -1,
    # деэссер против свиста на шипящих (с/ш/щ/ж)
    "deesser": "off",
    "deesser_intensity": 0.4,   # сила подавления (0..1)
    "deesser_freq": 0.25,       # частота разделения (доля от Найквиста; ~6 кГц при 48к)
    # вырез гулкости комнаты ("бочка") в полосе ~200–500 Гц
    "deboom": "off",
    "deboom_freq": 300,         # центр выреза, Гц
    "deboom_q": 1.0,            # добротность (ширина полосы)
    "deboom_gain": -5,          # глубина выреза, дБ
}
LOUDNORM_LRA = 11  # целевой разброс громкости для loudnorm

# Порядок и значения фиксированы заданием.
HIGHPASS = "highpass=f=80"
# Тихое поднимаем умеренно (+8 дБ на уровне -30, а не +12): сильный подъём
# сплющивал разброс громкости и делал голос плоским.
COMPAND = "compand=attacks=0.02:decays=0.3:points=-70/-70|-30/-22|-15/-11|0/-5:gain=0"
# Потолок пика -3 dBFS. level=disabled — лимитер не подтягивает громкость обратно,
# иначе запас по пику пропадает и loudnorm снова уходит в динамический режим.
LIMITER = "alimiter=limit=0.7:level=disabled"
# afftdn от мягкого к сильному. Отправная точка; уточняется по режиму сравнения.
# У high nr=16:nf=-25 вместо nr=24:nf=-20 — фон тише, чем на medium,
# а верхние частоты голоса (4-8 кГц) почти не страдают.
STRENGTH = {
    "low":    "afftdn=nr=6:nf=-30",
    "medium": "afftdn=nr=12:nf=-25",
    "high":   "afftdn=nr=16:nf=-25",
}
COMPARE_LEVELS = ["off", "low", "medium", "high"]
COMPARE_NAMES = {"off": "nodenoise", "low": "low", "medium": "medium", "high": "high"}
SPEECH_WINDOW_SEC = 30


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
    """presets/audio.json поверх умолчаний. Сломан/нет файла → умолчания, не падаем."""
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
    cfg["pauses_on"] = str(cfg["pauses"]).strip().lower() != "off"
    cfg["_config_status"] = status
    return cfg


def denoise_level(cfg):
    """Итоговый уровень шумодава: 'off' | 'low' | 'medium' | 'high'."""
    if str(cfg["denoise"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["denoise_strength"]).strip().lower()
    return lv if lv in STRENGTH else "medium"


def deboom_filter(cfg):
    """Вырез гулкости комнаты ('бочка') в полосе ~200–500 Гц — peaking EQ с провалом."""
    return (f"equalizer=f={cfg['deboom_freq']}:width_type=q"
            f":w={cfg['deboom_q']}:g={cfg['deboom_gain']}")


def deesser_filter(cfg):
    """Деэссер против свиста на шипящих."""
    return f"deesser=i={cfg['deesser_intensity']}:m=0.5:f={cfg['deesser_freq']}:s=o"


def build_chain(level, cfg, deboom=False, deesser=False):
    """
    Цепочка ДО loudnorm. Порядок:
    highpass=80 [→ вырез гула] [→ afftdn] [→ деэссер] → compand → лимитер.
    Вырез гула — до шумодава (чистим низ), деэссер — после (гасим звон, если он от шумодава).
    """
    parts = [HIGHPASS]
    if deboom:
        parts.append(deboom_filter(cfg))
    if level != "off":
        parts.append(STRENGTH[level])
    if deesser:
        parts.append(deesser_filter(cfg))
    parts.append(COMPAND)
    parts.append(LIMITER)
    return ",".join(parts)


def build_pre(level, cfg=None):
    """Обычный режим: строим цепочку по конфигу (deboom/deesser включаются флагами в audio.json)."""
    if cfg is None:
        return build_chain(level, DEFAULTS, deboom=False, deesser=False)
    deboom = str(cfg.get("deboom", "off")).strip().lower() != "off"
    deesser = str(cfg.get("deesser", "off")).strip().lower() != "off"
    return build_chain(level, cfg, deboom=deboom, deesser=deesser)


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
    """1-й проход loudnorm: измерить. pre — фильтры до loudnorm ('' = ничего)."""
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


def detect_silences(ffmpeg, path, cfg):
    _, _, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-af", f"silencedetect=noise={cfg['silence_threshold_db']}dB:d={cfg['silence_min_sec']}",
        "-f", "null", "-",
    ])
    starts, ends = [], []
    for line in err.splitlines():
        m = re.search(r"silence_start:\s*([-\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end:\s*([-\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))
    return [(s, ends[i] if i < len(ends) else None) for i, s in enumerate(starts)]


def keep_segments(silences, duration, cfg):
    pad = cfg["keep_edge_sec"]
    cuts = []
    for s, e in silences:
        end = duration if e is None else e
        cut_s, cut_e = s + pad, end - pad
        if cut_e > cut_s:
            cuts.append((cut_s, cut_e))
    cuts.sort()
    keep, cursor = [], 0.0
    for cut_s, cut_e in cuts:
        if cut_s > cursor:
            keep.append((cursor, cut_s))
        cursor = max(cursor, cut_e)
    if duration - cursor > 0.01:
        keep.append((cursor, duration))
    return keep, len(cuts)


def build_cut(ffmpeg, src, keep, out, work_dir, tag):
    """Пересобрать видео из отрезков keep (trim+concat одним проходом). Звук и картинка не разъезжаются."""
    parts, labels = [], []
    for i, (s, e) in enumerate(keep):
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keep)}:v=1:a=1[v][a]"
    graph_file = os.path.join(work_dir, f"cut_{tag}.txt")
    with open(graph_file, "w", encoding="utf-8") as f:
        f.write(graph)
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-filter_complex_script", graph_file,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"резка пауз не удалась: {err.strip()[-400:]}")


def process_one(ffmpeg, ffprobe, src, out, level, cfg, work_dir, tag):
    """
    Полная цепочка: highpass[,afftdn],compand,loudnorm (2 прохода) → затем резка пауз.
    Возвращает pauses_cut.
    """
    pre = build_pre(level, cfg)
    measured = loudnorm_measure(ffmpeg, src, pre, cfg)
    filtered = os.path.join(work_dir, f"filtered_{tag}.mp4")
    loudnorm_apply(ffmpeg, src, pre, measured, filtered, cfg)

    if not cfg["pauses_on"]:
        shutil.copyfile(filtered, out)
        return 0
    dur = ffprobe_duration(ffprobe, filtered)
    silences = detect_silences(ffmpeg, filtered, cfg)
    keep, pauses_cut = keep_segments(silences, dur, cfg)
    if pauses_cut > 0 and keep:
        build_cut(ffmpeg, filtered, keep, out, work_dir, tag)
    else:
        shutil.copyfile(filtered, out)
        pauses_cut = 0
    return pauses_cut


def mean_volume(ffmpeg, path, start, dur):
    """Средняя громкость окна [start, start+dur] в dB — для поиска речи."""
    _, _, err = run([
        ffmpeg, "-hide_banner", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
        "-i", path, "-af", "volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    return float(m.group(1)) if m else -999.0


def pick_speech_window(ffmpeg, ffprobe, path, dur=SPEECH_WINDOW_SEC):
    """Найти 30-секундное окно с речью = самое громкое в среднем. Вернуть (start, dur)."""
    total = ffprobe_duration(ffprobe, path)
    if total <= dur:
        return 0.0, total
    best_start, best_mean = 0.0, -1e9
    step = max(dur / 2.0, (total - dur) / 6.0)
    t = 0.0
    while t <= total - dur + 0.01:
        mv = mean_volume(ffmpeg, path, t, dur)
        if mv > best_mean:
            best_start, best_mean = t, mv
        t += step
    return best_start, dur


def extract_segment(ffmpeg, src, start, dur, out):
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
        "-i", src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"не удалось вырезать отрезок: {err.strip()[-300:]}")


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


def run_normal(ffmpeg, ffprobe, args, cfg, work_dir):
    """Два файла: с шумодавом (по конфигу) и без — для сравнения на слух."""
    out_clean = args.output
    out_nodenoise = os.path.join(os.path.dirname(out_clean), "clip_audio_nodenoise.mp4")
    level = denoise_level(cfg)

    i0, tp0, lra0 = measure_loudness(ffmpeg, args.input, cfg)
    dur_before = ffprobe_duration(ffprobe, args.input)

    pauses_cut = process_one(ffmpeg, ffprobe, args.input, out_clean, level, cfg, work_dir, "clean")
    process_one(ffmpeg, ffprobe, args.input, out_nodenoise, "off", cfg, work_dir, "nodenoise")

    i1, tp1, lra1 = measure_loudness(ffmpeg, out_clean, cfg)
    dur_after = ffprobe_duration(ffprobe, out_clean)

    audio = {
        "duration_before": round(dur_before, 2),
        "duration_after": round(dur_after, 2),
        "pauses_cut": pauses_cut,
        "loudness_before": i0, "loudness_after": i1,
        "peak_before": tp0, "peak_after": tp1,
        "lra_after": lra1,
        "denoise": level,
    }
    wrote = write_pipeline(out_clean, audio)

    print("=== ОТЧЁТ audio_clean ===")
    print(f"Конфиг:        {cfg['_config_status']}")
    print(f"Порядок:       highpass=80 → afftdn({level}) → compand → лимитер → loudnorm, паузы последними")
    print(f"Длительность:  {round(dur_before,2)} с → {round(dur_after,2)} с "
          f"(вырезано пауз: {pauses_cut})")
    print(f"Громкость:     {i0} → {i1} LUFS   (цель {cfg['target_lufs']})")
    print(f"Пик:           {tp0} → {tp1} dBTP  (потолок {cfg['true_peak_db']})")
    print(f"Разброс LRA:   {lra0} → {lra1} LU")
    print(f"Шумодав:       {level}" + (f"  ({STRENGTH[level]})" if level != 'off' else ""))
    print(f"Компрессия:    {COMPAND}")
    print(f"Лимитер:       {LIMITER}")
    print(f"Файлы:         {out_clean}")
    print(f"               {out_nodenoise}  (без шумодава, для сравнения)")
    print(f"pipeline.json: {'обновлён (current.audio)' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=========================")


def run_compare(ffmpeg, ffprobe, args, cfg, work_dir):
    """30-секундный отрезок с речью, прогнанный четырежды: без шумодава, low, medium, high."""
    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    start, dur = pick_speech_window(ffmpeg, ffprobe, args.input)
    segment = os.path.join(work_dir, "compare_segment.mp4")
    extract_segment(ffmpeg, args.input, start, dur, segment)

    i0, tp0, lra0 = measure_loudness(ffmpeg, segment, cfg)

    print("=== РЕЖИМ СРАВНЕНИЯ audio_clean ===")
    print(f"Конфиг:        {cfg['_config_status']}")
    print(f"Порядок:       highpass=80 → afftdn(<уровень>) → compand → лимитер → loudnorm, паузы последними")
    print(f"Отрезок:       {round(start,1)}–{round(start+dur,1)} с (самый громкий = речь), {round(dur,1)} с")
    print(f"Компрессия:    {COMPAND}")
    print(f"Лимитер:       {LIMITER}")
    print(f"До обработки:  {i0} LUFS, пик {tp0} dBTP, LRA {lra0} LU")
    print(f"Цель:          {cfg['target_lufs']} LUFS, пик ≤ {cfg['true_peak_db']} dBTP")
    print("-" * 64)
    print(f"{'уровень':<10}{'громкость':>12}{'пик dBTP':>12}{'LRA':>8}{'параметры afftdn':>22}")
    print("-" * 64)
    files = []
    for level in COMPARE_LEVELS:
        name = COMPARE_NAMES[level]
        out = os.path.join(out_dir, f"compare_{name}.mp4")
        process_one(ffmpeg, ffprobe, segment, out, level, cfg, work_dir, f"cmp_{name}")
        i1, tp1, lra1 = measure_loudness(ffmpeg, out, cfg)
        params = STRENGTH[level] if level != "off" else "—"
        print(f"{name:<10}{i1:>10} LUFS{tp1:>10}  {lra1:>6}  {params:>22}")
        files.append(out)
    print("-" * 64)
    for f in files:
        print("Файл:", f)
    print("===================================")


def band_level(ffmpeg, path, lo, hi):
    """Средняя громкость в полосе [lo,hi] Гц (dB) — для оценки звона/яркости."""
    _, _, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-af", f"highpass=f={lo},lowpass=f={hi},volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    return round(float(m.group(1)), 1) if m else None


def process_with_pre(ffmpeg, src, out, pre, cfg):
    """loudnorm 2 прохода с заданной цепочкой pre. Пауз не режем (для сравнения на слух)."""
    measured = loudnorm_measure(ffmpeg, src, pre, cfg)
    loudnorm_apply(ffmpeg, src, pre, measured, out, cfg)


def run_audiofix(ffmpeg, ffprobe, args, cfg, work_dir):
    """
    4 варианта звука на 30-секундном отрезке с речью (слушать с ТЕЛЕФОНА):
      1) current         — как сейчас (шумодав high)
      2) nodenoise       — без шумодава
      3) deesser         — как сейчас + деэссер против свиста
      4) deesser_deboom  — деэссер + вырез гулкости 200–500 Гц
    Плюс (5) denoise_low — тише шумодав, для сравнения off/low на слух.
    Цифрами: громкость, пик и полоса 4–8 кГц (там сидят звон/шипящие).
    """
    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    start, dur = pick_speech_window(ffmpeg, ffprobe, args.input)
    segment = os.path.join(work_dir, "seg.mp4")
    extract_segment(ffmpeg, args.input, start, dur, segment)

    i0, tp0, _ = measure_loudness(ffmpeg, segment, cfg)
    band0 = band_level(ffmpeg, segment, 4000, 8000)

    variants = [
        ("current",        build_chain("high", cfg, deboom=False, deesser=False)),
        ("nodenoise",      build_chain("off",  cfg, deboom=False, deesser=False)),
        ("deesser",        build_chain("high", cfg, deboom=False, deesser=True)),
        ("deesser_deboom", build_chain("high", cfg, deboom=True,  deesser=True)),
        ("denoise_low",    build_chain("low",  cfg, deboom=False, deesser=False)),
    ]

    print("=== ЗВУК: 4+1 варианта на послушать (телефон) ===")
    print(f"Конфиг:     {cfg['_config_status']}")
    print(f"Отрезок:    {round(start,1)}–{round(start+dur,1)} с (самый громкий = речь), {round(dur,1)} с")
    print(f"Деэссер:    {deesser_filter(cfg)}")
    print(f"Вырез гула: {deboom_filter(cfg)}")
    print(f"До обработки: {i0} LUFS, пик {tp0} dBTP, полоса 4–8 кГц {band0} dB")
    print("-" * 72)
    print(f"{'вариант':<16}{'громкость':>11}{'пик dBTP':>10}{'4–8 кГц dB':>12}   что это")
    print("-" * 72)
    what = {
        "current": "как сейчас (шумодав high)",
        "nodenoise": "без шумодава",
        "deesser": "+ деэссер",
        "deesser_deboom": "+ деэссер + вырез гула",
        "denoise_low": "шумодав low (для off/low)",
    }
    files, rows = [], []
    for name, pre in variants:
        out = os.path.join(out_dir, f"audiofix_{name}.mp4")
        process_with_pre(ffmpeg, segment, out, pre, cfg)
        i1, tp1, _ = measure_loudness(ffmpeg, out, cfg)
        band = band_level(ffmpeg, out, 4000, 8000)
        print(f"{name:<16}{i1:>9} LUFS{tp1:>9} {band:>10} dB   {what[name]}")
        files.append(out)
        rows.append({"variant": name, "loudness": i1, "peak": tp1, "band_4_8k": band})
    print("-" * 72)
    print("Полоса 4–8 кГц: чем выше, тем сильнее звон/шипящие. Сравни nodenoise vs denoise_low vs current —")
    print("если у current заметно выше, значит звон даёт шумодав.")
    for f in files:
        print("Файл:", f)
    print("audiofix =", json.dumps(rows, ensure_ascii=False))
    print("=" * 48)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    ap.add_argument("--compare", action="store_true",
                    help="режим сравнения: 30 с речи × (off/low/medium/high). Только для audio-test.")
    ap.add_argument("--audiofix", action="store_true",
                    help="4+1 варианта (current/nodenoise/deesser/deesser_deboom/denoise_low) на послушать.")
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
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    if args.audiofix:
        run_audiofix(ffmpeg, ffprobe, args, cfg, work_dir)
    elif args.compare:
        run_compare(ffmpeg, ffprobe, args, cfg, work_dir)
    else:
        run_normal(ffmpeg, ffprobe, args, cfg, work_dir)


if __name__ == "__main__":
    main()
