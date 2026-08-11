#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика. ЧИСТИМ, ПОТОМ ГРОМКОСТЬ.

Цепочка по решению Анны (гулкая комната, «бочка»):
  1) highpass ~80–100 Гц      — срез низов (рокот/гул комнаты);
  2) вырез полосы ~200–400 Гц — убираем гулкость («бочку»), equalizer с провалом;
  3) шумодав afftdn           — остаточный широкополосный шип;
  4) громкость ЛЁГКИМ ФИКСИРОВАННЫМ ГЕЙНОМ (volume), НЕ динамическим loudnorm —
     фиксированный гейн двигает голос и фон на одну и ту же величину и НЕ
     подтягивает фон вверх (динамический loudnorm поднимал фон → «бочка»).

Порядок: сначала чистим (1→2→3), только потом громкость (4). Гейн ограничен так,
чтобы истинный пик остался ниже true_peak_db (с запасом на пересжатие); если пик
всё равно вышел за потолок — печатаем предупреждение.

Итоговый файл собирается ОДНИМ проходом ffmpeg (одно сжатие AAC, без потерь на
пересжатии). Фон по шагам меряем анализом с префиксом фильтров (`-f null`, без
промежуточных файлов) в ОДНОМ И ТОМ ЖЕ окне (самом тихом на исходнике) — видно,
где именно падает: исходник → highpass → вырез → шумодав → громкость.

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  highpass_hz           срез низов, Гц (умолч. 90)
  deboom_hz             центр выреза гулкой полосы, Гц (умолч. 300)
  deboom_q              ширина выреза, добротность (умолч. 1.0)
  deboom_gain           глубина выреза, дБ (умолч. -6, со знаком минус)
  denoise               on/off
  denoise_strength      gentle/low/medium/strong (умолч. gentle — бочку берёт вырез)
  target_lufs           целевая громкость (эталон -9.3)
  true_peak_db          потолок пика (безопасно -1)

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
    "highpass_hz": 90,             # срез низов (рокот/гул), 80–100
    "deboom_hz": 300,             # центр выреза гулкой полосы (200–400)
    "deboom_q": 1.0,              # ширина выреза (добротность)
    "deboom_gain": -6,            # глубина выреза, дБ (минус = провал)
    "denoise": "on",
    "denoise_strength": "gentle",  # бочку берёт вырез; afftdn лёгкий, для остаточного шипа
    "target_lufs": -9.3,          # как у эталона Анны
    "true_peak_db": -1,           # эталон ~0.1; безопасно -1 (0 клиппит)
}
# Числовые ключи, которые участвуют в арифметике/фильтрах — приводим к float,
# чтобы строка в JSON не роняла обработку.
NUMERIC_KEYS = ["highpass_hz", "deboom_hz", "deboom_q", "deboom_gain",
                "target_lufs", "true_peak_db"]
LOUDNORM_LRA = 11        # для замера громкости (loudnorm print, без применения)
FLOOR_WIN_SEC = 0.5      # длина окна замера фона (с); в семплы переводим по частоте файла
FLOOR_SILENCE_DB = -120  # ниже этого — цифровая тишина (правка/пустое окно), не фон комнаты
PEAK_HEADROOM_DB = 1.0   # запас по пику под фиксированный гейн (округление + пересжатие)

