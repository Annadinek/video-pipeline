#!/usr/bin/env python3
"""
sync_brain.py — тянет папку `expert-brain` с Google Диска в brain/.

Защита:
- Файл на Диске усох больше чем вдвое — НЕ синхронизирую, шлю предупреждение.
- Файл пропал с Диска — в репозитории ОСТАВЛЯЮ, шлю предупреждение.
- Файлы из репозитория не удаляю никогда. Только скачиваю и предупреждаю.

Доступ к Диску — через секрет GDRIVE_CREDENTIALS. Его пока нет — Анна добавит.
Нет секрета — корректно выходим с понятным сообщением.
"""

import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN_DIR = os.path.join(ROOT, "brain")
LOG_DIR = os.path.join(ROOT, "logs")
ERRORS_LOG = os.path.join(LOG_DIR, "errors.log")

SHRINK_LIMIT = 0.5


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_error(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(ERRORS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{_stamp()}] sync_brain: {msg}\n")


def alert(msg):
    """Предупреждение Анне (в Telegram, если есть токен) и в журнал."""
    print("ВНИМАНИЕ:", msg)
    log_error("ALERT: " + msg)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        import requests
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "281187873")
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "sync_brain: " + msg},
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        log_error(f"не смог отправить alert в Telegram: {e}")


def parse_drive_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_time(path):
    return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)


def download(local_path, drive_file):
    """Скачать файл с Диска в local_path. Подключается к реальному Drive API."""
    _drive_download(drive_file["id"], local_path)


def sync_one(local_path, drive_file):
    if not os.path.exists(local_path):
        download(local_path, drive_file)
        return "новый файл скачан"
    local_size = os.path.getsize(local_path)
    drive_size = int(drive_file.get("size", 0))
    drive_newer = parse_drive_time(drive_file["modifiedTime"]) > local_time(local_path)
    if drive_newer and local_size > 0 and drive_size < local_size * SHRINK_LIMIT:
        percent = round(drive_size / local_size * 100)
        alert(f"Файл {os.path.basename(local_path)} на Диске усох до {percent}%. Не синхронизирую.")
        return "остановлено"
    if drive_newer:
        download(local_path, drive_file)
        return "скачан с Диска"
    return "без изменений"


def sync_all(local_dir, drive_files):
    drive_names = {f["title"] for f in drive_files}
    for f in drive_files:
        result = sync_one(os.path.join(local_dir, f["title"]), f)
        print(f"{f['title']}: {result}")
    for name in os.listdir(local_dir):
        if name.endswith(".md") and name not in drive_names:
            alert(f"Файл {name} пропал с Диска. В репозитории оставляю.")


# --- подключение к Google Диску ---
def _get_drive_service():
    """Создать клиент Google Drive из секрета GDRIVE_CREDENTIALS (JSON сервис-аккаунта)."""
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_json = os.environ["GDRIVE_CREDENTIALS"]
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)


def _list_brain_files(service):
    """Найти папку expert-brain и вернуть список файлов в формате sync_all."""
    folders = service.files().list(
        q="name = 'expert-brain' and mimeType = 'application/vnd.google-apps.folder'",
        fields="files(id, name)",
    ).execute().get("files", [])
    if not folders:
        raise RuntimeError("папка expert-brain не найдена на Диске")
    folder_id = folders[0]["id"]
    items = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id, name, size, modifiedTime)",
    ).execute().get("files", [])
    return [
        {
            "id": it["id"],
            "title": it["name"],
            "size": it.get("size", 0),
            "modifiedTime": it["modifiedTime"],
        }
        for it in items
    ]


def _drive_download(file_id, local_path):
    from googleapiclient.http import MediaIoBaseDownload

    service = _get_drive_service()
    request = service.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def main():
    if not os.environ.get("GDRIVE_CREDENTIALS"):
        msg = "секрета GDRIVE_CREDENTIALS нет — синхронизация мозга пропущена. Анна добавит секрет позже."
        print(msg)
        log_error(msg)
        sys.exit(0)

    os.makedirs(BRAIN_DIR, exist_ok=True)
    try:
        service = _get_drive_service()
        drive_files = _list_brain_files(service)
    except Exception as e:  # noqa: BLE001
        log_error(f"не смог получить список файлов с Диска: {e}")
        print(f"Ошибка доступа к Диску: {e}", file=sys.stderr)
        sys.exit(1)

    sync_all(BRAIN_DIR, drive_files)
    print("Синхронизация мозга завершена.")


if __name__ == "__main__":
    main()
