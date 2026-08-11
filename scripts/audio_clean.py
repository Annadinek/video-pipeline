#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика. ДВА ШАГА, СТРОГО ПО ПОРЯДКУ:
  1) ШУМОДАВ  — сначала убираем фон комнаты (afftdn), ДО громкости.
  2) ГРОМКОСТЬ — loudnorm (2 прохода) уже на чистом звуке.

Почему такой порядок (правка Анны): если сначала тянуть громкость, loudnorm
поднимает всё разом — вместе с голосом растёт и фон комнаты, звук становится
гулким («из ведра»). Поэтому шум давим ПЕРВЫМ, на тихом звуке, и только потом
поднимаем громкость на уже чистом сигнале.

Проверка цифрами — по фону в самом тихом участке (там, где Анна молчит или
голос проседает). Меряем НЕ порогом тишины (у гулкой комнаты фон высокий,
порог ничего не ловит), а напрямую: режем звук на окна ~0.5 с, astats считает
RMS каждого окна. На ИСХОДНИКЕ находим самое тихое реальное окно и запоминаем
его НОМЕР; фон после шумодава и в итоге меряем в ТОМ ЖЕ окне (по номеру) —
сравнение честное, один и тот же момент времени.
  - если после шумодава фон не упал на floor_drop_target_db — шумодав слабый,
    усиливаем сам (gentle→low→medium→strong).

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  denoise               on/off
  denoise_strength      gentle/low/medium/strong  (по умолчанию medium)
  target_lufs           целевая громкость (эталон -9.3)
  true_peak_db          потолок пика (безопасно -1)
  floor_drop_target_db  на сколько хотим опустить фон шумодавом (умолч. 6)

Только Python 3, ffmpeg и стандартная библиотека. Исходник не трогаем.
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
    "denoise_strength": "medium",   # gentle не справлялся с фоном — по умолчанию сильнее
    "target_lufs": -9.3,            # как у эталона Анны
    "true_peak_db": -1,             # эталон ~0.1; безопасно -1 (0 клиппит)
    "floor_drop_target_db": 6,      # на сколько хотим опустить фон в тихих участках шумодавом
}
LOUDNORM_LRA = 11        # целевой разброс громкости для loudnorm
FLOOR_WIN_SEC = 0.5      # длина окна замера фона (с); в семплы переводим по частоте файла
FLOOR_SILENCE_DB = -120  # ниже этого — цифровая тишина (правка/пустое окно), не фон комнаты

# Шумодав afftdn от мягкого к сильному. nr — сила подавления (больше = сильнее),
# nf — порог шума в дБ. От gentle к strong фон в паузах давится всё сильнее.
STRENGTH = {
    "gentle": "afftdn=nr=3:nf=-32",
    "low":    "afftdn=nr=6:nf=-30",
    "medium": "afftdn=nr=12:nf=-27",
    "strong": "afftdn=nr=24:nf=-24",
}
LEVELS = ["gentle", "low", "medium", "strong"]  # порядок усиления


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
    """Стартовый уровень шумодава: 'off' | 'gentle' | 'low' | 'medium' | 'strong'."""
    if str(cfg["denoise"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["denoise_strength"]).strip().lower()
    return lv if lv in STRENGTH else "medium"


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


def ffprobe_sample_rate(ffprobe, path):
    """Частота дискретизации звука (Гц). Не смогли прочитать → 48000."""
    _, out, _ = run([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate", "-of", "csv=p=0", path,
    ])
    try:
        sr = int(out.strip())
        return sr if sr > 0 else 48000
    except (ValueError, AttributeError):
        return 48000


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
    """Вернуть (I, TP, LRA) файла — для отчёта громкости до/после."""
    m = loudnorm_measure(ffmpeg, path, "", cfg)
    return (round(float(m["input_i"]), 1),
            round(float(m["input_tp"]), 1),
            round(float(m["input_lra"]), 1))


def window_rms(ffmpeg, path, win_samples):
    """
    RMS каждого окна ~win_samples (dB), по порядку. astats со сбросом на каждое
    окно + ametadata print. Битые/тихие окна дают '-inf' → храним как -inf,
    позиция окна сохраняется (номера окон совпадают у исходника и результата).
    """
    _, out, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-af", (f"asetnsamples=n={win_samples}:p=0,astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"),
        "-f", "null", "-",
    ])
    vals = []
    for x in re.findall(r"RMS_level=(\S+)", out + "\n" + err):
        try:
            vals.append(float(x))   # '-inf' → -inf, не падаем
        except ValueError:
            vals.append(float("-inf"))
    return vals


def floor_pick(vals):
    """(номер, значение dB) самого тихого РЕАЛЬНОГО окна (цифровую тишину пропускаем)."""
    best_i, best_v = None, None
    for i, v in enumerate(vals):
        if v > FLOOR_SILENCE_DB and (best_v is None or v < best_v):
            best_i, best_v = i, v
    return best_i, (round(best_v, 1) if best_v is not None else None)


def floor_at(vals, idx):
    """Фон в окне №idx (dB). Нет окна/цифровая тишина → None."""
    if idx is None or idx >= len(vals):
        return None
    v = vals[idx]
    return round(v, 1) if v > FLOOR_SILENCE_DB else None


def denoise_to_file(ffmpeg, src, out, level):
    """Только шумодав (afftdn нужного уровня) → отдельный файл. Громкость не трогаем."""
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-af", STRENGTH[level],
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"шумодав ({level}) не удался: {err.strip()[-300:]}")


