#!/usr/bin/env python3
"""
vizard_cut_file.py — нарезка вертикали 9:16 через Vizard по НАШЕМУ ФАЙЛУ (вариант A).

Зачем отдельно от vizard_clip.py: тот берёт ссылку YouTube и шлёт клипы в Telegram
на проверку. Здесь на вход — публичный URL нашего УЖЕ обработанного файла
(clip_color.mp4, выложенного релиз-ассетом), чтобы Vizard резал именно обработку,
а не сырой ролик (иначе теряем звук/стаб/цвет — решение Анны, вариант A).

  - videoType = 1 (прямой файл по URL; ext берём из ссылки, обычно mp4);
  - режим множественной нарезки (getClips НЕ ставим в 0 → Vizard возвращает
    несколько вертикальных клипов, отсортированных по виральности);
  - subtitleSwitch = 0 — субтитры Vizard ВЫКЛЮЧЕНЫ, свои рисуем на этапе 03b;
  - клипы скачиваем локально, каждый в свою папку out_dir/clip_NN/clip.mp4
    (в своей папке — чтобы у каждого был свой transcript.json для субтитров);
  - НИКАКОГО Telegram.

Ключ — секрет VIZARDAI_API_KEY (только GitHub Secrets). Запуск:
  scripts/vizard_cut_file.py --url <public_url.mp4> --out-dir outputs/clips [--lang ru]
"""

import argparse
import json
import os
import time

import requests

import config

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
POLL_EVERY = 30
MAX_WAIT = int(os.environ.get("VIZARD_MAX_WAIT", "1800"))  # до 30 минут
# 0 = авто-длина; Vizard сам выбирает лучшие моменты.
PREFER_LENGTH = [int(x) for x in os.environ.get("VIZARD_PREFER", "0").split(",") if x.strip()]


def ext_from_url(url):
    tail = url.lower().split("?")[0].rsplit(".", 1)
    ext = tail[-1] if len(tail) == 2 else "mp4"
    return ext if ext in ("mp4", "mov", "avi", "3gp") else "mp4"


def create_project(api_key, url, lang):
    """Создать проект нарезки по прямому файлу. Субтитры Vizard выключены."""
    body = {
        "videoUrl": url,
        "videoType": 1,             # прямой файл по ссылке
        "ext": ext_from_url(url),   # обязателен для videoType=1
        "lang": lang,
        "preferLength": PREFER_LENGTH or [0],
        "subtitleSwitch": 0,        # свои субтитры рисуем сами (03b)
        "headlineSwitch": 0,
    }
    r = requests.post(
        f"{API}/project/create",
        headers={"Content-Type": "application/json", "VIZARDAI_API_KEY": api_key},
        json=body, timeout=60,
    )
    data = r.json()
    if data.get("code") not in (1000, 2000) or not data.get("projectId"):
        raise SystemExit(f"Vizard create не удался: {data}")
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
            print(f"Vizard: ещё режет… ({waited} c)")
            continue
        raise SystemExit(f"Vizard query не удался: {data}")
    raise SystemExit("Vizard: превышено время ожидания нарезки")


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="публичный URL нашего clip_color.mp4")
    ap.add_argument("--out-dir", required=True, help="куда класть клипы (по папкам clip_NN)")
    ap.add_argument("--lang", default=os.environ.get("VIZARD_LANG", "ru"))
    args = ap.parse_args()

    api_key = config.require_env("VIZARDAI_API_KEY")
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"Vizard: отправляю на нарезку файл {args.url} (videoType=1, субтитры выкл)")
    project_id = create_project(api_key, args.url, args.lang)
    print(f"Vizard: проект {project_id}, жду клипы (опрос каждые {POLL_EVERY} c)…")
    clips = wait_for_clips(api_key, project_id)

    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)

    meta = []
    for i, c in enumerate(clips, 1):
        d = os.path.join(args.out_dir, f"clip_{i:02d}")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "clip.mp4")
        download(c.get("videoUrl"), dst)
        dur = round((c.get("videoMsDuration") or 0) / 1000)
        item = {"n": i, "dir": d, "title": (c.get("title") or "").strip(),
                "viral_score": c.get("viralScore"), "seconds": dur}
        meta.append(item)
        print(f"клип {i}: {item['title']} | виральность {item['viral_score']} | {dur} c → {dst}")

    with open(os.path.join(args.out_dir, "clips.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": project_id, "count": len(clips), "clips": meta},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"ГОТОВО: Vizard вернул {len(clips)} клипов → {args.out_dir}")


if __name__ == "__main__":
    main()
