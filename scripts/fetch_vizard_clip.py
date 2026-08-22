#!/usr/bin/env python3
# fetch_vizard_clip.py — забирает ОДИН готовый клип из проекта Vizard (для замера).
# Не режет заново, не платит: только query существующего проекта + скачивание.
# Запуск: fetch_vizard_clip.py <project_id> <out.mp4> [index]

import sys

import requests

import config

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"


def main():
    project_id = sys.argv[1]
    out = sys.argv[2]
    idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    key = config.require_env("VIZARDAI_API_KEY")
    data = requests.get(f"{API}/project/query/{project_id}",
                        headers={"VIZARDAI_API_KEY": key}, timeout=60).json()
    vids = data.get("videos") or []
    if not vids:
        raise SystemExit(f"Vizard: у проекта {project_id} нет клипов (code={data.get('code')}). "
                         "Возможно, ссылки истекли (живут 7 дней).")
    url = vids[idx].get("videoUrl")
    print(f"Vizard: клипов {len(vids)}, качаю #{idx}: {vids[idx].get('title')}")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f"скачал: {out}")


if __name__ == "__main__":
    main()
