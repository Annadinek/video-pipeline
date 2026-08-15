#!/usr/bin/env python3
# publish_socials.py — берёт ТЕ ЖЕ клипы Vizard, что и YouTube-шортсы, и публикует
# их в Instagram Reels и TikTok — через сам Vizard (эндпоинт project/publish-video).
# Это «сестра» vizard_to_youtube.py: тот же проект Vizard, те же вертикальные
# нарезки 9:16, те же подписи голосом Анны. YouTube грузим отдельным скриптом
# (нужны свои квоты/обложки), соцсети — тут, чтобы не заводить Meta/TikTok API.
#
# Как это работает (по скиллу .claude/skills/using-vizard-api/):
#   1) GET  /project/social-accounts — какие аккаунты Анна подключила в Vizard;
#   2) POST /project/publish-video   — публикуем клип на выбранный аккаунт
#      (Instagram / TikTok), подпись — её текстом или пустая (Vizard сам напишет).
# Скачивать/резать/ffmpeg тут НЕ нужно: клип уже лежит у Vizard, публикует он сам.
# Значит и Google-библиотеки не нужны — только requests.
#
# ВАЖНО (правило Анны 22): публикуем ТОЛЬКО после её «ок» по роликам. Поэтому
# скрипт запускается кнопкой (workflow_dispatch), а не сам по расписанию.
#
# Подключение аккаунтов — разовое действие Анны в интерфейсе Vizard
# (Settings → Social Accounts → подключить Instagram и TikTok). Скрипт их не
# создаёт: если аккаунт не подключён, честно пишет об этом и площадку пропускает.
#
# Запуск: scripts/publish_socials.py            (берёт готовый проект VIZARD_PROJECT_ID)
#         scripts/publish_socials.py <videoUrl> (создаст новую нарезку — платно)

import json
import os
import re
import sys

import requests

import config
import tg
import vizard_clip as vz

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"

# Куда публикуем. По умолчанию Instagram + TikTok (YouTube — отдельным скриптом).
PLATFORMS = [p.strip().lower()
             for p in os.environ.get("SOCIAL_PLATFORMS", "instagram,tiktok").split(",")
             if p.strip()]

# Сколько клипов публиковать за один запуск на КАЖДУЮ площадку. Держим скромно:
# соцсети не любят пачку постов сразу. Остальные — следующим запуском (учёт ниже).
SOCIAL_MAX = int(os.environ.get("SOCIAL_MAX", "3"))

# Готовые подписи Анны по номеру клипа (тот же файл, что у YouTube).
CAPTIONS_FILE = os.environ.get("CAPTIONS_FILE", "").strip()

# Пустая подпись = Vizard сам сгенерирует текст и хэштеги. Включается флагом.
AI_CAPTION = os.environ.get("AI_CAPTION", "0") == "1"

# Режим пометки: считать N клипов уже выложенными (учёт), без реальной публикации.
INIT_MARK = os.environ.get("INIT_MARK", "0") == "1"

# Учёт выложенного, чтобы не дублировать: {projectId: {platform: [videoId, ...]}}
STATE_FILE = os.environ.get("SOCIALS_STATE_FILE", "socials_published.json")

# Предел длины подписи у Instagram и TikTok — 2200 символов (из api-reference).
CAPTION_LIMIT = 2200

# Хэштеги под площадку (чисто, без запрещённых слов). Контакт — общий.
HASHTAGS = {
    "instagram": "#reels #рилс #свобода #осознание #регрессология",
    "tiktok": "#tiktok #свобода #осознание #регрессология",
}
CONTACT = ("Полное видео и запись на консультацию — телеграм @dinekanna_bot "
           "или мой телеграм @annadinek")

# Запрещённые слова (как в vizard_to_youtube.py) — по корням, чтобы ловить формы.
FORBIDDEN_ROOTS = ["матриц", "эзотерик", "вибрац", "энерги", "пробужден",
                   "саботаж", "квантов", "трансформац"]
FORBIDDEN_WHOLE = {"ноль"}


def has_forbidden(text):
    low = (text or "").lower()
    if any(r in low for r in FORBIDDEN_ROOTS):
        return True
    return any(w in low.split() for w in FORBIDDEN_WHOLE)


