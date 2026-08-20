#!/usr/bin/env python3
# publora.py — тонкий клиент REST API Publora (https://api.publora.com).
# Publora сам публикует пост во ВСЕ подключённые сети (Instagram, TikTok,
# YouTube, Facebook и т.д.). Мы: узнаём подключённые аккаунты, создаём пост,
# загружаем в него видео (pre-signed S3), пост уходит по расписанию.
#
# Ключ — секрет PUBLORA_API_KEY (вид: sk_xxx....). Заголовок: x-publora-key.
# Док: https://docs.publora.com / github.com/publora/publora-api-docs

import os
import mimetypes

import requests

import config

BASE = "https://api.publora.com/api/v1"


def _key():
    return config.require_env("PUBLORA_API_KEY")


def _headers():
    return {"x-publora-key": _key(), "Content-Type": "application/json"}


def _check(r, what):
    if r.status_code >= 300:
        raise RuntimeError(f"Publora {what}: HTTP {r.status_code} — {r.text[:500]}")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Publora {what}: не-JSON ответ — {r.text[:300]}")
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"Publora {what}: {data}")
    return data


def list_connections():
    """Подключённые аккаунты: [{platformId, username, displayName}, ...].
    platformId имеет вид '<сеть>-<id>', например 'instagram-333'."""
    r = requests.get(f"{BASE}/platform-connections", headers=_headers(), timeout=60)
    data = _check(r, "platform-connections")
    # ответ может быть массивом или {connections: [...]}
    if isinstance(data, dict):
        return data.get("connections") or data.get("data") or []
    return data


def platform_ids(prefixes):
    """Вернуть platformId для нужных сетей по префиксу ('instagram','tiktok',
    'youtube'). Берём ПЕРВЫЙ аккаунт каждой сети. Возвращает (ids, missing)."""
    conns = list_connections()
    found = {}
    for c in conns:
        pid = str(c.get("platformId") or c.get("id") or "")
        net = pid.split("-", 1)[0].lower()
        found.setdefault(net, pid)
    ids, missing = [], []
    for p in prefixes:
        p = p.lower()
        if p in found:
            ids.append(found[p])
        else:
            missing.append(p)
    return ids, missing


def create_post(content, platform_ids_list, scheduled_iso=None, platform_settings=None):
    """Создать пост (черновик, если scheduled_iso=None). Возвращает postGroupId."""
    body = {"content": content, "platforms": platform_ids_list}
    if scheduled_iso:
        body["scheduledTime"] = scheduled_iso
    if platform_settings:
        body["platformSettings"] = platform_settings
    r = requests.post(f"{BASE}/create-post", headers=_headers(), json=body, timeout=120)
    data = _check(r, "create-post")
    pg = data.get("postGroupId")
    if not pg:
        raise RuntimeError(f"Publora create-post: нет postGroupId — {data}")
    return pg, data


def _upload_url(post_group_id, file_name, content_type):
    body = {
        "postGroupId": post_group_id,
        "fileName": file_name,
        "contentType": content_type,
        "type": "video" if content_type.startswith("video") else "image",
    }
    r = requests.post(f"{BASE}/get-upload-url", headers=_headers(), json=body, timeout=60)
    return _check(r, "get-upload-url")


def attach_video(post_group_id, video_path):
    """Загрузить видео в существующий пост (pre-signed S3 PUT).
    Возвращает mediaId/fileUrl из ответа get-upload-url."""
    content_type = mimetypes.guess_type(video_path)[0] or "video/mp4"
    file_name = os.path.basename(video_path).replace(" ", "_")
    up = _upload_url(post_group_id, file_name, content_type)
    put_url = up.get("uploadUrl")
    if not put_url:
        raise RuntimeError(f"Publora get-upload-url: нет uploadUrl — {up}")
    with open(video_path, "rb") as f:
        pr = requests.put(put_url, data=f, headers={"Content-Type": content_type},
                          timeout=600)
    if pr.status_code >= 300:
        raise RuntimeError(f"Publora S3 PUT: HTTP {pr.status_code} — {pr.text[:300]}")
    return up


def get_post(post_group_id):
    r = requests.get(f"{BASE}/get-post/{post_group_id}", headers=_headers(), timeout=60)
    return _check(r, "get-post")


def schedule_video(content, platform_ids_list, scheduled_iso, video_path,
                   platform_settings=None):
    """Полный цикл: создать пост + прикрепить видео. Возвращает (postGroupId, info)."""
    pg, created = create_post(content, platform_ids_list, scheduled_iso, platform_settings)
    up = attach_video(pg, video_path)
    return pg, {"created": created, "upload": up}


if __name__ == "__main__":
    # Проверка ключа и вывод подключённых аккаунтов.
    for c in list_connections():
        print(c.get("platformId"), "|", c.get("displayName") or c.get("username"))
