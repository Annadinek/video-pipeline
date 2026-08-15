#!/usr/bin/env python3
"""
color.py — цвет и сглаживание лица (кожи).

Вход:  outputs/ready/[id]/clip_stab.mp4
Выход: outputs/ready/[id]/clip_color.mp4

Порядок:
1. Измеряем СОДЕРЖИМОЕ кадра через ffmpeg signalstats (не заголовок файла):
   средняя яркость и разброс тонов, насыщенность, пересветы и провалы в тенях.
2. Цвет. Есть LUT presets/cinema.cube — красим по нему (lut3d). Нет —
   базовая коррекция eq. В авто-режиме параметры eq ПОДБИРАЮТСЯ от измерений
   (тёмный кадр → поднять яркость; плоский → добавить контраст; тускло → насыщенность;
   при пересветах/провалах — не усиливаем клиппинг).
3. Сглаживание кожи — через OpenCV: Haar-каскад находит лицо, по YCrCb строим
   маску кожи ВНУТРИ лица, сглаживаем bilateralFilter только кожу (глаза, брови,
   волосы, фон не трогаем). Уровень off/low/medium/high.
4. Лёгкая резкость (unsharp) на финальном кодировании.

Разрешение и звук не меняются (звук копируется из промежуточного файла).

Настройки — presets/color.json (сломан/нет файла → умолчания, не падаем):
  lut               путь к LUT .cube; нет файла → цвет через eq
  auto              on/off — подбирать eq от измерений (иначе фиксированные значения)
  target_brightness целевая средняя яркость Y (0..255) для авто-режима
  target_saturation целевая насыщенность (0..255) для авто-режима
  brightness/contrast/saturation/gamma  — значения eq для ручного режима (auto=off)
  skin_smooth       off/low/medium/high — сила сглаживания кожи
  sharpen           резкость (unsharp luma_amount; 0 = не резчить)

OpenCV уже используется в проекте (scripts/vertical_cut.py) и ставится в workflow.
Только Python 3, ffmpeg, OpenCV и стандартная библиотека. Исходник не трогает.
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
    "auto": "on",
    "target_brightness": 120,
    "target_saturation": 120,
    "brightness": 0.0,
    "contrast": 1.03,
    "saturation": 1.06,
    "gamma": 1.0,
    "skin_smooth": "medium",
    "sharpen": 0.3,
}

# Сглаживание кожи (OpenCV bilateralFilter) от мягкого к сильному.
SKIN = {
    "low":    {"d": 5,  "sc": 40,  "ss": 40,  "strength": 0.5},
    "medium": {"d": 9,  "sc": 75,  "ss": 75,  "strength": 0.7},
    "high":   {"d": 13, "sc": 110, "ss": 110, "strength": 0.9},
}
# Диапазон кожи в YCrCb (Cr 133..173, Cb 77..127) — типичный для тонов кожи.
SKIN_LO = (0, 133, 77)
SKIN_HI = (255, 173, 127)


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


def ffprobe_dims_fps(ffprobe, path):
    _, out, _ = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", path,
    ])
    parts = out.strip().split(",")
    if len(parts) >= 3:
        w, h = int(parts[0]), int(parts[1])
        num, den = (parts[2].split("/") + ["1"])[:2]
        fps = float(num) / float(den) if float(den) else float(num)
        return w, h, round(fps, 3)
    return None, None, None


def measure(ffmpeg, path):
    """
    Содержимое кадра через signalstats (2 кадра/с). Возвращает dict:
    y_avg, y_spread (средний тональный размах YHIGH-YLOW), sat_avg,
    highlights_pct (доля кадров с почти белым YMAX>=254),
    shadows_pct (доля кадров с почти чёрным YMIN<=1).
    """
    _, out, err = run([
        ffmpeg, "-hide_banner", "-i", path,
        "-vf", "fps=2,signalstats,metadata=print:file=-", "-f", "null", "-",
    ])
    text = out + "\n" + err

    def col(tag):
        return [float(x) for x in re.findall(rf"lavfi\.signalstats\.{tag}=([\d.]+)", text)]

    yavg, ylow, yhigh = col("YAVG"), col("YLOW"), col("YHIGH")
    ymin, ymax, satavg = col("YMIN"), col("YMAX"), col("SATAVG")
    n = len(yavg)
    if n == 0:
        return None

    def mean(a):
        return sum(a) / len(a) if a else 0.0

    spread = mean([hi - lo for hi, lo in zip(yhigh, ylow)]) if yhigh and ylow else 0.0
    hi_pct = 100.0 * sum(1 for v in ymax if v >= 254) / len(ymax) if ymax else 0.0
    sh_pct = 100.0 * sum(1 for v in ymin if v <= 1) / len(ymin) if ymin else 0.0
    return {
        "y_avg": round(mean(yavg), 1),
        "y_spread": round(spread, 1),
        "sat_avg": round(mean(satavg), 1),
        "highlights_pct": round(hi_pct),
        "shadows_pct": round(sh_pct),
    }


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def derive_grade(stats, cfg):
    """Подобрать eq-параметры от измерений (auto=on). Возвращает (params, заметки)."""
    manual = {"brightness": float(cfg["brightness"]), "contrast": float(cfg["contrast"]),
              "saturation": float(cfg["saturation"]), "gamma": float(cfg["gamma"])}
    if str(cfg["auto"]).strip().lower() == "off" or stats is None:
        return manual, ["ручной режим (auto=off)"]

    y, spread, sat = stats["y_avg"], stats["y_spread"], stats["sat_avg"]
    hi, sh = stats["highlights_pct"], stats["shadows_pct"]
    tb, tsat = float(cfg["target_brightness"]), float(cfg["target_saturation"])
    notes = []

    b = clamp((tb - y) / 255.0, -0.12, 0.12)
    if hi > 20 and b > 0:
        b = min(b, 0.03); notes.append("пересветы → яркость почти не поднимаю")
    if sh > 20 and b < 0:
        b = max(b, -0.03); notes.append("провалы → яркость почти не опускаю")

    c = clamp(1.0 + (150 - spread) / 600.0, 0.95, 1.15)
    if hi > 20:
        c = min(c, 1.03); notes.append("пересветы → контраст не задираю")

    # Потолок насыщенности поднят 1.3 → 1.6 по просьбе Анны: её клип бледнее образца
    # (SATAVG 11.8 против 18.7 ≈ ×1.6), при потолке 1.3 добавить столько было нельзя.
    s = clamp(1.0 + (tsat - sat) / 300.0, 0.9, 1.6)

    g = 1.0
    if sh > 20:
        g = 1.08; notes.append("провалы в тенях → поднимаю гамму (осветляю тени)")
    elif hi > 20:
        g = 0.96; notes.append("пересветы → опускаю гамму")

    if not notes:
        notes.append("картинка сбалансирована, правки мягкие")
    return {"brightness": round(b, 3), "contrast": round(c, 3),
            "saturation": round(s, 3), "gamma": round(g, 3)}, notes


def grade_color(ffmpeg, src, cfg, stats, out):
    """Шаг цвета: LUT или eq. Возвращает описание того, что применили."""
    lut = str(cfg["lut"]).strip()
    lut_path = lut if os.path.isabs(lut) else os.path.join(ROOT, lut)
    if lut and os.path.exists(lut_path):
        safe = lut_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        vf = f"lut3d='{safe}'"
        desc = f"LUT ({lut})"
        params = None
    else:
        params, notes = derive_grade(stats, cfg)
        vf = (f"eq=contrast={params['contrast']}:brightness={params['brightness']}"
              f":saturation={params['saturation']}:gamma={params['gamma']}")
        desc = "eq " + json.dumps(params, ensure_ascii=False) + " [" + "; ".join(notes) + "]"
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-i", src, "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        die(f"цвет (шаг eq/LUT) не удался: {err.strip()[-300:]}", 1)
    return desc, params


def skin_level(cfg):
    if str(cfg["skin_smooth"]).strip().lower() == "off":
        return "off"
    lv = str(cfg["skin_smooth"]).strip().lower()
    return lv if lv in SKIN else "medium"


def smooth_faces(ffmpeg, graded, out, level, sharpen, w, h, fps):
    """
    OpenCV: сглаживаем ТОЛЬКО кожу лица, кадры отдаём в ffmpeg (звук из graded).
    Возвращает долю кадров, где нашли лицо (%).
    """
    import cv2

    if not hasattr(cv2, "CascadeClassifier"):
        die("OpenCV без CascadeClassifier — нужен opencv-python-headless<5 → blocked", 3)

    params = SKIN[level]
    cascade = cv2.CascadeClassifier(
        os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    min_face = max(60, int(h * 0.12))

    cap = cv2.VideoCapture(graded)
    enc_cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        "-i", graded,
    ]
    if sharpen > 0:
        enc_cmd += ["-filter_complex",
                    f"[0:v]unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharpen}[v]",
                    "-map", "[v]", "-map", "1:a:0?"]
    else:
        enc_cmd += ["-map", "0:v", "-map", "1:a:0?"]
    enc_cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "copy", "-movflags", "+faststart", out]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    if enc.stdin is None:
        die("не удалось открыть поток в ffmpeg для сборки видео", 1)

    frames = faces_frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(min_face, min_face))
            if len(faces):
                faces_frames += 1
                for (x, y, fw, fh) in faces:
                    x0, y0 = max(0, x), max(0, y)
                    x1, y1 = min(w, x + fw), min(h, y + fh)
                    roi = frame[y0:y1, x0:x1]
                    if roi.size == 0:
                        continue
                    ycrcb = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
                    mask = cv2.inRange(ycrcb, SKIN_LO, SKIN_HI)
                    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=5)
                    sm = cv2.bilateralFilter(roi, params["d"], params["sc"], params["ss"])
                    a = (mask.astype("float32") / 255.0) * params["strength"]
                    a = a[..., None]
                    roi[:] = (sm.astype("float32") * a + roi.astype("float32") * (1 - a)).astype("uint8")
            enc.stdin.write(frame.tobytes())
    finally:
        cap.release()
        if enc.stdin:
            enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        die("сборка видео после сглаживания не удалась", 1)
    return round(100.0 * faces_frames / frames, 1) if frames else 0.0


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


def stats_line(s):
    if s is None:
        return "н/д (signalstats не отдал данные)"
    return (f"яркость Y={s['y_avg']}, разброс тонов={s['y_spread']}, "
            f"насыщенность={s['sat_avg']}, пересветы={s['highlights_pct']}%, "
            f"провалы={s['shadows_pct']}%")


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
    try:
        import cv2  # noqa: F401
    except Exception:
        die("нет OpenCV (opencv-python-headless) → blocked", 3)

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    w, h, fps = ffprobe_dims_fps(ffprobe, args.input)

    # 1. измерение ДО
    before = measure(ffmpeg, args.input)

    # 2. цвет (eq подбирается от измерений или LUT)
    graded = os.path.join(work_dir, "graded.mp4")
    color_desc, _ = grade_color(ffmpeg, args.input, cfg, before, graded)

    # 3. сглаживание кожи (OpenCV) + 4. резкость, звук из graded
    level = skin_level(cfg)
    sharpen = float(cfg["sharpen"])
    if level == "off":
        # только резкость (или простое копирование), лицо не трогаем
        if sharpen > 0:
            rc, _, err = run([
                ffmpeg, "-y", "-hide_banner", "-i", graded,
                "-vf", f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharpen}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "copy", "-movflags", "+faststart", args.output,
            ])
            if rc != 0:
                die(f"резкость не удалась: {err.strip()[-300:]}", 1)
        else:
            shutil.copyfile(graded, args.output)
        faces_pct = None
    else:
        faces_pct = smooth_faces(ffmpeg, graded, args.output, level, sharpen, w, h, fps)

    # 5. измерение ПОСЛЕ
    w1, h1, _ = ffprobe_dims_fps(ffprobe, args.output)
    after = measure(ffmpeg, args.output)

    color = {
        "resolution_before": f"{w}x{h}",
        "resolution_after": f"{w1}x{h1}",
        "before": before,
        "after": after,
        "color": color_desc,
        "skin_smooth": level,
        "faces_pct": faces_pct,
        "sharpen": sharpen,
    }
    wrote = write_pipeline(args.output, color)

    print("=== ОТЧЁТ color ===")
    print(f"Конфиг:        {cfg['_config_status']}")
    print(f"Разрешение:    {w}x{h} → {w1}x{h1}" +
          ("  (не изменилось)" if (w, h) == (w1, h1) else "  ВНИМАНИЕ: изменилось!"))
    print(f"Кадр ДО:       {stats_line(before)}")
    print(f"Кадр ПОСЛЕ:    {stats_line(after)}")
    print(f"Цвет:          {color_desc}")
    if level == "off":
        print("Кожа:          сглаживание выкл")
    else:
        p = SKIN[level]
        faces_txt = f"{faces_pct}% кадров" if faces_pct is not None else "н/д"
        print(f"Кожа:          OpenCV, уровень {level} (d={p['d']}, sc={p['sc']}, ss={p['ss']}, "
              f"сила={p['strength']}); лицо найдено в {faces_txt}")
    print(f"Резкость:      unsharp luma_amount={sharpen}" if sharpen > 0 else "Резкость:      выкл")
    print(f"Файл:          {args.output}")
    print(f"pipeline.json: {'обновлён (current.color)' if wrote else 'не трогал (нет current с этим id)'}")
    print("color =", json.dumps(color, ensure_ascii=False))
    print("===================")


if __name__ == "__main__":
    main()
