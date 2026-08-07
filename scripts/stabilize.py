#!/usr/bin/env python3
"""
stabilize.py — стабилизация видео (устранение тряски камеры).

Вход:  outputs/ready/[id]/clip_audio_clean.mp4
Выход: outputs/ready/[id]/clip_stab.mp4

Как работает:
- Два прохода vidstab: vidstabdetect (замер) → vidstabtransform (стабилизация).
- Сначала ИЗМЕРЯЕМ тряску и печатаем цифрой. Мера тряски — среднее покадровое
  смещение камеры в пикселях (из ascii-.trf от vidstabdetect: медиана локальных
  сдвигов на кадр). Больше пикселей = сильнее мотает.
- Если камера почти не двигалась (тряска ниже порога skip_below_px) —
  этап ПРОПУСКАЕМ: копируем файл как есть, БЕЗ пересборки, чтобы не портить картинку.
- Иначе стабилизируем и снова измеряем — печатаем тряску до/после (px и % высоты)
  и сколько процентов кадра съедено обрезкой (Final zoom реального прохода).

Стабилизация обрезает края (зумит внутрь) — «съедено обрезкой» показывает, сколько.
Съёмка 1080p/30 — обрезка уменьшает полезную площадь кадра.

Настройки — presets/stab.json (сломан/нет файла → умолчания, не падаем):
  shakiness   чувствительность детектора (1..10)
  smoothing   сила сглаживания траектории (кадры)
  zoom        принудительный зум, % (0 = не добавлять)
  optzoom     1 = авто-зум, чтобы скрыть чёрные края; 0 = выключить
  skip_below_px  порог пропуска в пикселях: тряска ниже — этап пропускаем

Звук из входного файла сохраняется как есть (-c:a copy).
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
CONFIG_PATH = os.path.join(ROOT, "presets", "stab.json")

DEFAULTS = {
    "shakiness": 5,
    "smoothing": 10,
    "zoom": 0,
    "optzoom": 1,
    "skip_below_px": 3.0,
}


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"stabilize: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
    """presets/stab.json поверх умолчаний. Сломан/нет файла → умолчания, не падаем."""
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
            status = "ok (presets/stab.json)"
        except Exception:
            cfg = dict(DEFAULTS)
            status = "corrupted, using defaults"
    cfg["_config_status"] = status
    return cfg


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def ffprobe_dims(ffprobe, path):
    _, out, _ = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "csv=p=0", path,
    ])
    parts = out.strip().split(",")
    if len(parts) >= 3:
        w, h, rate = int(parts[0]), int(parts[1]), parts[2]
        return w, h, rate
    return None, None, None


def vidstab_detect(ffmpeg, src, trf, shakiness):
    """
    Проход 1: замер движения, пишет .trf.
    Пробуем текстовый формат (fileformat=ascii) — он нужен для разбора тряски.
    Если сборка ffmpeg не знает эту опцию — откатываемся на формат по умолчанию
    (у старых сборок он и так текстовый; у новых — бинарный, тогда тряску не измерим).
    """
    safe = trf.replace("\\", "/")
    base = f"vidstabdetect=shakiness={shakiness}:result={safe}"
    last_err = ""
    for vf in (base.replace("result=", "fileformat=ascii:result="), base):
        rc, _, err = run([
            ffmpeg, "-y", "-hide_banner", "-i", src, "-vf", vf, "-f", "null", "-",
        ])
        if rc == 0 and os.path.exists(trf):
            return
        last_err = err
    raise RuntimeError(f"vidstabdetect не удался: {last_err.strip()[-300:]}")


def parse_trf_motion(trf_path):
    """
    Реальная тряска из ascii-.trf: на каждый кадр берём медиану локальных смещений
    (dx, dy) — устойчиво к выбросам — и её магнитуду в пикселях. Возвращаем среднее
    по всем кадрам (сколько в среднем дёргается камера от кадра к кадру, px).
    """
    import statistics
    mags = []
    with open(trf_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("Frame"):
                continue
            lms = re.findall(r"\(LM\s+(-?\d+)\s+(-?\d+)", line)
            if not lms:
                continue
            dxs = [int(a) for a, _ in lms]
            dys = [int(b) for _, b in lms]
            mdx, mdy = statistics.median(dxs), statistics.median(dys)
            mags.append((mdx * mdx + mdy * mdy) ** 0.5)
    if not mags:
        return None   # .trf не в текстовом формате (бинарный) — тряску не измерить
    return sum(mags) / len(mags)


def final_zoom(ffmpeg, src, trf, smoothing, zoom, optzoom, out=None):
    """
    Проход 2: vidstabtransform. Если out=None — считаем в null (только измеряем).
    Возвращает применённый зум в % (Final zoom), либо значение zoom при optzoom=0.
    Звук копируем, если пишем реальный файл.
    """
    safe = trf.replace("\\", "/")
    vf = (f"vidstabtransform=input={safe}:smoothing={smoothing}"
          f":zoom={zoom}:optzoom={optzoom}")
    if out is None:
        cmd = [ffmpeg, "-v", "verbose", "-hide_banner", "-i", src, "-vf", vf, "-f", "null", "-"]
    else:
        cmd = [ffmpeg, "-v", "verbose", "-y", "-hide_banner", "-i", src, "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
               "-c:a", "copy", "-movflags", "+faststart", out]
    rc, _, err = run(cmd)
    if rc != 0:
        raise RuntimeError(f"vidstabtransform не удался: {err.strip()[-300:]}")
    m = re.search(r"Final zoom:\s*([-\d.]+)", err)
    if m:
        return round(float(m.group(1)), 2)
    # Final zoom печатается только когда зум ненулевой; иначе применён ровно zoom
    return round(float(zoom), 2)


def measure_shake(ffmpeg, src, work_dir, cfg, tag):
    """
    Мера тряски в пикселях: среднее покадровое смещение камеры (из ascii-.trf).
    Возвращает (пиксели, путь_к_trf) — .trf можно переиспользовать для трансформа.
    """
    trf = os.path.join(work_dir, f"detect_{tag}.trf")
    vidstab_detect(ffmpeg, src, trf, cfg["shakiness"])
    px = parse_trf_motion(trf)
    return (round(px, 1) if px is not None else None), trf


def write_pipeline(out_path, stab):
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
    cur["stab"] = stab
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip_audio_clean.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_stab.mp4")
    args = ap.parse_args()

    cfg = load_config()
    # Можно указать отдельный ffmpeg/ffprobe через STAB_FFMPEG/STAB_FFPROBE
    # (нужно, когда системный ffmpeg не знает vidstab fileformat=ascii, а качать
    # видео он всё равно должен — тогда для стабилизации берём статический билд).
    def resolve(name, env):
        p = os.environ.get(env)
        return p if (p and os.path.exists(p)) else shutil.which(name)
    ffmpeg = resolve("ffmpeg", "STAB_FFMPEG")
    ffprobe = resolve("ffprobe", "STAB_FFPROBE")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)
    if not os.path.exists(args.input):
        die(f"нет входного файла: {args.input}", 2)

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    w, h, rate = ffprobe_dims(ffprobe, args.input)

    def fmt(px):
        """px + % высоты, либо 'н/д', если измерить не удалось."""
        if px is None:
            return "н/д"
        pct = round(px / h * 100, 2) if h else 0.0
        return f"{px} px ({pct}% высоты)"

    # --- шаг 1: измеряем тряску (px; None = сборка ffmpeg не отдала текстовый .trf) ---
    shake_before, trf_in = measure_shake(ffmpeg, args.input, work_dir, cfg, "before")

    threshold = float(cfg["skip_below_px"])
    if shake_before is not None and shake_before < threshold:
        # камера почти не двигалась — не портим картинку лишней пересборкой
        shutil.copyfile(args.input, args.output)
        stab = {
            "shake_before": shake_before,
            "shake_after": shake_before,
            "crop_percent": 0.0,
            "applied": False,
            "note": "тряски нет",
        }
        write_pipeline(args.output, stab)
        print("=== ОТЧЁТ stabilize ===")
        print(f"Конфиг:       {cfg['_config_status']}")
        print(f"Кадр:         {w}x{h} @ {rate}")
        print(f"Тряска:       {fmt(shake_before)} < порог {threshold} px → ТРЯСКИ НЕТ")
        print("Этап пропущен: файл скопирован без пересборки, картинка не тронута.")
        print("Обрезка:      0% (стабилизация не применялась)")
        print(f"Файл:         {args.output}")
        print("stab =", json.dumps(stab, ensure_ascii=False))
        print("=======================")
        return

    # --- шаг 2: стабилизация с настройками из конфига (переиспользуем trf_in) ---
    crop_percent = final_zoom(
        ffmpeg, args.input, trf_in, cfg["smoothing"],
        zoom=cfg["zoom"], optzoom=cfg["optzoom"], out=args.output,
    )

    # --- шаг 3: измеряем остаточную тряску ---
    shake_after, _ = measure_shake(ffmpeg, args.output, work_dir, cfg, "after")

    stab = {
        "shake_before": shake_before,
        "shake_after": shake_after,
        "crop_percent": crop_percent,
        "applied": True,
        "note": "стабилизировано" if shake_before is not None else "стабилизировано (тряска не измерена)",
    }
    write_pipeline(args.output, stab)

    print("=== ОТЧЁТ stabilize ===")
    print(f"Конфиг:       {cfg['_config_status']}")
    print(f"Кадр:         {w}x{h} @ {rate}")
    print(f"Тряска:       {fmt(shake_before)} → {fmt(shake_after)}  (меньше = стабильнее)")
    print(f"Обрезка:      {crop_percent}% кадра съедено (зум внутрь по каждой стороне)")
    print(f"Настройки:    shakiness={cfg['shakiness']}, smoothing={cfg['smoothing']}, "
          f"zoom={cfg['zoom']}, optzoom={cfg['optzoom']}")
    print(f"Файл:         {args.output}")
    print("stab =", json.dumps(stab, ensure_ascii=False))
    print("=======================")


if __name__ == "__main__":
    main()
