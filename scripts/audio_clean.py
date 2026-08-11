#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика. ЧИСТИМ, ПОТОМ ГРОМКОСТЬ.

Цепочка по решению Анны (гулкость и голос в одной полосе — резать нельзя):
  1) highpass 80 Гц         — срез самых низов (рокот/гул), голос не задевает;
  2) arnndn с моделью       — нейросетевой шумодав речи (rnnoise): давит фон,
                              голос сохраняет (в отличие от выреза полос);
  3) громкость ФИКСИРОВАННЫМ гейном (volume) до target_lufs (умолч. -14),
     ограничен так, чтобы истинный пик остался ниже true_peak_db.

Вырез полос 200–400 Гц УБРАН: он попадал в основной тон голоса, звук «из колодца».

Модели rnnoise — в presets/rnnoise/*.rnnn (GregorR/rnnoise-models, public domain).
Для речи + шум записи (комната) по README лучшие: sh (somnolent-hogwash) и
bd (beguiling-drafter). Выбор — режимом --models (сравнить все) + на слух Анны.

Если ffmpeg без фильтра arnndn или модели нет — откат на afftdn (с предупреждением).

Итог собирается ОДНИМ проходом ffmpeg (одно сжатие). Фон в самом тихом участке
меряем по окнам на каждом шаге (исходник → highpass → шумодав → громкость).

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4  (один файл)
Режим --models: по файлу clip_<модель>.mp4 на каждую модель, для выбора.

Настройки — presets/audio.json (нет файла/сломан → умолчания, не падаем):
  highpass_hz     срез низов, Гц (умолч. 80)
  denoise         on/off
  denoise_model   имя .rnnn в presets/rnnoise/ (умолч. sh.rnnn)
  target_lufs     целевая громкость (умолч. -14)
  true_peak_db    потолок пика (безопасно -1)

Только Python 3, ffmpeg (желательно с фильтром arnndn) и стандартная библиотека.
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
MODELS_DIR = os.path.join(ROOT, "presets", "rnnoise")

DEFAULTS = {
    "highpass_hz": 80,             # срез низов, голос не задевает
    "denoise": "on",
    "denoise_model": "sh.rnnn",    # somnolent-hogwash — речь + шум записи (по README)
    "target_lufs": -14,           # норма площадок; решение Анны
    "true_peak_db": -1,           # безопасно -1 (0 клиппит)
}
NUMERIC_KEYS = ["highpass_hz", "target_lufs", "true_peak_db"]
LOUDNORM_LRA = 11        # для замера громкости (loudnorm print, без применения)
FLOOR_WIN_SEC = 0.5      # окно замера фона (с); в семплы переводим по частоте файла
FLOOR_SILENCE_DB = -120  # ниже этого — цифровая тишина, не фон комнаты
PEAK_HEADROOM_DB = 1.0   # запас по пику под фиксированный гейн
AFFTDN_FALLBACK = "afftdn=nr=10:nf=-28"  # откат, если arnndn/модель недоступны


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


def _pre(af):
    return (af + ",") if af else ""


def _esc_path(p):
    """Экранировать метасимволы filtergraph в пути к модели (: , ' \\ [ ] ; пробел)."""
    return re.sub(r"([\\':,\[\]; ])", r"\\\1", p)


def has_arnndn(ffmpeg):
    """Есть ли в ffmpeg фильтр arnndn."""
    _, out, err = run([ffmpeg, "-hide_banner", "-filters"])
    return "arnndn" in (out + err)


def list_models():
    """Список моделей presets/rnnoise/*.rnnn (по имени)."""
    if not os.path.isdir(MODELS_DIR):
        return []
    return sorted(f for f in os.listdir(MODELS_DIR) if f.endswith(".rnnn"))


def denoise_filter(cfg, model, arnndn_ok):
    """(фильтр, метка, откат?). arnndn с моделью, если есть и файл, и фильтр; иначе afftdn."""
    if str(cfg["denoise"]).strip().lower() == "off":
        return "", "выкл", False
    path = os.path.join(MODELS_DIR, model)
    if arnndn_ok and os.path.exists(path):
        return f"arnndn=m={_esc_path(path)}", model, False
    reason = "нет модели" if not os.path.exists(path) else "ffmpeg без arnndn"
    return AFFTDN_FALLBACK, f"afftdn (откат: {reason})", True


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
    chain = _pre(pre) + (
        f"loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']}:LRA={LOUDNORM_LRA}"
        ":print_format=json"
    )
    _, _, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", chain, "-f", "null", "-"])
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"loudnorm не отдал измерения: {err.strip()[-400:]}")
    return json.loads(err[start:end + 1])


def measure_loudness(ffmpeg, path, cfg, pre=""):
    """Вернуть (I, TP, LRA). pre — фильтры до замера."""
    m = loudnorm_measure(ffmpeg, path, cfg, pre)
    return (round(float(m["input_i"]), 1),
            round(float(m["input_tp"]), 1),
            round(float(m["input_lra"]), 1))


def window_rms(ffmpeg, path, win_samples, pre=""):
    """RMS каждого окна ~win_samples (dB), по порядку, с префиксом фильтров pre."""
    af = _pre(pre) + (
        f"asetnsamples=n={win_samples}:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    _, out, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", af, "-f", "null", "-"])
    vals = []
    for x in re.findall(r"RMS_level=(\S+)", out + "\n" + err):
        try:
            vals.append(float(x))
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


def source_context(ffmpeg, ffprobe, src, cfg):
    """Замеры исходника, не зависящие от модели (считаем один раз на весь прогон)."""
    win = max(1, round(FLOOR_WIN_SEC * ffprobe_sample_rate(ffprobe, src)))
    idx, floor_src = floor_pick(window_rms(ffmpeg, src, win))
    i_src, tp_src, _ = measure_loudness(ffmpeg, src, cfg)
    return {"win": win, "idx": idx, "floor_src": floor_src, "i_src": i_src, "tp_src": tp_src}


def process(ffmpeg, src, out, cfg, ctx, arnndn_ok, model=None):
    """
    highpass → шумодав → фикс-гейн. Фон по шагам — анализом с префиксом фильтров, итог
    одним проходом. ctx — замеры исходника (source_context). Возвращает цифры.
    """
    model = model or cfg["denoise_model"]
    win, idx = ctx["win"], ctx["idx"]

    def floor(pre):
        return floor_at(window_rms(ffmpeg, src, win, pre), idx)

    hp = f"highpass=f={cfg['highpass_hz']:g}"
    dn_filter, dn_label, fallback = denoise_filter(cfg, model, arnndn_ok)
    clean = hp + (f",{dn_filter}" if dn_filter else "")

    floor_hp = floor(hp)
    floor_dn = floor(clean)

    # громкость: фиксированный гейн по чистому сигналу, с запасом по пику
    i_pre, tp_pre, _ = measure_loudness(ffmpeg, src, cfg, pre=clean)
    gain_loud = cfg["target_lufs"] - i_pre
    gain_peak = cfg["true_peak_db"] - tp_pre - PEAK_HEADROOM_DB
    gain = round(min(gain_loud, gain_peak), 1)
    limited_by = "пик" if gain_peak <= gain_loud else "громкость"

    apply_af(ffmpeg, src, out, f"{clean},volume={gain}dB")
    floor_out = floor(f"{clean},volume={gain}dB")
    i_out, tp_out, lra_out = measure_loudness(ffmpeg, out, cfg)

    return {
        "floor_src": ctx["floor_src"], "floor_hp": floor_hp,
        "floor_dn": floor_dn, "floor_out": floor_out,
        "denoise": dn_label, "fallback": fallback, "gain_db": gain, "gain_limited_by": limited_by,
        "peak_over": tp_out > cfg["true_peak_db"],
        "loudness_before": ctx["i_src"], "loudness_after": i_out,
        "peak_before": ctx["tp_src"], "peak_after": tp_out, "lra_after": lra_out,
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
    if a is None or b is None:
        return "—"
    return f"{b - a:+.1f} dB"


def run_normal(ffmpeg, ffprobe, args, cfg):
    """Один файл: highpass → arnndn(модель) → громкость. Фон по шагам."""
    out = args.output
    dur = ffprobe_duration(ffprobe, args.input)
    ctx = source_context(ffmpeg, ffprobe, args.input, cfg)
    r = process(ffmpeg, args.input, out, cfg, ctx, has_arnndn(ffmpeg))

    fs, fh, fd, fo = r["floor_src"], r["floor_hp"], r["floor_dn"], r["floor_out"]
    audio = {
        "duration": round(dur, 2),
        "noise_floor_src": fs, "noise_floor_highpass": fh,
        "noise_floor_denoise": fd, "noise_floor_final": fo,
        "gain_db": r["gain_db"],
        "loudness_before": r["loudness_before"], "loudness_after": r["loudness_after"],
        "peak_before": r["peak_before"], "peak_after": r["peak_after"],
        "lra_after": r["lra_after"], "denoise": r["denoise"],
    }
    wrote = write_pipeline(out, audio)

    print("=== ОТЧЁТ audio_clean (highpass → arnndn → громкость) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print("ФОН В САМОМ ТИХОМ УЧАСТКЕ по шагам (тише = ниже число):")
    print(f"  исходник:              {_fmt(fs)} dB")
    print(f"  после highpass {cfg['highpass_hz']:g} Гц:  {_fmt(fh)} dB   ({_delta(fs, fh)})")
    print(f"  после шумодава:        {_fmt(fd)} dB   ({_delta(fh, fd)})")
    print(f"  после громкости:       {_fmt(fo)} dB   ({_delta(fd, fo)})")
    print(f"ИТОГ фон: {_fmt(fs)} → {_fmt(fo)} dB   ({_delta(fs, fo)} к исходнику)")
    print(f"Шумодав:     {r['denoise']}" + ("  ⚠️ ОТКАТ" if r["fallback"] else ""))
    print(f"Гейн:        {r['gain_db']:+g} dB (фиксированный, ограничен: {r['gain_limited_by']})")
    print(f"Громкость:   {r['loudness_before']} → {r['loudness_after']} LUFS  (цель {cfg['target_lufs']:g})")
    print(f"Пик:         {r['peak_before']} → {r['peak_after']} dBTP  (потолок {cfg['true_peak_db']:g})")
    if r["peak_over"]:
        print(f"⚠️ Пик итога выше потолка {cfg['true_peak_db']:g} — возможен клиппинг.")
    print(f"Файл:        {out}")
    print(f"pipeline.json: {'обновлён' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=" * 55)


def run_models(ffmpeg, ffprobe, args, cfg):
    """Сравнить все модели presets/rnnoise/*.rnnn — по файлу на каждую, фон по шагам."""
    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    models = list_models()
    if not models:
        die("нет моделей в presets/rnnoise/*.rnnn → blocked", 2)
    arnndn_ok = has_arnndn(ffmpeg)
    ctx = source_context(ffmpeg, ffprobe, args.input, cfg)

    print("=== СРАВНЕНИЕ МОДЕЛЕЙ arnndn (30 с, выбрать лучшую для речи) ===")
    print(f"Конфиг:      {cfg['_config_status']}")
    print(f"Цепочка: highpass {cfg['highpass_hz']:g} Гц → arnndn(модель) → гейн до {cfg['target_lufs']:g} LUFS")
    if str(cfg["denoise"]).strip().lower() == "off":
        print("⚠️ denoise=off — шумодав выключен, файлы моделей будут одинаковыми. Включи denoise.")
    if not arnndn_ok:
        print("⚠️ ffmpeg без фильтра arnndn — сравнить нельзя, всё уходит в afftdn.")
    print("-" * 64)
    rows = []
    for m in models:
        out = os.path.join(out_dir, f"clip_{m[:-5]}.mp4")   # убрать .rnnn
        r = process(ffmpeg, args.input, out, cfg, ctx, arnndn_ok, model=m)
        drop = (round(r["floor_out"] - r["floor_src"], 1)
                if r["floor_src"] is not None and r["floor_out"] is not None else None)
        print(f"[{m}]")
        print(f"  фон: исходник {_fmt(r['floor_src'])} → highpass {_fmt(r['floor_hp'])} "
              f"→ arnndn {_fmt(r['floor_dn'])} → громкость {_fmt(r['floor_out'])} dB "
              f"(итог {_fmt(drop)} dB)")
        print(f"  громкость {r['loudness_after']} LUFS; пик {r['peak_after']} dBTP; гейн {r['gain_db']:+g} dB")
        print(f"  файл: {out}")
        print("-" * 64)
        rows.append({"model": m, "floor_src": r["floor_src"], "floor_final": r["floor_out"],
                     "drop_db": drop, "loudness": r["loudness_after"], "peak": r["peak_after"]})
    print("По README репо для речи + шум записи (комната) лучшие: sh, bd. Голос — на слух Анны.")
    print("models =", json.dumps(rows, ensure_ascii=False))
    print("=" * 64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    ap.add_argument("--models", action="store_true",
                    help="сравнить все модели presets/rnnoise/*.rnnn (по файлу на модель)")
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

    if args.models:
        run_models(ffmpeg, ffprobe, args, cfg)
    else:
        run_normal(ffmpeg, ffprobe, args, cfg)


if __name__ == "__main__":
    main()
