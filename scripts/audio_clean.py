#!/usr/bin/env python3
"""
audio_clean.py — обработка звука ролика.

Вход:  outputs/ready/[id]/clip.mp4
Выход: outputs/ready/[id]/clip_audio_clean.mp4      (паузы + шум + громкость)
       outputs/ready/[id]/clip_audio_nodenoise.mp4  (то же, но БЕЗ шумоподавления —
                                                      чтобы Анна сравнила на слух)

Три шага:
1. Паузы. ffmpeg silencedetect, порог и минимум из presets/audio.json.
   Вырезаю из видео и звука одновременно, оставляя по keep_edge_sec с каждого края.
   Режу пересборкой отрезков (trim+concat одним проходом), не аудиофильтром,
   чтобы звук и картинка не разъехались.
   Пауз не найдено — резку пропускаю, но шаги 2 и 3 выполняю обязательно.
2. Шум. Управляется presets/audio.json → "denoise":
   "on"  — сначала arnndn (если рядом модель .rnnn), иначе afftdn. Мягко.
   "off" — шаг пропускается, в отчёт пишется denoise: "off".
3. Громкость. loudnorm в два прохода до target_lufs, пик не выше true_peak_db.

Настройки берутся из presets/audio.json. Файла нет — значения по умолчанию,
поведение как раньше. Резка пауз и громкость не меняются от наличия файла.

Только Python 3, ffmpeg и стандартная библиотека. Ничего не скачивает.
Исходник не трогает.

Отчёт цифрами печатается в stdout и, если найден state/pipeline.json с current
того же id, записывается в current.audio.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "audio.json")

# Значения по умолчанию — совпадают с прежним поведением.
# target_lufs и true_peak_db взяты из stages/05-qa/PROMPT.md (в RULES.md их нет).
DEFAULTS = {
    "denoise": "on",
    "pauses": "on",
    "silence_threshold_db": -35,
    "silence_min_sec": 0.45,
    "keep_edge_sec": 0.12,
    "target_lufs": -16,
    "true_peak_db": -1,
}
LOUDNORM_LRA = 11  # разброс громкости; поведение громкости не меняем


def die(msg, code):
    """Внятно сообщить и выйти с кодом (для loop.py: нет ffmpeg → blocked)."""
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"audio_clean: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
    """
    Прочитать presets/audio.json поверх умолчаний.
    Файла нет → умолчания. Файл сломан/нечитаем → умолчания, статус
    'corrupted, using defaults'. Никогда не падаем из-за конфига.
    """
    cfg = dict(DEFAULTS)
    status = "defaults (нет файла)"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if not isinstance(user, dict):
                raise ValueError("presets/audio.json — не объект JSON")
            for k in DEFAULTS:
                if k in user and user[k] is not None:
                    cfg[k] = user[k]
            status = "ok (presets/audio.json)"
        except Exception:            # любой сбой чтения/разбора — не падаем
            cfg = dict(DEFAULTS)
            status = "corrupted, using defaults"
    cfg["denoise_on"] = str(cfg["denoise"]).strip().lower() != "off"
    cfg["pauses_on"] = str(cfg["pauses"]).strip().lower() != "off"
    cfg["_config_status"] = status
    return cfg


def run(cmd):
    """Запустить ffmpeg/ffprobe, вернуть (returncode, stdout, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def ffprobe_duration(ffprobe, path):
    rc, out, err = run([
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ])
    if rc != 0 or not out.strip():
        raise RuntimeError(f"ffprobe не смог прочитать длительность {path}: {err.strip()}")
    return float(out.strip())


def detect_silences(ffmpeg, path, cfg):
    """Вернуть список пауз [(start, end), ...] по silencedetect."""
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
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None  # пауза до конца файла
        silences.append((s, e))
    return silences


def keep_segments(silences, duration, cfg):
    """
    Из пауз собрать отрезки, которые ОСТАВЛЯЕМ.
    Режем только внутреннюю часть паузы: [start+pad, end-pad].
    Возвращает (keep, pauses_cut).
    """
    pad = cfg["keep_edge_sec"]
    cuts = []
    for s, e in silences:
        end = duration if e is None else e
        cut_s = s + pad
        cut_e = end - pad
        if cut_e > cut_s:               # пауза длиннее 2*pad — есть что резать
            cuts.append((cut_s, cut_e))
    cuts.sort()

    keep = []
    cursor = 0.0
    for cut_s, cut_e in cuts:
        if cut_s > cursor:
            keep.append((cursor, cut_s))
        cursor = max(cursor, cut_e)
    if duration - cursor > 0.01:
        keep.append((cursor, duration))
    return keep, len(cuts)


def build_cut(ffmpeg, src, keep, work_dir):
    """
    Пересобрать видео из отрезков keep (trim+concat одним проходом).
    Видео и звук режутся по одинаковым интервалам — рассинхрона нет.
    """
    parts, labels = [], []
    for i, (s, e) in enumerate(keep):
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + \
        f"concat=n={len(keep)}:v=1:a=1[v][a]"

    graph_file = os.path.join(work_dir, "cut_filter.txt")
    with open(graph_file, "w", encoding="utf-8") as f:
        f.write(graph)

    out = os.path.join(work_dir, "cut_base.mp4")
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src,
        "-filter_complex_script", graph_file,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        out,
    ])
    if rc != 0:
        raise RuntimeError(f"резка пауз не удалась: {err.strip()[-400:]}")
    return out


def find_denoise_model():
    """Найти локальную модель arnndn (.rnnn). Ничего не скачиваем."""
    env = os.environ.get("AUDIO_DENOISE_MODEL")
    if env and os.path.exists(env):
        return env
    for d in ("presets", "models", "scripts"):
        found = sorted(glob.glob(os.path.join(ROOT, d, "*.rnnn")))
        if found:
            return found[0]
    return None


