#!/usr/bin/env python3
# vizard_polish.py — доводит клипы Vizard до полноэкранного вертикального Shorts:
#   1) чёрные поля (Vizard вписал 4:3 в 9:16) заполняем РАЗМЫТЫМ фоном того же кадра
#      — лицо НЕ зумим, не «расширяем»; субтитры Vizard остаются как есть;
#   2) накладываем фоновую музыку (тихо, под голос);
#   3) шлём в бот (или только кадр-превью на проверку).
#
# Клипы берём из готового проекта Vizard (не режем заново).
# Env: PROJECT_ID, MUSIC_FILE, START (с какого клипа), LIMIT (сколько),
#      SEND (1=слать в бот, 0=только превью-кадры), MUSIC_GAIN.

import os
import re
import subprocess

import requests

import config
import tg

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ["PROJECT_ID"].strip()
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music/anna_piano.mp3")
START = int(os.environ.get("START", "0"))
LIMIT = int(os.environ.get("LIMIT", "1"))
SEND = os.environ.get("SEND", "0") == "1"
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.12")
OUT = "assets/vzpolish"


def get_clips():
    key = config.require_env("VIZARDAI_API_KEY")
    data = requests.get(f"{API}/project/query/{PROJECT_ID}",
                        headers={"VIZARDAI_API_KEY": key}, timeout=60).json()
    return data.get("videos") or []


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for ch in r.iter_content(1 << 20):
                f.write(ch)
    return path


def detect_content_box(clip):
    """cropdetect: находим область без чёрных полей → (w,h,x,y)."""
    r = subprocess.run(["ffmpeg", "-i", clip, "-vf", "cropdetect=24:2:0",
                        "-frames:v", "60", "-f", "null", "-"],
                       capture_output=True, text=True)
    boxes = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not boxes:
        return None
    w, h, x, y = map(int, boxes[-1])
    return w, h, x, y


def polish(clip, out, music):
    box = detect_content_box(clip)
    if not box:
        # запасной вариант: без заливки, просто музыка
        vf_map = ["-c:v", "copy"]
        filt = (f"[0:a]volume=1.0[a0];[1:a]volume={MUSIC_GAIN}[a1];"
                f"[a0][a1]amix=inputs=2:duration=first[a]")
        cmd = ["ffmpeg", "-y", "-i", clip, "-stream_loop", "-1", "-i", music,
               "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
               *vf_map, "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"]
        subprocess.run(cmd, check=True)
        return out
    w, h, x, y = box
    # контент (без полос) → split: один экземпляр растягиваем на фон и размываем,
    # второй кладём поверх на его РОДНОЕ место (лицо не зумим). Полосы уходят.
    filt = (
        f"[0:v]crop={w}:{h}:{x}:{y},split[c1][c2];"
        f"[c1]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,gblur=sigma=26[bg];"
        f"[bg][c2]overlay={x}:{y}[v];"
        f"[0:a]volume=1.0[a0];[1:a]volume={MUSIC_GAIN}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first[a]"
    )
    cmd = ["ffmpeg", "-y", "-i", clip, "-stream_loop", "-1", "-i", music,
           "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"]
    subprocess.run(cmd, check=True)
    return out


def main():
    os.makedirs("work", exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    clips = get_clips()
    if not clips:
        raise SystemExit(f"нет клипов у проекта {PROJECT_ID}")
    sel = clips[START:START + LIMIT]
    print(f"клипов всего {len(clips)}, обрабатываю {len(sel)} (с {START})")
    for i, c in enumerate(sel, start=START + 1):
        clip = download(c["videoUrl"], f"work/vz_{i}.mp4")
        out = polish(clip, f"work/short_{i}.mp4", MUSIC_FILE)
        # кадр-превью для глаз
        subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", out, "-frames:v", "1",
                        "-q:v", "2", os.path.join(OUT, f"short_{i}.jpg"), "-loglevel", "error"],
                       check=False)
        title = (c.get("title") or "").strip()
        if SEND:
            size = os.path.getsize(out) / 1e6
            if size <= 49:
                tg.send_video(out, caption=f"Шорт {i} (полноэкранный, музыка): {title}")
            else:
                tg.send_message(f"Шорт {i}: {title} — файл {size:.0f} МБ, велик для бота.")
        print(f"клип {i}: {title} готов ({'в бот' if SEND else 'превью'})")


if __name__ == "__main__":
    main()
