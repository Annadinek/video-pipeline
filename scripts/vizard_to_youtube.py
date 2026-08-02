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

import os
import re
import sys

import config
import tg
import vizard_clip as vz
import yt_ops

# Сколько роликов выложить за раз (квота YouTube ~10000/сутки, загрузка ~1600).
MAX_UPLOAD = int(os.environ.get("YT_MAX_UPLOAD", "6"))
SHORTS_PLAYLIST_ID = config.SHORTS_PLAYLIST_ID  # «Клауд шортсы рилсы»

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
    clips = clips[:MAX_UPLOAD]
    print(f"Vizard: беру {len(clips)} лучших клипов для YouTube Shorts")

    os.makedirs("shorts", exist_ok=True)
    results = []
    for i, c in enumerate(clips, 1):
        title = clean_title(c.get("title"))
        desc = description_for(title)
        url = c.get("videoUrl")
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
            print(f"клип {i}: {link} | статус={status} | отклонён={reason}")
        except Exception as e:
            results.append((i, title, None, "ошибка", str(e)))
            print(f"клип {i}: ошибка загрузки — {e}")

    ok = [r for r in results if r[2] and r[4] in (None, "")]
    blocked = [r for r in results if r[4] not in (None, "") and r[2]]
    failed = [r for r in results if not r[2]]

    lines = [f"Выложил в YouTube Shorts (открытый доступ): {len(ok)} из {len(results)}.", ""]
    for i, title, link, status, reason in results:
        if link and reason in (None, ""):
            lines.append(f"✅ {title}\n{link}")
        elif link:
            lines.append(f"⚠️ {title}\n{link}\nYouTube пометил: {reason}")
        else:
            lines.append(f"❌ {title} — не загрузился ({reason})")
    if blocked:
        lines.append("\nОтмеченные ⚠️ — проверь в YouTube Studio, возможно ограничение.")
    tg.send_message("\n".join(lines))
    print(f"ГОТОВО: выложено {len(ok)}, с пометкой {len(blocked)}, ошибок {len(failed)}.")


if __name__ == "__main__":
    main()
