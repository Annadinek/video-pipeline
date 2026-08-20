#!/usr/bin/env python3
# publora_publish.py — АВТОвыкладка готовых роликов через Publora API.
# Publora сам публикует в Instagram / TikTok / YouTube (и др.) — от Анны ничего
# не требуется, только один раз подключить аккаунты в кабинете Publora и добавить
# ключ PUBLORA_API_KEY в GitHub Secrets.
#
# Что делает скрипт:
#   1. Берёт клипы проекта Vizard (в порядке videos = как в боте).
#   2. Для каждого нового клипа: качает → субтитры (resub) → музыка → готовый ролик.
#   3. Загружает ролик в Publora и ставит в расписание сразу на все выбранные сети.
#   4. Учёт выложенного — publora_published.json (ключ = ID проекта).
#   5. Отчёт Анне в бот.
#
# Env: PROJECT_ID, VIZARDAI_API_KEY, PUBLORA_API_KEY, TELEGRAM_BOT_TOKEN,
#      NETWORKS (instagram,tiktok,youtube), PUBLORA_MAX (сколько за запуск),
#      MUSIC_FILE, MUSIC_GAIN, SQUEEZE (0 = не трогать нарезку Vizard),
#      FONT_SIZE/MAX_WORDS/SUB_HL/SUB_OUTLINE/WHISPER_MODEL — стиль субтитров,
#      START_HOUR_UTC (7 = 10:00 по Турции), STEP_DAYS (1),
#      TEST_MINUTES (>0 — быстрый тест: поставить через N минут от сейчас),
#      DRY_RUN (1 = собрать ролик и прислать в бот, БЕЗ Publora).

import importlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import requests

import tg
import publora

rs = importlib.import_module("resub")  # transcribe_words + build_ass (стиль из env)

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
# Если PROJECT_ID пуст — берём проект по ссылке на видео. Раньше созданный проект
# запоминаем в vizard_projects.json, чтобы НЕ резать одно и то же видео повторно
# (у Vizard нет списка проектов, а ссылки на клипы живут 7 дней — потерять id нельзя).
VIDEO_URL = os.environ.get("VIDEO_URL", "").strip()
MAP_FILE = "vizard_projects.json"
# Ссылка на полное видео (ставим в конец подписи — «посмотреть на YouTube»).
SOURCE_URL = os.environ.get("SOURCE_URL", "").strip()
NETWORKS = [n.strip().lower() for n in
            os.environ.get("NETWORKS", "instagram,tiktok,youtube").split(",") if n.strip()]