def denoise_filter(cfg):
    """
    Вернуть (строка_фильтра, имя) для шумоподавления.
    denoise off → ('', 'off'). on → arnndn при наличии модели, иначе afftdn. Мягко.
    """
    if not cfg["denoise_on"]:
        return "", "off"
    model = find_denoise_model()
    if model:
        # arnndn — по нейросетевой модели; путь экранируем для filtergraph
        safe = model.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
        return f"arnndn=m='{safe}'", "arnndn"
    # afftdn — мягко: умеренное подавление, без «металлического» призвука
    return "afftdn=nr=10:nf=-25", "afftdn"


def loudnorm_measure(ffmpeg, src, pre, cfg):
    """
    Первый проход loudnorm: измерить. pre — фильтры до loudnorm (шумодав или '').
    Возвращает dict с измеренными значениями.
    """
    chain = (pre + "," if pre else "") + (
        f"loudnorm=I={cfg['target_lufs']}:TP={cfg['true_peak_db']}:LRA={LOUDNORM_LRA}"
        ":print_format=json"
    )
    _, _, err = run([
        ffmpeg, "-hide_banner", "-i", src, "-af", chain, "-f", "null", "-",
    ])
    start = err.rfind("{")
    end = err.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"loudnorm не отдал измерения: {err.strip()[-400:]}")
    return json.loads(err[start:end + 1])


def loudnorm_apply(ffmpeg, src, pre, measured, out, cfg):
    """
    Второй проход loudnorm: применить измеренные значения (linear=true).
    Видео копируем как есть, меняем только звук.
    """
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
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"loudnorm (2-й проход) не удался: {err.strip()[-400:]}")


def measure_loudness(ffmpeg, path, cfg):
    """Измеренная интегральная громкость (LUFS) файла — для отчёта."""
    m = loudnorm_measure(ffmpeg, path, "", cfg)
    return round(float(m["input_i"]), 1)


def write_pipeline(out_path, audio):
    """Если рядом state/pipeline.json и current.id совпадает с [id] из пути — записать audio."""
    pipeline = os.path.join(ROOT, "state", "pipeline.json")
    if not os.path.exists(pipeline):
        return False
    # id = имя папки outputs/ready/<id>/файл
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

    out_clean = args.output
    out_nodenoise = os.path.join(
        os.path.dirname(out_clean), "clip_audio_nodenoise.mp4"
    )
    out_dir = os.path.dirname(os.path.abspath(out_clean)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    # --- измерения ДО ---
    duration_before = ffprobe_duration(ffprobe, args.input)
    loudness_before = measure_loudness(ffmpeg, args.input, cfg)

    # --- шаг 1: паузы (управляется presets/audio.json → "pauses") ---
    if not cfg["pauses_on"]:
        base = args.input          # резка выключена — шум и громкость всё равно делаю
        pauses_cut = 0
        pause_note = 'резка отключена (pauses: "off")'
    else:
        silences = detect_silences(ffmpeg, args.input, cfg)
        keep, pauses_cut = keep_segments(silences, duration_before, cfg)
        if pauses_cut > 0 and keep:
            base = build_cut(ffmpeg, args.input, keep, work_dir)
            pause_note = f"вырезано пауз: {pauses_cut}"
        else:
            base = args.input      # пауз нет — исходник не режу, шаги 2-3 всё равно делаю
            pause_note = "пауз не найдено, резка пропущена"

    # --- шаг 2 подготовка: шум (управляется presets/audio.json) ---
    den_filter, denoise_name = denoise_filter(cfg)

    # --- clean: (шум) + громкость (2 прохода) ---
    m_clean = loudnorm_measure(ffmpeg, base, den_filter, cfg)
    loudnorm_apply(ffmpeg, base, den_filter, m_clean, out_clean, cfg)

    # --- nodenoise: только громкость (2 прохода), для сравнения на слух ---
    m_nd = loudnorm_measure(ffmpeg, base, "", cfg)
    loudnorm_apply(ffmpeg, base, "", m_nd, out_nodenoise, cfg)

    # --- измерения ПОСЛЕ (по реальному файлу, не из головы) ---
    duration_after = ffprobe_duration(ffprobe, out_clean)
    loudness_after = measure_loudness(ffmpeg, out_clean, cfg)

    audio = {
        "duration_before": round(duration_before, 2),
        "duration_after": round(duration_after, 2),
        "pauses_cut": pauses_cut,
        "loudness_before": loudness_before,
        "loudness_after": loudness_after,
        "denoise": denoise_name,
    }

    wrote = write_pipeline(out_clean, audio)

    # --- отчёт цифрами ---
    same = "  (= clean, шумодав выключен)" if denoise_name == "off" else ""
    print("=== ОТЧЁТ audio_clean ===")
    print(f"Конфиг:        {cfg['_config_status']}")
    print(f"Длительность:  {audio['duration_before']} с → {audio['duration_after']} с")
    print(f"Паузы:         {pause_note}")
    print(f"Громкость:     {audio['loudness_before']} LUFS → {audio['loudness_after']} LUFS "
          f"(цель {cfg['target_lufs']}, пик ≤ {cfg['true_peak_db']} dB)")
    print(f"Шумоподавление: {denoise_name}")
    print(f"Файлы:         {out_clean}")
    print(f"               {out_nodenoise}{same}")
    print(f"pipeline.json: {'обновлён (current.audio)' if wrote else 'не трогал (нет current с этим id)'}")
    print("audio =", json.dumps(audio, ensure_ascii=False))
    print("=========================")


if __name__ == "__main__":
    main()
