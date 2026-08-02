#!/usr/bin/env python3
# vizard_to_youtube.py — берёт клипы из Vizard и выкладывает их в YouTube Shorts
# на канал Анны с ОТКРЫТЫМ доступом (public). Каждый шортс подписывается по
# правилам голоса Анны (без запрещённых слов), добавляется в плейлист шортсов.
# После загрузки проверяем, что YouTube не отклонил ролик (копирайт/блок).
#
# Клипы Vizard — вертикальные и короткие, YouTube сам делает из них Shorts.
# Каждая загрузка стоит ~1600 единиц квоты: за сутки помещается ~6 роликов.
#
# Запуск: scripts/vizard_to_youtube.py <videoUrl или ID YouTube>

import json
import os
import re
import sys

import config
import tg
import vizard_clip as vz
import yt_ops

# Сколько роликов выложить за раз. YouTube даёт ~6 загрузок в сутки (квота 10000,
# videos.insert = 1600). Поэтому все клипы выкладываем НЕ за раз, а по партиям —
# каждый день следующая партия, пока не выложены ВСЕ (учёт в STATE_FILE).
MAX_UPLOAD = int(os.environ.get("YT_MAX_UPLOAD", "6"))
SHORTS_PLAYLIST_ID = config.SHORTS_PLAYLIST_ID  # «Клауд шортсы рилсы»
STATE_FILE = "shorts_published.json"            # {projectId: [vizard videoId...]}
# Режим пометки: записать топ-N в state как уже выложенные, БЕЗ загрузки
# (нужен один раз, чтобы учесть клипы, выложенные до появления учёта).
INIT_MARK = os.environ.get("INIT_MARK", "0") == "1"


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

# Запрещённые слова из CLAUDE.md (ЧАСТЬ 10) — по корням, чтобы ловить формы.
FORBIDDEN_ROOTS = ["матриц", "эзотерик", "вибрац", "энерги", "пробужден",
                   "саботаж", "квантов", "трансформац"]
FORBIDDEN_WHOLE = {"ноль"}

TAGS = ["shorts", "свобода", "осознание", "регрессология"]


def has_forbidden(text):
    low = text.lower()
    if any(r in low for r in FORBIDDEN_ROOTS):
        return True
    return any(w in low.split() for w in FORBIDDEN_WHOLE)


def clean_title(vizard_title):
    """Заголовок шортса без запрещённых слов. Заголовок Vizard часто состоит из
    двух фраз («Вопрос? Пояснение»). Берём первую фразу БЕЗ запрещённых слов;
    если все с запрещёнными — вычищаем такие слова из первой фразы."""
    t = re.sub(r"\s+", " ", vizard_title or "").strip()
    t = re.sub(r"\(\d+\)", "", t).strip()          # убрать «(2)» от Vizard
    parts = [p.strip() for p in re.split(r"(?<=[?.!])\s", t) if p.strip()]
    for p in parts:                                 # первая чистая фраза целиком
        if len(p) >= 8 and not has_forbidden(p):
            return p[:88].rstrip(" ,—–-")
    cand = parts[0] if parts else t                 # чистых нет — режем слова
    words = [w for w in cand.split()
             if not (any(r in w.lower() for r in FORBIDDEN_ROOTS)
                     or w.lower().strip(".,!?") in FORBIDDEN_WHOLE)]
    cand = " ".join(words).strip(" —–-,")
    if len(cand) < 8:
        cand = "Что такое свобода"
    return cand[:88].rstrip(" ,—–-")


def description_for(title):
    return (f"{title}\n\n"
            "Полное видео и запись на консультацию — телеграм @dinekanna_bot "
            "или мой телеграм @annadinek\n\n"
            "#shorts #свобода #осознание #регрессология #выходтишины")


def check_status(video_id):
    """Статус загрузки: не отклонил ли YouTube (копирайт/блок)."""
    yt = yt_auth_service()
    r = yt.videos().list(part="status", id=video_id).execute()
    items = r.get("items")
    if not items:
        return "unknown", None
    st = items[0]["status"]
    return st.get("uploadStatus"), st.get("rejectionReason")


