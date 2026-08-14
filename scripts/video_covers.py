#!/usr/bin/env python3
# video_covers.py — делает обложки (1280x720) из кадров ПРИСЛАННОГО видео.
# Текст берётся из названия видео (слова Анны), не выдумывается.
# Кадры вынимаются из самого видео, лицо ищется автоматически (OpenCV),
# кадр приближается по лицу — как в make_thumbs, но координаты лица не заданы вручную.
#
# Результат (кадры-кандидаты и готовые обложки) КОММИТИТСЯ в assets/<id>/,
# чтобы я посмотрел глазами перед отправкой Анне. Сам ничего в бот не шлёт.

import glob
import json
import os
import subprocess
import sys

import cv2
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else "NtX08PXq7i8"
WORK = "work"
FRAMES = os.path.join(WORK, "frames")
OUT = os.path.join("assets", VIDEO_ID)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1280, 720
YELLOW = (255, 205, 0)
DARK = (20, 20, 20)

# Тексты обложек — из названия видео «Зачем мы живём? … В чём смысл жизни?
# Про опыт — то что до ума!». Слова Анны, ничего не придумано.
COVER_TEXTS = [
    [("ЗАЧЕМ МЫ", "white"), ("ЖИВЁМ?", "yellow")],
    [("В ЧЁМ СМЫСЛ", "white"), ("ЖИЗНИ?", "yellow")],
    [("ОПЫТ —", "white"), ("ТО, ЧТО ДО УМА", "yellow")],
]

FACE_OUT = 0.42     # какой долей высоты обложки хотим лицо (крупно)
FACE_AT_Y = 0.34    # где по вертикали центр лица в обложке


def download():
    os.makedirs(WORK, exist_ok=True)
    raw = os.path.join(WORK, "raw.mp4")
    if os.path.exists(raw):
        return raw
    base = ["yt-dlp", "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=web_safari,web,mweb,tv",
            "--retries", "5", "--fragment-retries", "5"]
    if os.path.exists(os.path.join(WORK, "cookies.txt")):
        base += ["--cookies", os.path.join(WORK, "cookies.txt")]
    cmd = base + ["-S", "res,ext:mp4:m4a", "-f", "bv*+ba/b",
                  "--merge-output-format", "mp4",
                  "-o", raw, f"https://www.youtube.com/watch?v={VIDEO_ID}"]
    for a in range(1, 4):
        if subprocess.run(cmd).returncode == 0 and os.path.exists(raw):
            return raw
        print(f"скачивание: попытка {a} не удалась")
    raise SystemExit("не удалось скачать видео")


def extract_frames(raw):
    os.makedirs(FRAMES, exist_ok=True)
    # кадр раз в 2 секунды
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-vf", "fps=1/2",
                    "-q:v", "2", os.path.join(FRAMES, "f_%04d.jpg"),
                    "-loglevel", "error"], check=True)
    return sorted(glob.glob(os.path.join(FRAMES, "f_*.jpg")))


def score_frame(path, cascade):
    """Оценка кадра: крупное чёткое фронтальное лицо ближе к центру = выше."""
    img = cv2.imread(path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.1, 6, minSize=(120, 120))
    if len(faces) == 0:
        return None
    h, w = gray.shape
    # берём самое крупное лицо
    x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
    face_frac = fh / h                       # доля высоты кадра
    cx = (x + fw / 2) / w
    center_pen = abs(cx - 0.5)               # штраф за смещение от центра
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()  # резкость
    score = face_frac * 100 - center_pen * 30 + min(sharp, 500) / 500 * 10
    return {
        "path": path, "score": score, "face_frac": face_frac,
        "fx": cx, "fy": (y + fh / 2) / h,
    }


