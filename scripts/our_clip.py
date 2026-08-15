#!/usr/bin/env python3
# our_clip.py — НАША нарезка вертикали 9:16 вместо Vizard.
# Замер показал: наша нарезка вдвое чётче Vizard (33.4 против 15.1), а у Vizard
# нет настройки качества. Поэтому режем сами.
#
# Что делает:
#   1) скачивает исходник (макс. качество);
#   2) вырезает кусок [SEG_START, SEG_START+SEG_LEN] копией потока (без пересжатия);
#   3) ведёт кадр по лицу (vertical_cut) → окно 9:16;
#   4) распознаёт речь на куске и вшивает субтитры (эталонный стиль);
#   5) ОДИН проход ffmpeg: кроп→масштаб 1080×1920→субтитры, видео кодируем один раз,
#      ЗВУК копируем потоком (-c:a copy) — настройки звука из исходника не теряются;
#   6) заливает unlisted и шлёт ссылку в бот.
#
# Vizard остаётся в коде (vizard_clip.py), но по умолчанию НЕ используется.

import importlib
import os
import subprocess

import tg
import yt_ops

so = importlib.import_module("subtitles_only")
vc = importlib.import_module("vertical_cut")

VIDEO = os.environ.get("VIDEO_ID", "").strip()
SEG_START = float(os.environ.get("SEG_START", "30"))
SEG_LEN = float(os.environ.get("SEG_LEN", "45"))
OUT_W, OUT_H = 1080, 1920


def cut_segment(raw):
    """Вырезаем кусок копией потока — без пересжатия (звук и видео как есть)."""
    seg = "work/seg.mp4"
    subprocess.run(["ffmpeg", "-y", "-ss", str(SEG_START), "-t", str(SEG_LEN),
                    "-i", raw, "-c", "copy", "-avoid_negative_ts", "make_zero",
                    seg, "-loglevel", "error"], check=True)
    return seg


def crop_window(seg):
    """Окно 9:16 по лицу. Если слежение недоступно — центр (громко сообщаем)."""
    width, height = vc.ffprobe_dimensions(seg)
    try:
        centers = vc.detect_face_centers(seg, width, height)
        crop_w, crop_h, x = vc.build_crop_expr(centers, width, height)
        n_found = sum(1 for _, cx in centers if abs(cx - width / 2) > 1)
        print(f"слежение за лицом: кадров {len(centers)}, лицо найдено ~{n_found}")
        return crop_w, crop_h, x, True
    except Exception as e:  # noqa: BLE001
        print(f"ВНИМАНИЕ: слежение сорвалось ({e}) — беру центр")
        crop_w = min(int(round(height * 9 / 16)), width)
        x = (width - crop_w) // 2
        return crop_w, height, x, False


def main():
    if not VIDEO:
        raise SystemExit("Не задан VIDEO_ID.")
    raw = so.download(VIDEO)
    seg = cut_segment(raw)

    crop_w, crop_h, x, tracked = crop_window(seg)

    print("распознаю речь на куске...")
    words = so.transcribe(seg)
    ass = so.build_ass(words, "work/subs.ass", OUT_W, OUT_H) if words else None
    if not words:
        print("речь не распозналась — вставлю без субтитров")

    # ОДИН проход: кроп → масштаб 1080×1920 → субтитры. Звук копируем потоком.
    vf = f"crop={crop_w}:{crop_h}:{x}:0,scale={OUT_W}:{OUT_H}"
    if ass:
        vf += f",ass={ass}"
    out = "work/clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", seg, "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "copy", out, "-loglevel", "error"], check=True)

    print("заливаю клип на YouTube (unlisted)...")
    yt = yt_ops.upload_video(
        out, "Клип — наша нарезка (без Vizard)",
        "Тест: наша вертикальная нарезка vertical_cut.py + субтитры, "
        "звук копией потока. Без Vizard.", privacy="unlisted")
    vid = yt["id"] if isinstance(yt, dict) else yt
    link = f"https://youtu.be/{vid}"
    track_txt = "со слежением за лицом" if tracked else "по центру (слежение не сработало)"
    tg.send_message(
        f"Готовый клип НАШЕЙ нарезкой (без Vizard), {track_txt}, с субтитрами, "
        f"звук копией потока без пересжатия:\n{link}\n"
        f"Кусок {int(SEG_START)}–{int(SEG_START + SEG_LEN)} c. Посмотри резкость и субтитры.")
    print(f"ГОТОВО: {link} | слежение={tracked} | слов={len(words)}")


if __name__ == "__main__":
    main()