def yt_auth_service():
    import yt_auth
    return yt_auth.get_service()


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        arg = sys.argv[1].strip()
    else:
        arg = os.environ.get("VIDEO_URL", "").strip()
    if not arg:
        raise SystemExit("Не задана ссылка на видео (аргумент или VIDEO_URL).")
    video_url = arg if "://" in arg else f"https://www.youtube.com/watch?v={arg}"

    api_key = vz.get_api_key()
    pid = os.environ.get("VIZARD_PROJECT_ID", "").strip()
    if pid:
        project_id = int(pid)
        print(f"Vizard: беру готовый проект {project_id} (без новой обработки)")
    else:
        print(f"Vizard: отправляю на нарезку {video_url}")
        project_id = vz.create_project(api_key, video_url)
        print(f"Vizard: проект {project_id}, жду клипы...")
    clips = vz.wait_for_clips(api_key, project_id)

    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)

    key = str(project_id)
    state = load_state()
    done = set(str(x) for x in state.get(key, []))
    pending = [c for c in clips if str(c.get("videoId")) not in done]
    print(f"Всего клипов {len(clips)}, уже выложено {len(done)}, осталось {len(pending)}")

    # Режим пометки: считаем следующие MAX_UPLOAD уже выложенными (учёт), не грузим.
    if INIT_MARK:
        mark = [str(c.get("videoId")) for c in pending[:MAX_UPLOAD]]
        state[key] = sorted(set(state.get(key, [])) | set(mark))
        save_state(state)
        tg.send_message(f"Учёт: отметил {len(mark)} уже выложенных клипов. "
                        f"Осталось выложить {len(pending) - len(mark)}.")
        print(f"INIT_MARK: помечено {len(mark)}")
        return

    batch = pending[:MAX_UPLOAD]
    if not batch:
        tg.send_message("Все клипы этого видео уже выложены в YouTube Shorts. Готово.")
        print("нечего выкладывать — всё выложено")
        return

    os.makedirs("shorts", exist_ok=True)
    results = []
    for i, c in enumerate(batch, 1):
        title = clean_title(c.get("title"))
        desc = description_for(title)
        url = c.get("videoUrl")
        vzid = str(c.get("videoId"))
        path = os.path.join("shorts", f"yt_{i}.mp4")
        try:
            vz.download(url, path)
            vid = yt_ops.upload_video(path, f"{title} #Shorts", desc,
                                      privacy="public", tags=TAGS)
            try:
                yt_ops.add_to_playlist(vid, SHORTS_PLAYLIST_ID)
            except Exception as e:
                print(f"клип {i}: в плейлист не добавился ({e})")
            status, reason = check_status(vid)
            link = f"https://youtube.com/shorts/{vid}"
            results.append((i, title, link, status, reason))
            # помечаем выложенным СРАЗУ и сохраняем — если дальше упадём по квоте,
            # уже выложенные не задвоятся в следующий раз.
            state[key] = sorted(set(state.get(key, [])) | {vzid})
            save_state(state)
            print(f"клип {i}: {link} | статус={status} | отклонён={reason}")
        except Exception as e:
            results.append((i, title, None, "ошибка", str(e)))
            print(f"клип {i}: ошибка загрузки — {e}")

    left = len([c for c in clips if str(c.get("videoId")) not in set(str(x) for x in state.get(key, []))])
    ok = [r for r in results if r[2] and r[4] in (None, "")]
    blocked = [r for r in results if r[4] not in (None, "") and r[2]]

    lines = [f"Выложил в YouTube Shorts (открытый доступ): {len(ok)}. Осталось {left}.", ""]
    for i, title, link, status, reason in results:
        if link and reason in (None, ""):
            lines.append(f"✅ {title}\n{link}")
        elif link:
            lines.append(f"⚠️ {title}\n{link}\nYouTube пометил: {reason}")
        else:
            lines.append(f"❌ {title} — не загрузился ({reason})")
    if left:
        lines.append(f"\nОстальные {left} выложу завтра (лимит YouTube ~6 в сутки).")
    if blocked:
        lines.append("Отмеченные ⚠️ — проверь в YouTube Studio, возможно ограничение.")
    tg.send_message("\n".join(lines))
    print(f"ГОТОВО: выложено {len(ok)}, осталось {left}.")


if __name__ == "__main__":
    main()