def clean_title(vizard_title):
    """Заголовок без запрещённых слов (та же логика, что у YouTube-скрипта)."""
    t = re.sub(r"\s+", " ", vizard_title or "").strip()
    t = re.sub(r"\(\d+\)", "", t).strip()
    parts = [p.strip() for p in re.split(r"(?<=[?.!])\s", t) if p.strip()]
    for p in parts:
        if len(p) >= 8 and not has_forbidden(p):
            return p[:88].rstrip(" ,—–-")
    cand = parts[0] if parts else t
    words = [w for w in cand.split()
             if not (any(r in w.lower() for r in FORBIDDEN_ROOTS)
                     or w.lower().strip(".,!?") in FORBIDDEN_WHOLE)]
    cand = " ".join(words).strip(" —–-,")
    if len(cand) < 8:
        cand = "Что такое свобода"
    return cand[:88].rstrip(" ,—–-")


def load_captions():
    if CAPTIONS_FILE and os.path.exists(CAPTIONS_FILE):
        try:
            return json.load(open(CAPTIONS_FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def build_post(caption, vizard_title, platform):
    """Текст поста для соцсети. Если есть готовая подпись Анны («№1. Заголовок\\n\\n
    текст…») — берём её; иначе чистый заголовок из Vizard. Плюс контакт и хэштеги
    площадки. Обрезаем до лимита 2200. AI_CAPTION=1 → пустая строка (Vizard сам)."""
    if AI_CAPTION:
        return ""
    tags = HASHTAGS.get(platform, "")
    if caption:
        blocks = [p.strip() for p in caption.split("\n\n") if p.strip()]
        head = re.sub(r"^№\s*\d+\.\s*", "", blocks[0]).strip() if blocks else ""
        body = "\n\n".join(blocks[1:]).strip()
        top = (head + "\n\n" + body).strip() if body else head
    else:
        top = clean_title(vizard_title)
    post = f"{top}\n\n{CONTACT}\n\n{tags}".strip()
    if len(post) > CAPTION_LIMIT:
        post = post[:CAPTION_LIMIT].rstrip()
    return post


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


def get_social_accounts(api_key):
    """Список подключённых в Vizard аккаунтов: [{id, platform, username, status}]."""
    r = requests.get(f"{API}/project/social-accounts",
                     headers={"VIZARDAI_API_KEY": api_key}, timeout=60)
    data = r.json()
    if data.get("code") not in (2000, None) and "accounts" not in data:
        raise RuntimeError(f"Vizard social-accounts: {data}")
    return data.get("accounts", []) or []


def publish_video(api_key, final_video_id, social_account_id, post):
    """Опубликовать один клип на один соцаккаунт. Возвращает (ok, message)."""
    body = {
        "finalVideoId": int(final_video_id),
        "socialAccountId": str(social_account_id),
        "post": post,  # "" = Vizard сам напишет подпись
    }
    r = requests.post(f"{API}/project/publish-video",
                      headers={"Content-Type": "application/json",
                               "VIZARDAI_API_KEY": api_key},
                      json=body, timeout=120)
    data = r.json()
    code = data.get("code")
    if code == 2000:
        return True, "опубликовано"
    messages = {
        4004: "нужен апгрейд тарифа Vizard (публикация в соцсети платная)",
        4011: "неверный аккаунт соцсети — переподключи его в Vizard",
        4007: "кончилось время/кредиты Vizard",
    }
    return False, messages.get(code, f"Vizard вернул {data}")


def accounts_for_platform(accounts, platform):
    """Активные аккаунты нужной площадки (Instagram/TikTok), сравнение без регистра."""
    out = []
    for a in accounts:
        if (a.get("platform", "").lower() == platform
                and a.get("status", "active").lower() in ("active", "")):
            out.append(a)
    return out


def resolve_project_id(api_key):
    """Какой проект Vizard публикуем. Приоритет — готовый VIZARD_PROJECT_ID (те же
    клипы, что ушли на YouTube; ничего заново не режем и не платим). Иначе — ссылка
    из аргумента/VIDEO_URL, тогда создаём новую нарезку (платно)."""
    pid = os.environ.get("VIZARD_PROJECT_ID", "").strip()
    if pid:
        print(f"Vizard: беру готовый проект {pid} (те же клипы, без новой нарезки)")
        return int(pid)
    arg = (sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip()
           else os.environ.get("VIDEO_URL", "").strip())
    if not arg:
        raise SystemExit("Не задан ни VIZARD_PROJECT_ID, ни ссылка на видео "
                         "(аргумент/VIDEO_URL). Для соцсетей обычно берём тот же "
                         "проект Vizard, что и для YouTube-шортсов.")
    video_url = arg if "://" in arg else f"https://www.youtube.com/watch?v={arg}"
    print(f"Vizard: отправляю на нарезку {video_url}")
    return vz.create_project(api_key, video_url)


def main():
    api_key = config.require_env("VIZARDAI_API_KEY")

    if not PLATFORMS:
        raise SystemExit("Пустой SOCIAL_PLATFORMS — нечего публиковать.")

    project_id = resolve_project_id(api_key)
    clips = vz.wait_for_clips(api_key, project_id)

    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)

    rank_by_id = {str(c.get("videoId")): i + 1 for i, c in enumerate(clips)}
    captions = load_captions()

    # Какие аккаунты подключены в Vizard.
    accounts = get_social_accounts(api_key)
    key = str(project_id)
    state = load_state()
    proj_state = state.setdefault(key, {})

    summary = [f"Vizard-проект {project_id}: клипов {len(clips)}."]
    any_published = False

    for platform in PLATFORMS:
        plat_accounts = accounts_for_platform(accounts, platform)
        done = set(str(x) for x in proj_state.get(platform, []))
        pending = [c for c in clips if str(c.get("videoId")) not in done]

        if not plat_accounts:
            summary.append(
                f"\n{platform.capitalize()}: нет подключённого аккаунта. "
                f"Подключи его в Vizard (Settings → Social Accounts) — тогда выложу.")
            print(f"{platform}: аккаунт не подключён, пропускаю")
            continue

        # Режим учёта: помечаем следующие N как выложенные, без реальной публикации.
        if INIT_MARK:
            mark = [str(c.get("videoId")) for c in pending[:SOCIAL_MAX]]
            proj_state[platform] = sorted(set(proj_state.get(platform, [])) | set(mark))
            save_state(state)
            summary.append(f"\n{platform.capitalize()}: отмечено {len(mark)} "
                           f"как выложенные (учёт). Осталось {len(pending) - len(mark)}.")
            continue

        batch = pending[:SOCIAL_MAX]
        if not batch:
            summary.append(f"\n{platform.capitalize()}: всё уже выложено.")
            print(f"{platform}: нечего выкладывать")
            continue

        acc = plat_accounts[0]  # обычно один аккаунт на площадку
        results = []
        for c in batch:
            vzid = str(c.get("videoId"))
            rank = rank_by_id.get(vzid)
            post = build_post(captions.get(str(rank)), c.get("title"), platform)
            try:
                ok, msg = publish_video(api_key, vzid, acc["id"], post)
            except Exception as e:  # сеть/JSON — не роняем весь запуск
                ok, msg = False, str(e)
            title = clean_title(c.get("title"))
            results.append((title, ok, msg))
            if ok:
                any_published = True
                # помечаем СРАЗУ и сохраняем — чтобы при обрыве не задвоить
                proj_state[platform] = sorted(set(proj_state.get(platform, [])) | {vzid})
                save_state(state)
            print(f"{platform} | {title} | {'ok' if ok else 'ошибка'}: {msg}")

        left = len([c for c in clips
                    if str(c.get("videoId")) not in set(str(x) for x in proj_state.get(platform, []))])
        ok_n = sum(1 for _, ok, _ in results if ok)
        summary.append(
            f"\n{platform.capitalize()} (@{acc.get('username', '?')}): выложил {ok_n} "
            f"из {len(results)}. Осталось {left}.")
        for title, ok, msg in results:
            summary.append(("✅ " if ok else "❌ ") + f"{title}" + ("" if ok else f" — {msg}"))
        if left:
            summary.append(f"Остальные {left} — следующим запуском.")

    tg.send_message("\n".join(summary))
    print("ГОТОВО." if any_published else "Ничего не опубликовано (см. отчёт выше).")


if __name__ == "__main__":
    main()
