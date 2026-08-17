#!/usr/bin/env python3
# vizard_finish.py — финальная сборка Shorts из ЧИСТЫХ клипов Vizard (без его субтитров):
#   • лицо ДАЛЬШE и немного У́ЖЕ (контент масштабируем: по ширине SCALE_X, по высоте SCALE_Y);
#   • по КРАЯМ лёгкое размытие (тот же кадр, размытый, как фон — центр резкий, качество то же);
#   • ФОНОВАЯ музыка (тихо, под голос);
#   • КАРАОКЕ-субтитры: белый текст, произносимое слово зелёным (наш стиль).
# Кадр остаётся 1080x1920. Ничего не растягиваем.
#
# Env: PROJECT_ID, INDICES, MUSIC_FILE, MUSIC_GAIN, SCALE_X, SCALE_Y, BLUR_SIGMA, SEND.

import importlib
import os
import subprocess

import requests

import tg

rs = importlib.import_module("resub")  # переиспуем transcribe_words + build_ass

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ["PROJECT_ID"].strip()
INDICES = [int(x) for x in os.environ.get("INDICES", "1").split(",") if x.strip()]
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music/anna_piano.mp3")
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.12")
SCALE_X = float(os.environ.get("SCALE_X", "0.82"))   # по ширине (у́же)
SCALE_Y = float(os.environ.get("SCALE_Y", "0.90"))   # по высоте (дальше)
BLUR_SIGMA = os.environ.get("BLUR_SIGMA", "24")
# PLAIN=1 (по умолчанию): НИЧЕГО не размываем и не сужаем — оставляем чёткий
# кадр Vizard как есть, только караоке-субтитры + музыка (Анна: размытые края
# «размазывают лицо», оставить как на её скрине 1). PLAIN=0 — старый режим с
# размытыми краями/уменьшением (по умолчанию выключен).
PLAIN = os.environ.get("PLAIN", "1") == "1"
SEND = os.environ.get("SEND", "0") == "1"
OUT = "assets/vzfinish"


def get_clips():
    key = os.environ["VIZARDAI_API_KEY"].strip()
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


def finish(clip, ass, out, music):
    audio = (f"[0:a]volume=1.0[a0];[1:a]volume={MUSIC_GAIN}[a1];"
             f"[a0][a1]amix=inputs=2:duration=first[a]")
    if PLAIN:
        # Чёткий кадр Vizard как есть + караоке-субтитры + музыка. Ничего не
        # размываем и не сужаем.
        fc = f"[0:v]ass={ass}[v];" + audio
    else:
        fw = int(round(1080 * SCALE_X / 2) * 2)
        fh = int(round(1920 * SCALE_Y / 2) * 2)
        x = (1080 - fw) // 2
        y = (1920 - fh) // 2
        fc = (
            f"[0:v]scale=1080:1920,setsar=1,split[bgsrc][fgsrc];"
            f"[bgsrc]gblur=sigma={BLUR_SIGMA}[bg];"
            f"[fgsrc]scale={fw}:{fh}[fg];"
            f"[bg][fg]overlay={x}:{y}[v0];"
            f"[v0]ass={ass}[v];" + audio
        )
    subprocess.run(["ffmpeg", "-y", "-i", clip, "-stream_loop", "-1", "-i", music,
                    "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"],
                   check=True)
    return out


def main():
    os.makedirs("work", exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    clips = get_clips()
    if not clips:
        raise SystemExit(f"нет клипов у проекта {PROJECT_ID}")
    done = 0
    for idx in INDICES:
        if idx < 1 or idx > len(clips):
            print(f"клип {idx}: вне диапазона 1..{len(clips)}")
            continue
        c = clips[idx - 1]
        clip = download(c["videoUrl"], f"work/vf_{idx}.mp4")
        title = (c.get("title") or "").strip()
        # RAW=1 — отправить ОРИГИНАЛ клипа Vizard как есть, ничего не меняя
        # (ни субтитров, ни музыки, ни кадрирования). «Ровно то, что сделал Vizard».
        if os.environ.get("RAW", "0") == "1":
            subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", clip, "-frames:v", "1",
                            "-q:v", "2", os.path.join(OUT, f"raw_{idx}.jpg"), "-loglevel", "error"],
                           check=False)
            if SEND:
                size = os.path.getsize(clip) / 1e6
                if size <= 49:
                    tg.send_video(clip, caption=f"Vizard клип {idx} (оригинал, без обработки): {title}")
                else:
                    tg.send_message(f"Vizard клип {idx}: {title} — файл {size:.0f} МБ, велик для бота.")
            print(f"клип {idx}: оригинал Vizard ({'в бот' if SEND else 'превью'})")
            done += 1
            continue
        words = rs.transcribe_words(clip)
        ass = rs.build_ass(words, f"work/subs_{idx}.ass")
        out = finish(clip, ass, f"work/short_{idx}.mp4", MUSIC_FILE)
        subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", out, "-frames:v", "1",
                        "-q:v", "2", os.path.join(OUT, f"short_{idx}.jpg"), "-loglevel", "error"],
                       check=False)
        if SEND:
            size = os.path.getsize(out) / 1e6
            if size <= 49:
                tg.send_video(out, caption=f"Шорт {idx}: {title}")
            else:
                tg.send_message(f"Шорт {idx}: {title} — файл {size:.0f} МБ, велик для бота.")
        print(f"клип {idx}: готов ({'в бот' if SEND else 'превью'}), слов {len(words)}")
        done += 1
    print(f"ГОТОВО: {done} из {len(INDICES)}")


if __name__ == "__main__":
    main()
