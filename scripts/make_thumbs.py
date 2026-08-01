#!/usr/bin/env python3
# make_thumbs.py — делает 3 обложки 1280x720 из кадров эталонного видео.
# Стиль (по скринам Анны + правка «лицо крупнее, аккуратнее»): КРУПНОЕ лицо
# (приближаем), лицо открыто и подсвечено; текст ЗАГЛАВНЫМИ, аккуратный, НИЖЕ лица,
# ключевое слово — на жёлтой плашке, остальное белым с обводкой.
#
# Лицо в кадрах примерно в одном месте (женщина за рулём, лицо сверху-в-центре),
# поэтому наводимся по координате лица (доля кадра) и приближаем.

import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

OUT = os.environ.get("THUMBS_OUT", "thumbs")
FONT_PATH = os.environ.get(
    "THUMB_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
W, H = 1280, 720
YELLOW = (255, 205, 0)
DARK = (20, 20, 20)

# (кадр, [ (строка, стиль) ], (fx, fy) — доля кадра, где центр лица)
# Тексты — из самого видео (про свободу, гармонию, желания души), яркие «жёлтые
# заголовки». Вариант 1 Анна одобрила — оставляем.
VARIANTS = [
    ("reference/frame08_t554s.jpg", [("ЧТО ТАКОЕ", "white"), ("СВОБОДА", "yellow")], (0.56, 0.45)),
    ("reference/frame13_t900s.jpg", [("КАК ЖИТЬ", "white"), ("В ГАРМОНИИ", "yellow")], (0.58, 0.46)),
    ("reference/frame15_t1039s.jpg", [("ЧТО ХОЧЕТ", "white"), ("ТВОЯ ДУША", "yellow")], (0.57, 0.44)),
]

FACE_FRAC = 0.22   # примерная высота лица как доля высоты кадра
FACE_OUT = 0.42    # какой долей высоты обложки хотим лицо (крупно)
FACE_AT_Y = 0.34   # где по вертикали разместить центр лица в обложке


def load_bg(path, fx, fy):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = FACE_OUT / FACE_FRAC          # во столько приближаем
    crop_h = int(round(H / scale))
    crop_w = int(round(crop_h * 16 / 9))
    cx, cy = fx * w, fy * h
    x0 = int(round(cx - 0.50 * crop_w))
    y0 = int(round(cy - FACE_AT_Y * crop_h))
    x0 = max(0, min(x0, w - crop_w)) if crop_w <= w else 0
    y0 = max(0, min(y0, h - crop_h)) if crop_h <= h else 0
    if crop_w > w:   # если приближение шире кадра — ограничим по ширине
        crop_w = w
        crop_h = int(round(crop_w * 9 / 16))
        x0 = 0
        y0 = max(0, min(int(round(cy - FACE_AT_Y * crop_h)), h - crop_h))
    im = im.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize((W, H))
    im = ImageEnhance.Brightness(im).enhance(1.12)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    im = ImageEnhance.Color(im).enhance(1.10)
    # Мягкое затемнение снизу под текст (не перекрывает лицо сверху).
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        t = max(0.0, (y - H * 0.55) / (H * 0.45))
        grad.putpixel((0, y), int(190 * t))
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


def draw_thumb(path, lines, face, out):
    im = load_bg(path, *face)
    d = ImageDraw.Draw(im)
    margin = 64
    max_w = W - margin * 2
    font = min((fit_font(t, max_w) for t, _ in lines), key=lambda f: f.size)
    heights = [d.textbbox((0, 0), t, font=font, stroke_width=6)[3] for t, _ in lines]
    gap = 12
    total = sum(heights) + gap * (len(lines) - 1)
    y = H - 64 - total
    x = margin
    for (t, st), h in zip(lines, heights):
        bb = d.textbbox((x, y), t, font=font, stroke_width=6)
        if st == "yellow":
            px, py = 22, 12
            d.rounded_rectangle([bb[0] - px, bb[1] - py, bb[2] + px, bb[3] + py],
                                radius=14, fill=YELLOW)
            d.text((x, y), t, font=font, fill=DARK)
        else:
            # мягкая тень + тонкая обводка = аккуратнее, чем толстый чёрный контур
            d.text((x + 4, y + 4), t, font=font, fill=(0, 0, 0))
            d.text((x, y), t, font=font, fill=(255, 255, 255),
                   stroke_width=6, stroke_fill=(0, 0, 0))
        y += h + gap
    im.save(out, quality=90)
    print("готово:", out)


def main():
    os.makedirs(OUT, exist_ok=True)
    for i, (frame, lines, face) in enumerate(VARIANTS, start=1):
        draw_thumb(frame, lines, face, os.path.join(OUT, f"thumb{i}.jpg"))


if __name__ == "__main__":
    main()
