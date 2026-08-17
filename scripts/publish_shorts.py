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

import config
import tg
import yt_ops
from vizard_to_youtube import TAGS, check_status, clean_title

rs = importlib.import_module("resub")  # transcribe_words + build_ass (стиль из env)

# Подпись под Shorts. БЕЗ слова «shorts» (Анна попросила убрать). Ведём в бот.
HASHTAGS = "#осознаниесебя #подсознание #психология #тревожность #АннаДинэк"


def build_desc(title):
    return (f"{title}\n\n"
            "Полное видео и запись на консультацию — Telegram @dinekanna_bot\n\n"
            f"{HASHTAGS}")

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ["PROJECT_ID"].strip()
MAX_UPLOAD = int(os.environ.get("YT_MAX_UPLOAD", "6"))
SQUEEZE = float(os.environ.get("SQUEEZE", "0.5") or "0")
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music/anna_piano.mp3")
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.12")
PRIVACY = os.environ.get("PRIVACY", "public")
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
            title = clean_title(c.get("title"))
            desc = build_desc(title)
            # Заголовок БЕЗ слова «shorts». Ролик — вертикаль ≤3 мин, YouTube сам
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
            results.append((idx, clean_title(c.get("title")), None, str(e)))
            print(f"клип {idx}: ошибка — {e}")
        finally:
            for p in (f"work/pub_{idx}.mp4", f"work/fin_{idx}.mp4"):
                try:
                    os.remove(p)
                except OSError:
                    pass

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
