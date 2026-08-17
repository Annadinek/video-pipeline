#!/usr/bin/env python3
# list_durations.py — только показать длину каждого клипа проекта Vizard (диагностика,
# ничего не грузит). Нужно, чтобы понять, почему YouTube кладёт клипы в обычные видео,
# а не в Shorts (Shorts надёжно = вертикаль + ≤60 сек).
import os
import requests

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PID = os.environ["PROJECT_ID"].strip()
key = os.environ["VIZARDAI_API_KEY"].strip()
data = requests.get(f"{API}/project/query/{PID}",
                    headers={"VIZARDAI_API_KEY": key}, timeout=60).json()
vs = data.get("videos") or []
over = 0
for i, c in enumerate(vs, 1):
    sec = round((c.get("videoMsDuration") or 0) / 1000, 1)
    flag = "  >60!" if sec > 60 else ""
    if sec > 60:
        over += 1
    print(f"{i:2d}: {sec:6.1f} с{flag}  {(c.get('title') or '').strip()[:50]}")
print(f"ВСЕГО {len(vs)} клипов, длиннее 60 сек: {over}")
