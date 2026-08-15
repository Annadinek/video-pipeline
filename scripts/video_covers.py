#!/usr/bin/env python3
# video_covers.py — ДВА режима.
#
#   frames  (по умолчанию): скачать присланное видео, вынуть кадры, отобрать
#           чёткие кадры-кандидаты (резкость через numpy, тёмные пропускаем) и
#           закоммитить в assets/<id>/cand_*.jpg. Я смотрю их глазами и выбираю.
#
#   covers  <picks.json>: собрать готовые обложки из ВЫБРАННЫХ кадров и текста.
#           Запускаю локально (кадры уже в репозитории) — OpenCV не нужен.
#
# Текст обложек берётся из названия видео (слова Анны), не выдумывается.
# Ничего в бот сам не шлёт.

import glob
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

try:
    import numpy as np
except Exception:
    np = None

WORK = "work"
FRAMES = os.path.join(WORK, "frames")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1280, 720
YELLOW = (255, 205, 0)
DARK = (20, 20, 20)
FACE_OUT = 0.34
FACE_AT_Y = 0.38


# ---------- режим frames ----------

def download(video_id):
    os.makedirs(WORK, exist_ok=True)
    raw = os.path.join(WORK, "raw.mp4")
    if os.path.exists(raw):
        return raw
    base = ["yt-dlp", "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=web_safari,web,tv",
            "--retries", "5", "--fragment-retries", "5"]
    if os.path.exists(os.path.join(WORK, "cookies.txt")):
        base += ["--cookies", os.path.join(WORK, "cookies.txt")]
    cmd = base + ["-S", "res,ext:mp4:m4a", "-f", "bv*+ba/b",
                  "--merge-output-format", "mp4",
                  "-o", raw, f"https://www.youtube.com/watch?v={video_id}"]
    for a in range(1, 4):
        if subprocess.run(cmd).returncode == 0 and os.path.exists(raw):
            return raw
        print(f"скачивание: попытка {a} не удалась")
    raise SystemExit("не удалось скачать видео")


def extract_frames(raw):
    os.makedirs(FRAMES, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-vf", "fps=1/4",
                    "-q:v", "2", os.path.join(FRAMES, "f_%04d.jpg"),
                    "-loglevel", "error"], check=True)
    return sorted(glob.glob(os.path.join(FRAMES, "f_*.jpg")))


def frame_stats(path):
    im = Image.open(path).convert("L").resize((320, 180))
    if np is None:
        return {"path": path, "bright": 128, "sharp": 0}
    a = np.asarray(im, dtype="float64")
    bright = float(a.mean())
    # резкость: энергия градиента (края)
    gx = np.abs(np.diff(a, axis=1)).mean()
    gy = np.abs(np.diff(a, axis=0)).mean()
    sharp = float(gx + gy)
    return {"path": path, "bright": bright, "sharp": sharp}


def pick_frames(video_id):
    os.makedirs(WORK, exist_ok=True)
    out = os.path.join("assets", video_id)
    os.makedirs(out, exist_ok=True)
    raw = download(video_id)
    frames = extract_frames(raw)
    print(f"кадров всего: {len(frames)}")
    stats = [frame_stats(f) for f in frames]
    # отбрасываем слишком тёмные/светлые
    good = [s for s in stats if 45 < s["bright"] < 225]
    good.sort(key=lambda s: s["sharp"], reverse=True)
    # берём топ по резкости, но не соседние кадры (разнообразие)
    idx = {s["path"]: i for i, s in enumerate(stats)}
    picks = []
    for s in good:
        if all(abs(idx[s["path"]] - idx[p["path"]]) > 1 for p in picks):
            picks.append(s)
        if len(picks) >= 24:
            break
    meta = []
    for i, s in enumerate(picks, 1):
        dst = os.path.join(out, f"cand_{i:02d}.jpg")
        Image.open(s["path"]).convert("RGB").save(dst, quality=88)
        meta.append({"n": i, "src": os.path.basename(s["path"]),
                     "bright": round(s["bright"], 1), "sharp": round(s["sharp"], 2)})
        print(f"кандидат {i}: {os.path.basename(s['path'])} "
              f"(яркость {s['bright']:.0f}, резкость {s['sharp']:.1f})")
    with open(os.path.join(out, "cands.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    make_contact_sheet(picks, os.path.join(out, "contact_sheet.jpg"))
    print("готово, кандидаты в", out)


def make_contact_sheet(picks, out_path, cols=4, cw=360, ch=270):
    """Одна картинка-сетка со всеми кандидатами и номерами — для быстрого выбора."""
    n = len(picks)
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * ch), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(FONT_PATH, 40)
    except Exception:
        font = ImageFont.load_default()
    for i, s in enumerate(picks):
        im = Image.open(s["path"]).convert("RGB").resize((cw, ch))
        r, c = divmod(i, cols)
        x, y = c * cw, r * ch
        sheet.paste(im, (x, y))
        label = str(i + 1)
        d.rectangle([x + 6, y + 6, x + 6 + 58, y + 6 + 52], fill=(0, 0, 0))
        d.text((x + 16, y + 8), label, font=font, fill=(255, 205, 0))
    sheet.save(out_path, quality=88)
    print("контактный лист:", out_path)


# ---------- режим covers ----------

def load_bg(path, fx, fy, face_frac):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = FACE_OUT / max(face_frac, 0.08)
    crop_h = int(round(H / scale))
    crop_w = int(round(crop_h * 16 / 9))
    if crop_w > w:
        crop_w, crop_h = w, int(round(w * 9 / 16))
    if crop_h > h:
        crop_h = h
        crop_w = min(int(round(h * 16 / 9)), w)
    cx, cy = fx * w, fy * h
    x0 = max(0, min(int(round(cx - 0.50 * crop_w)), w - crop_w))
    y0 = max(0, min(int(round(cy - FACE_AT_Y * crop_h)), h - crop_h))
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


def make_covers(picks_path):
    with open(picks_path, encoding="utf-8") as f:
        spec = json.load(f)
    out = os.path.dirname(picks_path)
    for i, item in enumerate(spec["covers"], 1):
        lines = [(t, s) for t, s in item["lines"]]
        draw_cover(item["frame"], lines, item.get("face_frac", 0.22),
                   item.get("fx", 0.5), item.get("fy", 0.42),
                   os.path.join(out, f"cover_{i}.jpg"))


def main():
    args = sys.argv[1:]
    if args and args[0] == "covers":
        make_covers(args[1])
    else:
        video_id = args[0] if args else "NtX08PXq7i8"
        pick_frames(video_id)


if __name__ == "__main__":
    main()
