#!/usr/bin/env python3
# vizard_clip.py — нарезка через Vizard.ai API (заменяет нашу ffmpeg-нарезку).
# По скиллу .claude/skills/using-vizard-api/: отправляем ссылку на видео,
# ждём готовые клипы, забираем их и отправляем Анне в Telegram-бот.
#
# Ключ — секрет VIZARDAI_API_KEY (только в GitHub Secrets, не в коде).
# Запуск: scripts/vizard_clip.py <videoUrl или videoId YouTube>
#
# Что делает по шагам:
#   1) POST /project/create — отдаём ссылку на видео (режим нарезки, много клипов);
#   2) каждые 30 c опрашиваем /project/query/{projectId}, пока не будет готово;
#   3) скачиваем клипы (ссылка живёт 7 дней) и шлём в бот: файл + заголовок +
#      оценка виральности; если файл больше лимита бота — шлём прямой ссылкой.

import os
import subprocess
import sys
import time

import requests

import config
import tg

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
LANG = os.environ.get("VIZARD_LANG", "ru")
# 0=авто длина; можно [2,3] для 30–90 c. Берём авто — Vizard сам выбирает лучшее.
PREFER_LENGTH = [int(x) for x in os.environ.get("VIZARD_PREFER", "0").split(",")]
MAX_SEND = int(os.environ.get("VIZARD_MAX_SEND", "6"))  # сколько клипов слать в бот
POLL_EVERY = 30
MAX_WAIT = int(os.environ.get("VIZARD_MAX_WAIT", "1800"))  # до 30 минут


def video_type_for(url):
    """Определяем источник по ссылке (см. таблицу videoType в скилле)."""
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return 2, None
    if "drive.google" in u:
        return 3, None
    if "vimeo.com" in u:
        return 4, None
    # прямой файл
    ext = u.rsplit(".", 1)[-1].split("?")[0] if "." in u else "mp4"
    if ext not in ("mp4", "mov", "avi", "3gp"):
        ext = "mp4"
    return 1, ext


# Субтитры/хедлайны Vizard. У видео Анны субтитры УЖЕ вшиты — поэтому по умолчанию
# отключаем добавление субтитров Vizard (иначе получаются двойные). 1=вкл, 0=выкл.
SUBTITLE_SWITCH = int(os.environ.get("SUBTITLE_SWITCH", "0"))
HEADLINE_SWITCH = int(os.environ.get("HEADLINE_SWITCH", "0"))


def create_project(api_key, video_url):
    vtype, ext = video_type_for(video_url)
    body = {
        "videoUrl": video_url,
        "videoType": vtype,
        "lang": LANG,
        "preferLength": PREFER_LENGTH,
        "subtitleSwitch": SUBTITLE_SWITCH,
        "headlineSwitch": HEADLINE_SWITCH,
    }
    if vtype == 1:
        body["ext"] = ext
    r = requests.post(f"{API}/project/create",
                      headers={"Content-Type": "application/json",
                               "VIZARDAI_API_KEY": api_key},
                      json=body, timeout=60)
    data = r.json()
    if data.get("code") not in (2000, 1000) or not data.get("projectId"):
        raise RuntimeError(f"Vizard create: {data}")
    return data["projectId"]


