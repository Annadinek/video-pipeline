#!/usr/bin/env python3
"""
vertical_cut.py — из горизонтального 16:9 делает вертикаль 9:16 со слежением за лицом.

Порядок:
1. OpenCV находит лицо на кадрах через каждые 0.5 секунды.
2. Считает центр лица.
3. Сглаживает траекторию, чтобы кадр не дёргался.
4. ffmpeg режет окно 9:16 по этой траектории.

Лицо не найдено на кадре — берём центр кадра, пишем в logs/errors.log, конвейер не останавливаем.

Библиотека OpenCV ставится в workflow шагом:
    pip install opencv-python-headless
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

LOG_DIR = "logs"
ERRORS_LOG = os.path.join(LOG_DIR, "errors.log")


def log_error(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(ERRORS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] vertical_cut: {msg}\n")


def ffprobe_dimensions(path):
    """Вернуть (ширина, высота) видео через ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def ffprobe_duration(path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def detect_face_centers(path, width, height, step=0.5):
    """
    Каждые step секунд ловим лицо, возвращаем список (время, x_центр).
    Лицо не найдено — x_центр = середина кадра, пишем в лог.
    """
    import cv2  # ставится в workflow: pip install opencv-python-headless

    cascade_path = os.path.join(
        cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
    )
    face_cascade = cv2.CascadeClassifier(cascade_path)

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = ffprobe_duration(path)

    centers = []
    t = 0.0
    while t < duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 5)
        if len(faces) > 0:
            # берём самое крупное лицо
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            cx = fx + fw / 2.0
        else:
            cx = width / 2.0
            log_error(f"лицо не найдено на {t:.1f}с, беру центр кадра")
        centers.append((t, cx))
        t += step

    cap.release()
    if not centers:
        centers = [(0.0, width / 2.0)]
    return centers


def smooth(values, window=5):
    """Скользящее среднее, чтобы окно кадра не дёргалось."""
    if len(values) <= 1:
        return values
    out = []
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def build_crop_expr(centers, width, height):
    """
    Ширина окна 9:16 при высоте height: crop_w = height * 9 / 16.
    Возвращаем x одним усреднённым значением (плавно, без рывков внутри клипа).
    Если нужно строгое покадровое движение — расширить до sendcmd/zoompan.
    """
    crop_w = int(round(height * 9 / 16))
    crop_w = min(crop_w, width)

    xs = smooth([c[1] for c in centers])
    avg_cx = sum(xs) / len(xs)
    x = int(round(avg_cx - crop_w / 2.0))
    x = max(0, min(x, width - crop_w))
    return crop_w, height, x


def center_crop(input_path, output_path, width, height):
    """Резерв: режем по центру, когда слежение выключено или лицо нигде не найдено."""
    crop_w = int(round(height * 9 / 16))
    crop_w = min(crop_w, width)
    x = (width - crop_w) // 2
    vf = f"crop={crop_w}:{height}:{x}:0,scale=1080:1920"
    run_ffmpeg(input_path, output_path, vf)


def run_ffmpeg(input_path, output_path, vf):
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ],
        check=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--face-tracking", default="true")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        log_error(f"нет входного файла: {args.input}")
        print(f"Нет входного файла: {args.input}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    width, height = ffprobe_dimensions(args.input)

    tracking = str(args.face_tracking).lower() in ("true", "1", "yes", "on")

    if not tracking:
        center_crop(args.input, args.output, width, height)
        print(f"Готово (по центру, без слежения): {args.output}")
        return

    try:
        centers = detect_face_centers(args.input, width, height)
        crop_w, crop_h, x = build_crop_expr(centers, width, height)
        vf = f"crop={crop_w}:{crop_h}:{x}:0,scale=1080:1920"
        run_ffmpeg(args.input, args.output, vf)
        print(f"Готово (слежение за лицом): {args.output}")
    except Exception as e:  # noqa: BLE001
        log_error(f"слежение сорвалось ({e}), режу по центру")
        center_crop(args.input, args.output, width, height)
        print(f"Готово (запасной вариант, по центру): {args.output}")


if __name__ == "__main__":
    main()