MAX_POSTS = int(os.environ.get("PUBLORA_MAX", "3"))
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music/anna_piano.mp3")
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.12")
SQUEEZE = float(os.environ.get("SQUEEZE", "0") or "0")
START_HOUR_UTC = int(os.environ.get("START_HOUR_UTC", "7"))  # 07:00 UTC = 10:00 Турция
STEP_DAYS = int(os.environ.get("STEP_DAYS", "1"))
TEST_MINUTES = int(os.environ.get("TEST_MINUTES", "0"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
STATE_FILE = "publora_published.json"

FORBIDDEN_ROOTS = ["эзотерик", "вибрац", "саботаж", "квантов", "трансформац"]


def clean_vz(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"\(\d+\)", "", t).strip()
    t = re.sub(r"#\S+", "", t).strip()
    words = [w for w in t.split()
             if not any(r in w.lower() for r in FORBIDDEN_ROOTS)]
    return " ".join(words).strip(" —–-,")


def build_desc(vz_title):
    body = clean_vz(vz_title)
    if len(body) > 200:
        body = body[:200].rsplit(" ", 1)[0].rstrip(" ,—–-") + "…"
    parts = [body, "Запись на консультацию — Telegram @dinekanna_bot"]
    if SOURCE_URL:  # по просьбе Анны — ссылка на полное видео В КОНЦЕ подписи
        parts.append(f"▶️ Смотреть это видео полностью на YouTube:\n{SOURCE_URL}")
    return "\n\n".join(parts)


def resolve_project():
    """Определить ID проекта Vizard. Если PROJECT_ID задан — берём его. Иначе по
    VIDEO_URL: сперва ищем в vizard_projects.json (не режем повторно), а если нет —
    создаём проект один раз и запоминаем связку видео→проект."""
    global PROJECT_ID
    if PROJECT_ID:
        return PROJECT_ID
    if not VIDEO_URL:
        raise SystemExit("нужен PROJECT_ID или VIDEO_URL")
    mapping = {}
    if os.path.exists(MAP_FILE):
        try:
            mapping = json.load(open(MAP_FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            mapping = {}
    if VIDEO_URL in mapping:
        PROJECT_ID = str(mapping[VIDEO_URL])
        print(f"Vizard: беру готовый проект {PROJECT_ID} для {VIDEO_URL} (без новой нарезки)")
        return PROJECT_ID
    import vizard_clip as vz
    key = os.environ["VIZARDAI_API_KEY"].strip()
    url = VIDEO_URL if "://" in VIDEO_URL else f"https://www.youtube.com/watch?v={VIDEO_URL}"
    print(f"Vizard: проекта для {url} ещё нет — создаю (один раз)")
    pid = vz.create_project(key, url)
    vz.wait_for_clips(key, pid)  # дождаться готовности клипов
    PROJECT_ID = str(pid)
    mapping[VIDEO_URL] = PROJECT_ID
    try:
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    print(f"Vizard: проект {PROJECT_ID} создан и записан в {MAP_FILE}")
    return PROJECT_ID


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
    """Готовый ролик: (опц. сужение по высоте) + караоке-субтитры + музыка."""
    audio = (f"[0:a]volume=1.0[a0];[1:a]volume={MUSIC_GAIN}[a1];"
             f"[a0][a1]amix=inputs=2:duration=first[a]")
    if SQUEEZE and 0 < SQUEEZE < 1:
        h2 = int(round(1920 / SQUEEZE / 2) * 2)
        off = int(((h2 - 1920) // 2) // 2 * 2)
        vf = f"scale=1080:{h2},crop=1080:1920:0:{off},ass={ass}"
    else:
        vf = f"ass={ass}"
    fc = f"[0:v]{vf}[v];" + audio
    subprocess.run(["ffmpeg", "-y", "-i", clip, "-stream_loop", "-1", "-i", music,
                    "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", out, "-loglevel", "error"],
                   check=True)
    return out


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def schedule_iso(order):
    """Время публикации для order-го ролика (ISO 8601 UTC)."""
    now = datetime.now(timezone.utc)
    if TEST_MINUTES > 0:
        t = now + timedelta(minutes=TEST_MINUTES * (order + 1))
    else:
        base = now.replace(hour=START_HOUR_UTC, minute=0, second=0, microsecond=0)
        if base <= now + timedelta(minutes=10):
            base += timedelta(days=1)
        t = base + timedelta(days=STEP_DAYS * order)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def platform_settings_for(title):
    """Настройки на сеть только для выбранных NETWORKS."""
    s = {}
    if "youtube" in NETWORKS:
        s["youtube"] = {"privacy": "public", "title": title[:100], "madeForKids": False}
    if "instagram" in NETWORKS:
        s["instagram"] = {"videoType": "REELS", "shareToFeed": True}
    if "tiktok" in NETWORKS:
        s["tiktok"] = {"viewerSetting": "PUBLIC_TO_EVERYONE", "allowComments": True}
    return s


def main():
    os.makedirs("work", exist_ok=True)
    resolve_project()
    clips = get_clips()
    if not clips:
        raise SystemExit(f"нет клипов у проекта {PROJECT_ID}")

    # Аккаунты Publora (кроме DRY_RUN — там ключ не нужен).
    plat_ids, missing = ([], NETWORKS)
    if not DRY_RUN:
        plat_ids, missing = publora.platform_ids(NETWORKS)
        if not plat_ids:
            tg.send_message(
                "Publora: не вижу подключённых аккаунтов "
                f"({', '.join(NETWORKS)}). Подключи их в кабинете Publora и "
                "проверь ключ PUBLORA_API_KEY.")
            raise SystemExit("нет подключённых аккаунтов Publora")
        if missing:
            tg.send_message("Publora: не подключены сети: " + ", ".join(missing) +
                            ". Выкладываю в остальные.")

    key = str(PROJECT_ID)
    state = load_state()
    done = set(str(x) for x in state.get(key, []))
    pending = [(i, c) for i, c in enumerate(clips, 1)
               if str(c.get("videoId")) not in done]
    batch = pending[:MAX_POSTS]
    if not batch:
        print("всё выложено — нечего публиковать")
        return

    results = []
    for order, (idx, c) in enumerate(batch):
        vzid = str(c.get("videoId"))
        try:
            clip = download(c["videoUrl"], f"work/pub_{idx}.mp4")
            words = rs.transcribe_words(clip)
            ass = rs.build_ass(words, f"work/pub_{idx}.ass")
            out = finish(clip, ass, f"work/fin_{idx}.mp4", MUSIC_FILE)
            vz_title = c.get("title") or ""
            title = clean_vz(vz_title)[:100].rstrip(" ,—–-") or "Осознание себя"
            desc = build_desc(vz_title)
            when = schedule_iso(order)

            if DRY_RUN:
                cap = (f"Готовый ролик {idx} для Publora.\n\n"
                       f"Сети: {', '.join(NETWORKS)}\nВремя (UTC): {when}\n\n"
                       f"Текст поста:\n{desc}")
                if os.path.getsize(out) / 1e6 <= 49:
                    tg.send_document(out, caption=cap)
                else:
                    tg.send_message(cap + "\n(файл велик для бота)")
                print(f"клип {idx}: DRY_RUN — файл отправлен")
                continue

            pg, _info = publora.schedule_video(
                desc, plat_ids, when, out, platform_settings_for(title))
            state[key] = sorted(set(state.get(key, [])) | {vzid})
            save_state(state)
            results.append((idx, title, when, pg, None))
            print(f"клип {idx}: Publora postGroupId={pg}, время={when}")
        except Exception as e:
            results.append((idx, clean_vz(c.get("title") or ""), None, None, str(e)))
            print(f"клип {idx}: ошибка — {e}")
        finally:
            for p in (f"work/pub_{idx}.mp4", f"work/fin_{idx}.mp4", f"work/pub_{idx}.ass"):
                try:
                    os.remove(p)
                except OSError:
                    pass

    if DRY_RUN:
        tg.send_message("Это готовые ролики (проверка перед Publora). "
                        "Скажи «ок» — включу автопостинг.")
        return

    left = len([c for c in clips
                if str(c.get("videoId")) not in set(str(x) for x in state.get(key, []))])
    ok = sum(1 for r in results if r[3] and not r[4])
    lines = [f"Publora — автопостинг в {', '.join(NETWORKS)}.",
             f"Запланировано: {ok}. Осталось клипов: {left}.", ""]
    for idx, title, when, pg, err in results:
        if pg and not err:
            lines.append(f"✅ {title}\n   на {when} UTC (все сети)")
        else:
            lines.append(f"❌ {title} — ошибка: {err}")
    tg.send_message("\n".join(lines))
    print(f"ГОТОВО: запланировано {ok}, осталось {left}")


if __name__ == "__main__":
    main()
