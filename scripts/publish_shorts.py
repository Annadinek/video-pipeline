#!/usr/bin/env python3
# publish_shorts.py — публикует ГОТОВЫЕ клипы (сужение + караоке-субтитры + музыка)
# в YouTube Shorts партиями по 6/сутки. Клипы берём из проекта Vizard в порядке
# videos (как в боте: клип 1 = videos[0]). Учёт выложенного — shorts_published.json
# (ключ = ID проекта). Каждый ролик грузим как Short: вертикаль + «#Shorts» в
# заголовке и описании, открытый доступ, добавляем в плейлист шортсов.
#
# Env: PROJECT_ID, VIZARDAI_API_KEY, YT_* , TELEGRAM_BOT_TOKEN,
#      YT_MAX_UPLOAD (6), SQUEEZE (0.5), MUSIC_FILE, MUSIC_GAIN, PRIVACY (public),
#      FONT_SIZE/MAX_WORDS/SUB_HL/SUB_OUTLINE — стиль субтитров (читает resub).

import importlib
import json
import os
import subprocess

import requests

import re

import config
import tg
import yt_ops
from vizard_to_youtube import check_status

rs = importlib.import_module("resub")  # transcribe_words + build_ass (стиль из env)

# Теги ролика (это НЕ хэштеги в тексте). Слова «shorts» здесь нет — Анна запретила.
TAGS = ["осознание", "подсознание", "психология", "смысл жизни"]
# Реально запрещённые слова (brain/FORBIDDEN.md на 17.08): матрица/энергия/
# пробуждение сняты с запрета — их НЕ трогаем (это слова Vizard/Анны).
FORBIDDEN_ROOTS = ["эзотерик", "вибрац", "саботаж", "квантов", "трансформац"]


def clean_vz(text):
    """Текст Vizard как есть, только: убрать хэштеги (в т.ч. любые #shorts),
    «(2)» от Vizard, реально запрещённые слова, лишние пробелы."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"\(\d+\)", "", t).strip()
    t = re.sub(r"#\S+", "", t).strip()               # никаких хэштегов, тем более #shorts
    words = [w for w in t.split()
             if not any(r in w.lower() for r in FORBIDDEN_ROOTS)]
    return " ".join(words).strip(" —–-,")


def build_desc(vz_title):
    """Подпись = описание Vizard (его заголовок), сокращённое. Ведём в бот.
    Слова «shorts» нет нигде."""
    body = clean_vz(vz_title)
    if len(body) > 200:
        body = body[:200].rsplit(" ", 1)[0].rstrip(" ,—–-") + "…"
    return f"{body}\n\nЗапись на консультацию — Telegram @dinekanna_bot"

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ["PROJECT_ID"].strip()
MAX_UPLOAD = int(os.environ.get("YT_MAX_UPLOAD", "6"))
SQUEEZE = float(os.environ.get("SQUEEZE", "0.5") or "0")
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music/anna_piano.mp3")
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.12")
PRIVACY = os.environ.get("PRIVACY", "public")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"   # 1 = образец в бот, без выкладки
STATE_FILE = "shorts_published.json"
PREVIEW = "assets/vzfinish"


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
    """Тот же финиш, что в vizard_finish (PLAIN): сужение по высоте (SQUEEZE) +
    караоке-субтитры + фоновая музыка. Без размытия, без чёрных полос."""
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


def main():
    os.makedirs("work", exist_ok=True)
    os.makedirs(PREVIEW, exist_ok=True)
    clips = get_clips()
    if not clips:
        raise SystemExit(f"нет клипов у проекта {PROJECT_ID}")
    key = str(PROJECT_ID)
    state = load_state()
    done = set(str(x) for x in state.get(key, []))
    # порядок videos (1-based индекс = номер клипа в боте)
    pending = [(i, c) for i, c in enumerate(clips, 1)
               if str(c.get("videoId")) not in done]
    batch = pending[:MAX_UPLOAD]
    if not batch:
        print("всё выложено — нечего публиковать")
        return

    results = []
    for idx, c in batch:
        vzid = str(c.get("videoId"))
        try:
            clip = download(c["videoUrl"], f"work/pub_{idx}.mp4")
            words = rs.transcribe_words(clip)
            ass = rs.build_ass(words, f"work/pub_{idx}.ass")
            out = finish(clip, ass, f"work/fin_{idx}.mp4", MUSIC_FILE)
            subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", out, "-frames:v", "1",
                            "-q:v", "2", os.path.join(PREVIEW, f"pub_{idx}.jpg"),
                            "-loglevel", "error"], check=False)
            vz_title = c.get("title") or ""
            title = clean_vz(vz_title)[:100].rstrip(" ,—–-") or "Осознание себя"
            desc = build_desc(vz_title)
            # Показать Анне ТОТ САМЫЙ ролик, что пойдёт в выкладку — файлом
            # (документом), чтобы Telegram не пережимал и не «размазывал».
            if DRY_RUN:
                cap = f"Ролик {idx} на выкладку\n\nЗаголовок: {title}\n\n{desc}"
                size = os.path.getsize(out) / 1e6
                if size <= 49:
                    tg.send_document(out, caption=cap)
                else:
                    tg.send_message(cap + f"\n(файл {size:.0f} МБ, велик для бота)")
                print(f"клип {idx}: отправлен файлом (полное качество)")
                continue
            # Заголовок БЕЗ слова «shorts». Ролик — вертикаль 30–90 сек, YouTube сам
            # классифицирует его как Short по формату кадра и длине.
            vid = yt_ops.upload_video(out, title, desc,
                                      privacy=PRIVACY, tags=TAGS)
            try:
                yt_ops.add_to_playlist(vid, config.SHORTS_PLAYLIST_ID)
            except Exception as e:
                print(f"клип {idx}: в плейлист не добавился ({e})")
            status, reason = check_status(vid)
            link = f"https://youtube.com/shorts/{vid}"
            results.append((idx, title, link, reason))
            state[key] = sorted(set(state.get(key, [])) | {vzid})
            save_state(state)
            print(f"клип {idx}: {link} | статус={status} | отклонён={reason}")
        except Exception as e:
            results.append((idx, clean_vz(c.get("title") or ""), None, str(e)))
            print(f"клип {idx}: ошибка — {e}")
        finally:
            for p in (f"work/pub_{idx}.mp4", f"work/fin_{idx}.mp4"):
                try:
                    os.remove(p)
                except OSError:
                    pass

    if DRY_RUN:
        tg.send_message("Это образцы после переделки (пока НЕ выложены). "
                        "Скажи «ок» — и завтра в 10:00 уйдут в Shorts по 6/сутки.")
        print("ГОТОВО: образцы отправлены (без выкладки)")
        return

    left = len([c for c in clips
                if str(c.get("videoId")) not in set(str(x) for x in state.get(key, []))])
    ok = sum(1 for r in results if r[2] and r[3] in (None, ""))
    lines = [f"YouTube Shorts (открытый доступ): выложил {ok}. Осталось {left}.", ""]
    for idx, title, link, reason in results:
        if link and reason in (None, ""):
            lines.append(f"✅ {title}\n{link}")
        elif link:
            lines.append(f"⚠️ {title}\n{link}\nYouTube пометил: {reason}")
        else:
            lines.append(f"❌ {title} — не загрузился ({reason})")
    if left:
        lines.append(f"\nОстальные {left} — завтра в 10:00 (по 6/сутки).")
    tg.send_message("\n".join(lines))
    print(f"ГОТОВО: выложено {ok}, осталось {left}")


if __name__ == "__main__":
    main()