def process(ffmpeg, ffprobe, src, out, cfg, work_dir):
    """
    Два шага: (1) шумодав с проверкой фона и авто-усилением, (2) громкость.
    Фон меряем в одном и том же окне (самом тихом на исходнике) у всех версий.
    Возвращает словарь с цифрами для отчёта и pipeline.json.
    """
    target_drop = float(cfg["floor_drop_target_db"])
    win = max(1, round(FLOOR_WIN_SEC * ffprobe_sample_rate(ffprobe, src)))

    # ШАГ 0. Самое тихое реальное окно исходника — его номер запоминаем.
    idx, floor_in = floor_pick(window_rms(ffmpeg, src, win))

    # ШАГ 1. Шумодав ДО громкости, с авто-усилением по фону (в том же окне).
    level0 = denoise_level(cfg)
    ladder = []  # [(level, floor)] — что пробовали
    if level0 == "off":
        used_level, denoised, floor_dn = "off", src, floor_in
    else:
        order = LEVELS[LEVELS.index(level0):] if level0 in LEVELS else LEVELS[LEVELS.index("medium"):]
        used_level = denoised = floor_dn = None
        for lvl in order:
            f = os.path.join(work_dir, f"denoised_{lvl}.mp4")
            denoise_to_file(ffmpeg, src, f, lvl)
            fl = floor_at(window_rms(ffmpeg, f, win), idx)
            ladder.append((lvl, fl))
            used_level, denoised, floor_dn = lvl, f, fl
            drop = (floor_in - fl) if (floor_in is not None and fl is not None) else None
            if drop is not None and drop >= target_drop:
                break  # фон упал достаточно — дальше не усиливаем

    # ШАГ 2. Громкость (2 прохода) на уже чистом звуке.
    i0, tp0, _ = measure_loudness(ffmpeg, src, cfg)
    measured = loudnorm_measure(ffmpeg, denoised, "", cfg)
    loudnorm_apply(ffmpeg, denoised, "", measured, out, cfg)
    i1, tp1, lra1 = measure_loudness(ffmpeg, out, cfg)
    floor_final = floor_at(window_rms(ffmpeg, out, win), idx)

    return {
        "floor_in": floor_in,
        "floor_denoised": floor_dn,
        "floor_final": floor_final,
        "drop_target_db": target_drop,
        "denoise": used_level,
        "ladder": ladder,
        "loudness_before": i0, "loudness_after": i1,
        "peak_before": tp0, "peak_after": tp1,
        "lra_after": lra1,
    }


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
    """b относительно a, в dB (со знаком). Меньше (тише) = отрицательное."""
    if a is None or b is None:
        return "—"
    return f"{b - a:+.1f} dB"


def run_normal(ffmpeg, ffprobe, args, cfg, work_dir):
    """Один файл: шумодав (с проверкой фона) → громкость. Цифры в отчёт."""
    out = args.output
    dur = ffprobe_duration(ffprobe, args.input)
    r = process(ffmpeg, ffprobe, args.input, out, cfg, work_dir)

    fi, fd, ff = r["floor_in"], r["floor_denoised"], r["floor_final"]
    lvl = r["denoise"]
    audio = {
        "duration": round(dur, 2),
        "noise_floor_in": fi,             # фон в тихом участке: исходник (эталон)
        "noise_floor_after_denoise": fd,  # после шумодава, ДО громкости
        "noise_floor_final": ff,          # итог, после громкости
        "loudness_before": r["loudness_before"], "loudness_after": r["loudness_after"],
        "peak_before": r["peak_before"], "peak_after": r["peak_after"],
        "lra_after": r["lra_after"],
        "denoise": lvl,
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (сначала шум, потом громкость) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print("ФОН В САМОМ ТИХОМ УЧАСТКЕ (там, где ты молчишь; тише = ниже число):")
    print(f"  исходник (твой эталон): {_fmt(fi)} dB")
    print(f"  после шумодава:         {_fmt(fd)} dB   ({_delta(fi, fd)} к исходнику)")
    print(f"  итог (после громкости): {_fmt(ff)} dB   ({_delta(fi, ff)} к исходнику)")
    if r["ladder"] and len(r["ladder"]) > 1:
        steps = " → ".join(f"{lv}:{_fmt(fl)}dB" for lv, fl in r["ladder"])
        print(f"Усиление:    {steps}  (усиливал, пока фон не упал на ≥{r['drop_target_db']} dB)")
    print(f"Шумодав:     {lvl}" + (f"  ({STRENGTH[lvl]})" if lvl != "off" else ""))
    print(f"Громкость:   {r['loudness_before']} → {r['loudness_after']} LUFS   (цель {cfg['target_lufs']})")
    print(f"Пик:         {r['peak_before']} → {r['peak_after']} dBTP  (потолок {cfg['true_peak_db']})")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён (current.audio)' if wrote else 'не трогал (нет current с этим id)'}")

    # Честная проверка: упал ли фон достаточно (только если шумодав вообще включён).
    if lvl != "off" and fi is not None and fd is not None:
        drop = fi - fd
        if drop < r["drop_target_db"]:
            print(f"⚠️ Фон упал только на {drop:.1f} dB (хотели ≥{r['drop_target_db']}). "
                  f"Даже '{lvl}' не добил. Похоже, это не широкополосный шум, а низкая "
                  f"гулкость (бочка) — её afftdn не берёт. Лучший рычаг тогда: срез низов "
                  f"(highpass) + вырез полосы ~200–400 Гц ДО громкости. Скажи — добавлю.")
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
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    run_normal(ffmpeg, ffprobe, args, cfg, work_dir)


if __name__ == "__main__":
    main()
