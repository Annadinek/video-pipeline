#!/usr/bin/env python3
"""
color.py — цвет и сглаживание лица (кожи).

Вход:  outputs/ready/[id]/clip_stab.mp4
Выход: outputs/ready/[id]/clip_color.mp4

Что делает (порядок фильтров):
1. Цвет. Если есть LUT-пресет (presets/cinema.cube) — красим по нему (lut3d).
   Пресета нет — базовая коррекция eq (контраст/яркость/насыщенность/гамма из конфига).
2. Сглаживание кожи. bilateral — сглаживает по яркости, СОХРАНЯЯ края (глаза, волосы,
   контур лица не мылит). Уровень off/low/medium/high.
3. Резкость. Лёгкий unsharp после сглаживания, чтобы кожа мягкая, а детали чёткие.

Разрешение и звук не меняются (-c:a copy). Съёмка 1080p — резкость и цвет считаем
на полном разрешении, поэтому важно, что fetch тянет 1080p.

Настройки — presets/color.json (сломан/нет файла → умолчания, не падаем):
  lut          путь к LUT .cube; если файла нет — цвет через eq
  brightness   eq: яркость (-1..1), только когда нет LUT
  contrast     eq: контраст
  saturation   eq: насыщенность
  gamma        eq: гамма
  skin_smooth  off/low/medium/high — сила сглаживания кожи (bilateral)
  sharpen      сила резкости после сглаживания (unsharp luma_amount; 0 = не резчить)

Отчёт цифрами: разрешение до/после (должно совпадать), яркость и насыщенность
до/после, какой способ цвета, уровень сглаживания, резкость.

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
CONFIG_PATH = os.path.join(ROOT, "presets", "color.json")

DEFAULTS = {
    "lut": "presets/cinema.cube",
    "brightness": 0.0,
    "contrast": 1.03,
    "saturation": 1.06,
    "gamma": 1.0,
    "skin_smooth": "medium",
    "sharpen": 0.5,
}

# Сглаживание кожи от мягкого к сильному. bilateral хранит края.
SKIN = {
    "low": "bilateral=sigmaS=6:sigmaR=0.06",
    "medium": "bilateral=sigmaS=10:sigmaR=0.1",
    "high": "bilateral=sigmaS=16:sigmaR=0.16",
}


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"color: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
    """presets/color.json поверх умолчаний. Сломан/нет файла → умолчания, не падаем."""
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
            status = "ok (presets/color.json)"
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
        "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
    ])
    parts = out.strip().split(",")
    if len(parts) >= 2:
        return int(parts[0]), int(parts[1])
    return None, None


def measure_color(ffmpeg, path):
    """
    Средняя яркость (Y, 0..255) и насыщенность (0..255) по клипу.
    Считаем через signalstats с прореживанием до 2 кадров/с.
    """
    _, out, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-vf", "fps=2,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ])
    text = out + "\n" + err
    ys = [float(m) for m in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)]
    sats = [float(m) for m in re.findall(r"lavfi\.signalstats\.SATAVG=([\d.]+)", text)]
    y = round(sum(ys) / len(ys), 1) if ys else None
    sat = round(sum(sats) / len(sats), 1) if sats else None
    return y, sat


def skin_level(cfg):
    if str(cfg["skin_smooth"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["skin_smooth"]).strip().lower()
    return lv if lv in SKIN else "medium"


def build_vf(cfg):
    """Собрать цепочку фильтров и вернуть (vf, описание_цвета, уровень_кожи, резкость)."""
    parts = []

    lut = str(cfg["lut"]).strip()
    lut_path = lut if os.path.isabs(lut) else os.path.join(ROOT, lut)
    if lut and os.path.exists(lut_path):
        safe = lut_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        parts.append(f"lut3d='{safe}'")
        color_desc = f"LUT ({lut})"
    else:
        parts.append(
            f"eq=contrast={cfg['contrast']}:brightness={cfg['brightness']}"
            f":saturation={cfg['saturation']}:gamma={cfg['gamma']}"
        )
        color_desc = "eq (LUT-пресета нет)"

    level = skin_level(cfg)
    if level != "off":
        parts.append(SKIN[level])

    sharpen = float(cfg["sharpen"])
    if sharpen > 0:
        parts.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharpen}")

    return ",".join(parts), color_desc, level, sharpen


def write_pipeline(out_path, color):
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
    cur["color"] = color
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip_stab.mp4")
    ap.add_argument("--output", required=True, help="outputs/ready/[id]/clip_color.mp4")
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

    w0, h0 = ffprobe_dims(ffprobe, args.input)
    y0, sat0 = measure_color(ffmpeg, args.input)

    vf, color_desc, level, sharpen = build_vf(cfg)
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", args.input, "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy", "-movflags", "+faststart", args.output,
    ])
    if rc != 0:
        die(f"цветокоррекция не удалась: {err.strip()[-300:]}", 1)

    w1, h1 = ffprobe_dims(ffprobe, args.output)
    y1, sat1 = measure_color(ffmpeg, args.output)

    def fmt(v):
        return "н/д" if v is None else v

    color = {
        "resolution_before": f"{w0}x{h0}",
        "resolution_after": f"{w1}x{h1}",
        "brightness_before": y0, "brightness_after": y1,
        "saturation_before": sat0, "saturation_after": sat1,
        "color": color_desc,
        "skin_smooth": level,
        "sharpen": sharpen,
    }
    wrote = write_pipeline(args.output, color)

    print("=== ОТЧЁТ color ===")
    print(f"Конфиг:        {cfg['_config_status']}")
    print(f"Разрешение:    {w0}x{h0} → {w1}x{h1}" +
          ("  (не изменилось)" if (w0, h0) == (w1, h1) else "  ВНИМАНИЕ: изменилось!"))
    print(f"Цвет:          {color_desc}")
    print(f"Яркость (Y):   {fmt(y0)} → {fmt(y1)}  (0..255)")
    print(f"Насыщенность:  {fmt(sat0)} → {fmt(sat1)}  (0..255)")
    print(f"Кожа:          сглаживание {level}" + (f"  ({SKIN[level]})" if level != 'off' else ""))
    print(f"Резкость:      unsharp luma_amount={sharpen}" if sharpen > 0 else "Резкость:      выкл")
    print(f"Файл:          {args.output}")
    print(f"pipeline.json: {'обновлён (current.color)' if wrote else 'не трогал (нет current с этим id)'}")
    print("color =", json.dumps(color, ensure_ascii=False))
    print("===================")


if __name__ == "__main__":
    main()