# Шумодав afftdn от мягкого к сильному (шаг 3 — только остаточный шип).
STRENGTH = {
    "gentle": "afftdn=nr=3:nf=-32",
    "low":    "afftdn=nr=6:nf=-30",
    "medium": "afftdn=nr=12:nf=-27",
    "strong": "afftdn=nr=24:nf=-24",
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
    # числовые значения — к float, кривое значение → умолчание
    for k in NUMERIC_KEYS:
        try:
            cfg[k] = float(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    cfg["_config_status"] = status
    return cfg


def denoise_level(cfg):
    """Уровень шумодава: 'off' | 'gentle' | 'low' | 'medium' | 'strong'."""
    if str(cfg["denoise"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["denoise_strength"]).strip().lower()
    return lv if lv in STRENGTH else "gentle"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _prefix(af):
    """Префикс из фильтров для цепочки '-af' ('' → пусто)."""
    return (af + ",") if af else ""


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


def loudnorm_measure(ffmpeg, path, cfg, pre=""):
    """Замер громкости: loudnorm 1-й проход (print json). pre — фильтры до замера."""
    chain = _prefix(pre) + (
        f"loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']}:LRA={LOUDNORM_LRA}"
        ":print_format=json"
    )
    _, _, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", chain, "-f", "null", "-"])
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"loudnorm не отдал измерения: {err.strip()[-400:]}")
    return json.loads(err[start:end + 1])


def measure_loudness(ffmpeg, path, cfg, pre=""):
    """Вернуть (I, TP, LRA) — интегральная громкость, истинный пик, разброс. pre — фильтры."""
    m = loudnorm_measure(ffmpeg, path, cfg, pre)
    return (round(float(m["input_i"]), 1),
            round(float(m["input_tp"]), 1),
            round(float(m["input_lra"]), 1))


def window_rms(ffmpeg, path, win_samples, pre=""):
    """
    RMS каждого окна ~win_samples (dB), по порядку, с префиксом фильтров pre.
    astats со сбросом на каждое окно + ametadata print. '-inf' (тишина/битое) →
    храним как -inf, позиция окна сохраняется (номера окон совпадают у всех версий).
    """
    af = _prefix(pre) + (
        f"asetnsamples=n={win_samples}:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    _, out, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", af, "-f", "null", "-"])
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


def apply_af(ffmpeg, src, out, af):
    """Собрать выход одним проходом: цепочка фильтров af, видео копируем."""
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-af", af,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"сборка звука не удалась ({af}): {err.strip()[-300:]}")


def process(ffmpeg, ffprobe, src, out, cfg):
    """
    Фон по шагам меряем анализом с накопленным префиксом фильтров (без промежуточных
    файлов), итог собираем одним проходом. Возвращает словарь цифр для отчёта.
    """
    win = max(1, round(FLOOR_WIN_SEC * ffprobe_sample_rate(ffprobe, src)))
    idx, floor_src = floor_pick(window_rms(ffmpeg, src, win))

    def floor(pre):
        return floor_at(window_rms(ffmpeg, src, win, pre), idx)

    hp = f"highpass=f={cfg['highpass_hz']:g}"
    deboom = (f"equalizer=f={cfg['deboom_hz']:g}:width_type=q"
              f":w={cfg['deboom_q']:g}:g={cfg['deboom_gain']:g}")
    level = denoise_level(cfg)

    floor_hp = floor(hp)
    floor_db = floor(f"{hp},{deboom}")
    clean = f"{hp},{deboom}" + ("" if level == "off" else f",{STRENGTH[level]}")
    floor_dn = floor(clean)

    # 4) фиксированный гейн по ЧИСТОМУ (до гейна) сигналу, с запасом по пику
    i_pre, tp_pre, _ = measure_loudness(ffmpeg, src, cfg, pre=clean)
    gain_loud = cfg["target_lufs"] - i_pre
    gain_peak = cfg["true_peak_db"] - tp_pre - PEAK_HEADROOM_DB
    gain = round(min(gain_loud, gain_peak), 1)
    limited_by = "пик" if gain_peak <= gain_loud else "громкость"

    apply_af(ffmpeg, src, out, f"{clean},volume={gain}dB")   # один проход = одно сжатие
    floor_out = floor_at(window_rms(ffmpeg, out, win), idx)
    i_out, tp_out, lra_out = measure_loudness(ffmpeg, out, cfg)
    i_src, tp_src, _ = measure_loudness(ffmpeg, src, cfg)

    return {
        "floor_src": floor_src, "floor_hp": floor_hp, "floor_db": floor_db,
        "floor_dn": floor_dn, "floor_out": floor_out,
        "denoise": level, "gain_db": gain, "gain_limited_by": limited_by,
        "peak_over": tp_out > cfg["true_peak_db"],
        "loudness_before": i_src, "loudness_after": i_out,
        "peak_before": tp_src, "peak_after": tp_out, "lra_after": lra_out,
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


def run_normal(ffmpeg, ffprobe, args, cfg):
    """Один файл: highpass → вырез → шумодав → гейн. Фон на каждом шаге в отчёт."""
    out = args.output
    dur = ffprobe_duration(ffprobe, args.input)
    r = process(ffmpeg, ffprobe, args.input, out, cfg)

    fs, fh, fb, fd, fo = (r["floor_src"], r["floor_hp"], r["floor_db"],
                          r["floor_dn"], r["floor_out"])
    lvl = r["denoise"]
    dn_label = lvl if lvl != "off" else "выкл"
    audio = {
        "duration": round(dur, 2),
        "noise_floor_src": fs, "noise_floor_highpass": fh, "noise_floor_deboom": fb,
        "noise_floor_denoise": fd, "noise_floor_final": fo,
        "gain_db": r["gain_db"],
        "loudness_before": r["loudness_before"], "loudness_after": r["loudness_after"],
        "peak_before": r["peak_before"], "peak_after": r["peak_after"],
        "lra_after": r["lra_after"], "denoise": lvl,
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (чистим → громкость) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print("ФОН В САМОМ ТИХОМ УЧАСТКЕ по шагам (тише = ниже число):")
    print(f"  исходник:                {_fmt(fs)} dB")
    print(f"  после highpass {cfg['highpass_hz']:g} Гц:   {_fmt(fh)} dB   ({_delta(fs, fh)})")
    print(f"  после выреза {cfg['deboom_hz']:g} Гц:     {_fmt(fb)} dB   ({_delta(fh, fb)})")
    print(f"  после шумодава ({dn_label}):  {_fmt(fd)} dB   ({_delta(fb, fd)})")
    print(f"  после громкости:         {_fmt(fo)} dB   ({_delta(fd, fo)})")
    print(f"ИТОГ фон: {_fmt(fs)} → {_fmt(fo)} dB   ({_delta(fs, fo)} к исходнику)")
    print(f"Гейн:        {r['gain_db']:+g} dB (фиксированный, ограничен: {r['gain_limited_by']})")
    print(f"Громкость:   {r['loudness_before']} → {r['loudness_after']} LUFS  (цель {cfg['target_lufs']:g})")
    print(f"Пик:         {r['peak_before']} → {r['peak_after']} dBTP  (потолок {cfg['true_peak_db']:g})")
    if r["peak_over"]:
        print(f"⚠️ Пик итога {r['peak_after']} dBTP выше потолка {cfg['true_peak_db']:g} — возможен клиппинг. "
              f"Уменьши гейн или подними запас (PEAK_HEADROOM_DB).")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён (current.audio)' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("===============================================")


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