def wait_for_clips(api_key, project_id):
    url = f"{API}/project/query/{project_id}"
    waited = 0
    while waited < MAX_WAIT:
        time.sleep(POLL_EVERY)
        waited += POLL_EVERY
        data = requests.get(url, headers={"VIZARDAI_API_KEY": api_key}, timeout=60).json()
        code = data.get("code")
        if code == 2000 and data.get("videos"):
            return data["videos"]
        if code == 1000:
            continue  # ещё обрабатывается
        raise RuntimeError(f"Vizard query: {data}")
    raise TimeoutError("Vizard: превышено время ожидания")


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def main():
    api_key = config.require_env("VIZARDAI_API_KEY")
    # Если задан VIZARD_PROJECT_ID — берём УЖЕ готовый проект Vizard и не режем
    # заново (не платим за новую нарезку). Иначе создаём новый проект по ссылке.
    existing = os.environ.get("VIZARD_PROJECT_ID", "").strip()
    if existing:
        project_id = existing
        print(f"Vizard: беру готовый проект {project_id}, забираю клипы...")
    else:
        if len(sys.argv) > 1 and sys.argv[1].strip():
            arg = sys.argv[1].strip()
        else:
            arg = os.environ.get("VIDEO_URL", "").strip()
        if not arg:
            raise SystemExit("Не задана ссылка на видео (аргумент или VIDEO_URL).")
        # Если передан только ID YouTube — собираем полную ссылку.
        video_url = arg if "://" in arg else f"https://www.youtube.com/watch?v={arg}"
        print(f"Vizard: отправляю на нарезку {video_url}")
        project_id = create_project(api_key, video_url)
        print(f"Vizard: проект {project_id}, жду клипы (опрос каждые {POLL_EVERY} c)...")
    clips = wait_for_clips(api_key, project_id)
    # Сортируем по виральности (Vizard уже сортирует, но подстрахуемся).
    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)
    print(f"Vizard: готово клипов — {len(clips)}")
    os.makedirs("shorts", exist_ok=True)

    # Режим проверки: скачать пару клипов и вынуть кадры (начало+середина) в
    # shorts_preview/, чтобы Клод посмотрел ГЛАЗАМИ, нет ли двойных субтитров.
    # В бот ничего не шлём.
    if os.environ.get("PREVIEW_ONLY", "0") == "1":
        os.makedirs("shorts_preview", exist_ok=True)
        n = min(int(os.environ.get("PREVIEW_N", "2")), len(clips))
        for i, c in enumerate(clips[:n], 1):
            p = download(c.get("videoUrl"), os.path.join("shorts", f"chk_{i}.mp4"))
            dur = (c.get("videoMsDuration") or 0) / 1000
            for tag, ss in (("a", "1.0"), ("b", f"{max(dur / 2, 1.5):.1f}")):
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", p, "-frames:v", "1",
                                "-q:v", "3", os.path.join("shorts_preview", f"chk_{i}{tag}.jpg")],
                               check=True, capture_output=True)
            print(f"проверка клип {i}: {c.get('title')}")
        print("PREVIEW_ONLY: кадры в shorts_preview/, в бот не слал")
        return

    tg.send_message(f"Vizard нарезал {len(clips)} клипов по видео. Показываю лучшие "
                    f"{min(MAX_SEND, len(clips))} (по оценке виральности). "
                    "По каждому: «ок / убрать / исправить».")
    # Готовые подписи по номеру клипа (название + текст голосом Анны).
    captions = {}
    cf = os.environ.get("CAPTIONS_FILE", "").strip()
    if cf and os.path.exists(cf):
        import json
        with open(cf, encoding="utf-8") as f:
            captions = json.load(f)
        print(f"Подписи взяты из {cf}: {len(captions)} шт.")
    sent = 0
    for i, c in enumerate(clips[:MAX_SEND], 1):
        title = (c.get("title") or "").strip()
        vs = c.get("viralScore") or "—"
        dur = round((c.get("videoMsDuration") or 0) / 1000)
        cap = captions.get(str(i)) or f"Клип {i} • {dur} c • виральность {vs}/10\n{title}"
        url = c.get("videoUrl")
        try:
            path = download(url, os.path.join("shorts", f"vizard_{i}.mp4"))
            size = os.path.getsize(path) / 1e6
            if size <= 49:
                tg.send_video(path, caption=cap)
            else:
                tg.send_message(cap + f"\nФайл {size:.0f} МБ (велик для бота), скачать: {url}")
            sent += 1
        except Exception as e:
            tg.send_message(cap + f"\nСсылка на клип (7 дней): {url}")
            print(f"клип {i}: не смог отправить файлом ({e}), послал ссылкой")
            sent += 1
        print(f"клип {i}: {title} | виральность {vs} | {dur} c")

    print(f"ГОТОВО: Vizard вернул {len(clips)} клипов, отправил в бот {sent}.")


if __name__ == "__main__":
    main()