def load_bg(path, fx, fy, face_frac):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = FACE_OUT / max(face_frac, 0.08)
    crop_h = int(round(H / scale))
    crop_w = int(round(crop_h * 16 / 9))
    cx, cy = fx * w, fy * h
    if crop_w > w:
        crop_w = w
        crop_h = int(round(crop_w * 9 / 16))
    if crop_h > h:
        crop_h = h
        crop_w = int(round(crop_h * 16 / 9))
        crop_w = min(crop_w, w)
    x0 = int(round(cx - 0.50 * crop_w))
    y0 = int(round(cy - FACE_AT_Y * crop_h))
    x0 = max(0, min(x0, w - crop_w))
    y0 = max(0, min(y0, h - crop_h))
    im = im.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize((W, H))
    im = ImageEnhance.Brightness(im).enhance(1.12)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    im = ImageEnhance.Color(im).enhance(1.10)
    grad = Image.new("L", (1, H), 0)
    for yy in range(H):
        t = max(0.0, (yy - H * 0.55) / (H * 0.45))
        grad.putpixel((0, yy), int(190 * t))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, grad.resize((W, H)))
    return im


def fit_font(text, max_w, start=118):
    size = start
    while size > 46:
        f = ImageFont.truetype(FONT_PATH, size)
        bb = f.getbbox(text, stroke_width=6)
        if bb[2] - bb[0] <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(FONT_PATH, 46)


def draw_cover(frame, lines, face_frac, fx, fy, out):
    im = load_bg(frame, fx, fy, face_frac)
    d = ImageDraw.Draw(im)
    margin = 64
    max_w = W - margin * 2
    font = min((fit_font(t, max_w) for t, _ in lines), key=lambda f: f.size)
    heights = [d.textbbox((0, 0), t, font=font, stroke_width=6)[3] for t, _ in lines]
    gap = 12
    total = sum(heights) + gap * (len(lines) - 1)
    y = H - 64 - total
    x = margin
    for (t, st), hh in zip(lines, heights):
        bb = d.textbbox((x, y), t, font=font, stroke_width=6)
        if st == "yellow":
            px, py = 22, 12
            d.rounded_rectangle([bb[0] - px, bb[1] - py, bb[2] + px, bb[3] + py],
                                radius=14, fill=YELLOW)
            d.text((x, y), t, font=font, fill=DARK)
        else:
            d.text((x + 4, y + 4), t, font=font, fill=(0, 0, 0))
            d.text((x, y), t, font=font, fill=(255, 255, 255),
                   stroke_width=6, stroke_fill=(0, 0, 0))
        y += hh + gap
    im.save(out, quality=90)
    print("обложка:", out)


def main():
    os.makedirs(OUT, exist_ok=True)
    raw = download()
    frames = extract_frames(raw)
    print(f"кадров: {len(frames)}")
    cascade_path = os.path.join(
        getattr(cv2, "data").haarcascades, "haarcascade_frontalface_default.xml"
    ) if hasattr(cv2, "data") else "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    scored = [s for s in (score_frame(f, cascade) for f in frames) if s]
    scored.sort(key=lambda s: s["score"], reverse=True)
    # берём топ-6 кандидатов, но не два подряд соседних кадра
    picks = []
    for s in scored:
        if all(abs(frames.index(s["path"]) - frames.index(p["path"])) > 2 for p in picks):
            picks.append(s)
        if len(picks) >= 6:
            break
    # сохраняем кандидатов (чтобы посмотреть глазами)
    meta = []
    for i, s in enumerate(picks, 1):
        Image.open(s["path"]).convert("RGB").save(os.path.join(OUT, f"cand_{i}.jpg"), quality=88)
        meta.append({"n": i, **{k: s[k] for k in ("score", "face_frac", "fx", "fy")}})
    with open(os.path.join(OUT, "cands.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # делаем 3 обложки из топ-3 кадров
    for i, (lines, s) in enumerate(zip(COVER_TEXTS, picks), 1):
        draw_cover(s["path"], lines, s["face_frac"], s["fx"], s["fy"],
                   os.path.join(OUT, f"cover_{i}.jpg"))
    print("готово, результат в", OUT)


if __name__ == "__main__":
    main()
