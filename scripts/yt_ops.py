#!/usr/bin/env python3
# yt_ops.py — действия на YouTube от имени Анны: загрузка видео, субтитры,
# обложка, публикация, плейлисты, удаление. Все квоты-дорогие вызовы здесь.

import yt_auth


def _media(path, mimetype, resumable=True):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(path, mimetype=mimetype, resumable=resumable)


def upload_video(path, title, description, privacy="unlisted", tags=None):
    """Загрузить видео. По умолчанию unlisted (черновик для проверки).
    Возвращает video_id. Стоит ~1600 единиц квоты."""
    yt = yt_auth.get_service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "22",  # People & Blogs
            "defaultLanguage": "ru",
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(
        part="snippet,status", body=body, media_body=_media(path, "video/*")
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def update_snippet(video_id, title, description, tags=None, category_id="22", lang="ru"):
    """Обновить заголовок/описание/теги видео (~50 единиц). Для snippet-update
    YouTube требует categoryId в теле."""
    yt = yt_auth.get_service()
    body = {
        "id": video_id,
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": category_id,
            "tags": tags or [],
            "defaultLanguage": lang,
        },
    }
    return yt.videos().update(part="snippet", body=body).execute()


def set_privacy(video_id, privacy="public"):
    """Сменить статус видео (unlisted -> public). Дёшево (~50 единиц)."""
    yt = yt_auth.get_service()
    return yt.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy}},
    ).execute()


def insert_caption(video_id, srt_path, language="ru", name="Русские субтитры"):
    """Загрузить субтитры .srt к видео (~400 единиц)."""
    yt = yt_auth.get_service()
    body = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
    return yt.captions().insert(
        part="snippet", body=body, media_body=_media(srt_path, "application/octet-stream")
    ).execute()


def set_thumbnail(video_id, image_path):
    """Поставить обложку (требует телефон-верификации канала; ~50 единиц)."""
    yt = yt_auth.get_service()
    return yt.thumbnails().set(
        videoId=video_id, media_body=_media(image_path, "image/jpeg", resumable=False)
    ).execute()


def add_to_playlist(video_id, playlist_id):
    yt = yt_auth.get_service()
    return yt.playlistItems().insert(
        part="snippet",
        body={"snippet": {"playlistId": playlist_id,
                          "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
    ).execute()


def delete_video(video_id):
    yt = yt_auth.get_service()
    return yt.videos().delete(id=video_id).execute()
