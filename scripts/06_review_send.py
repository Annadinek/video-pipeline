#!/usr/bin/env python3
# 06_review_send.py — Круг 1 согласования (длинное видео).
# 1) заливает готовое видео на YouTube как unlisted (черновик),
# 2) делает 3 обложки (кадр + текст-крючок),
# 3) пишет заголовок и описание,
# 4) шлёт всё Анне в Telegram: ссылку + 3 обложки + заголовок/описание + отчёт,
# 5) ставит состояние awaiting_review и сохраняет мелкие артефакты в review/.
#
# Сам файл видео в Telegram НЕ шлём — он тяжёлый (лимит бота 50 МБ). Анна
# смотрит готовый ролик по unlisted-ссылке на YouTube.

import json
import os
import subprocess

import config
import state
import texts
import tg
import yt_ops

WORK = config.WORK_DIR
REVIEW = config.REVIEW_DIR
FINAL = os.path.join(WORK, "03_final.mp4")
TRANSCRIPT = os.path.join(WORK, "transcript.json")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def make_thumbnails(hooks):
    """3 обложки 1280x720: кадр из видео + крупный текст-крючок с обводкой."""
    os.makedirs(REVIEW, exist_ok=True)
    dur = _duration(FINAL)
    spots = [dur * 0.2, dur * 0.5, dur * 0.8]  # три разных момента
    paths = []
    for i, (t, hook) in enumerate(zip(spots, hooks), start=1):
        out = os.path.join(REVIEW, f"thumb{i}.jpg")
        # Экранируем текст для drawtext.
        safe = hook.replace("'", "").replace(":", " ")
        vf = (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawtext=fontfile={FONT}:text='{safe}':fontcolor=white:fontsize=64:"
            "borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.12"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", FINAL, "-frames:v", "1",
             "-vf", vf, "-q:v", "2", out],
            check=True, capture_output=True,
        )
        paths.append(out)
    return paths


def build_report():
    """Строка отчёта. ВРЕМЕННО: реальные цифры дрожания добавит 02_process позже."""
    r = "work/report.txt"
    if os.path.exists(r):
        return open(r, encoding="utf-8").read().strip()
    return ("Отчёт: слежение за лицом на длинном видео выключено (исходник 1080p); "
            "шумодав afftdn; громкость −14 LUFS. (детальные цифры дрожания добавлю позже)")


def main(source_video_id):
    segments = texts.load_segments(TRANSCRIPT)
    title = texts.make_title(segments)
    description = texts.make_description(segments)
    hooks = texts.make_hooks(segments, 3)

    print("06: заливаю unlisted-копию на YouTube…")
    review_video_id = yt_ops.upload_video(FINAL, title, description, privacy="unlisted")
    link = f"https://youtu.be/{review_video_id}"

    print("06: делаю 3 обложки…")
    thumbs = make_thumbnails(hooks)

    # Сохраняем мелкие артефакты в review/ (переживут стирание машины).
    os.makedirs(REVIEW, exist_ok=True)
    with open(os.path.join(REVIEW, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"source_video_id": source_video_id,
                   "review_video_id": review_video_id,
                   "title": title, "description": description, "hooks": hooks},
                  f, ensure_ascii=False, indent=2)

    # Отправляем Анне.
    tg.send_message(
        "КРУГ 1 — длинное видео на проверку.\n\n"
        f"Видео (черновик, по ссылке): {link}\n\n"
        f"Заголовок: {title}\n\n"
        f"Описание: {description}\n\n"
        f"{build_report()}\n\n"
        "Ниже 3 обложки. Ответь:\n"
        "• «ОК 2» — публикуем, обложка №2 (номер выбери сама)\n"
        "• или напиши, что исправить."
    )
    for i, tp in enumerate(thumbs, start=1):
        tg.send_photo(tp, caption=f"Обложка №{i}: {hooks[i-1]}")

    state.set_review(state="awaiting_review",
                     source_video_id=source_video_id,
                     review_video_id=review_video_id,
                     correction=None)
    print(f"06: отправлено. review_video_id={review_video_id}")


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else state.get_review().get("source_video_id")
    main(src)
