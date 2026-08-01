#!/usr/bin/env python3
# make_thumbs.py — делает 3 обложки 1280x720 из кадров эталонного видео.
# Стиль (по скринам Анны): крупный жирный текст ЗАГЛАВНЫМИ; ключевое слово —
# на жёлтой плашке (тёмный текст), остальное — белым с толстой чёрной обводкой;
# лицо подсвечено, снизу лёгкое затемнение, чтобы текст читался.
#
# Кадры берутся из папки reference/ (закоммичены заранее). Результат: thumb1-3.jpg.

import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

OUT = os.environ.get("THUMBS_OUT", "thumbs")
FONT_PATH = os.environ.get(
    "THUMB_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
W, H = 1280, 720
YELLOW = (255, 205, 0)
DARK = (22, 22, 22)

# (кадр, [ (строка, стиль) ... ] ) — стиль: "white" или "yellow"
VARIANTS = [
    ("reference/frame08_t554s.jpg", [("ЧТО ТАКОЕ", "white"), ("СВОБОДА", "yellow")]),
    ("reference/frame13_t900s.jpg", [("ВЫХОД", "white"), ("ТИШИНЫ", "yellow")]),
    ("reference/frame15_t1039s.jpg", [("ТЫ —", "white"), ("НАБЛЮДАТЕЛЬ", "yellow")]),
]


def load_bg(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    # Обрезаем в 16:9, держим верх-центр (там лицо), низ с её субтитрами убираем.
    crop_h = int(w * 9 / 16)
    if crop_h <= h:
        # Берём верхнюю часть кадра: лицо сверху-в-центре, а её нижние субтитры
        # остаются НИЖЕ обрезки и в обложку не попадают.
        y0 = int((h - crop_h) * 0.12)
        im = im.crop((0, y0, w, y0 + crop_h))
    else:
        crop_w = int(h * 16 / 9)
        x0 = (w - crop_w) // 2
        im = im.crop((x0, 0, x0 + crop_w, h))
    im = im.resize((W, H))
    # Подсветить лицо/картинку: ярче, контрастнее, чуть насыщеннее.
    im = ImageEnhance.Brightness(im).enhance(1.10)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    im = ImageEnhance.Color(im).enhance(1.10)
    # Затемнение снизу под текст (сильнее и выше — заодно прячет любые остатки).
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        t = max(0.0, (y - H * 0.42) / (H * 0.58))
        grad.putpixel((0, y), int(215 * t))
    grad = grad.resize((W, H))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    im = Image.composite(black, im, grad)
    return im


def fit_font(text, max_w):
    """Подбираем размер шрифта так, чтобы строка влезла по ширине."""
    size = 130
    while size > 48:
        f = ImageFont.truetype(FONT_PATH, size)
        bb = f.getbbox(text, stroke_width=9)
        if bb[2] - bb[0] <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(FONT_PATH, 48)


def draw_thumb(path, lines, out):
    im = load_bg(path)
    d = ImageDraw.Draw(im)
    margin = 60
    max_w = W - margin * 2 - 60
    # Единый размер шрифта — по самой длинной строке.
    font = min((fit_font(t, max_w) for t, _ in lines), key=lambda f: f.size)
    # Меряем высоты.
    heights = []
    for t, _ in lines:
        bb = d.textbbox((0, 0), t, font=font, stroke_width=9)
        heights.append(bb[3] - bb[1])
    gap = 14
    total = sum(heights) + gap * (len(lines) - 1)
    y = H - 70 - total
    x = margin
    for (t, st), h in zip(lines, heights):
        bb = d.textbbox((x, y), t, font=font, stroke_width=9)
        if st == "yellow":
            px, py = 26, 14
            d.rounded_rectangle([bb[0] - px, bb[1] - py, bb[2] + px, bb[3] + py],
                                radius=16, fill=YELLOW)
            d.text((x, y), t, font=font, fill=DARK)
        else:
            d.text((x, y), t, font=font, fill=(255, 255, 255),
                   stroke_width=9, stroke_fill=(0, 0, 0))
        y += h + gap
    im.save(out, quality=90)
    print("готово:", out)


def main():
    os.makedirs(OUT, exist_ok=True)
    for i, (frame, lines) in enumerate(VARIANTS, start=1):
        draw_thumb(frame, lines, os.path.join(OUT, f"thumb{i}.jpg"))


if __name__ == "__main__":
    main()
