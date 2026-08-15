#!/usr/bin/env python3
# add_music.py — берёт аудио, которое Анна залила в бот (её пиано), и накладывает
# его ФОНОМ под голос на субтитрованное видео. Готовое заливает unlisted и шлёт
# ссылку в бот. Музыка — тихо, голос Анны остаётся главным.
#
# Видео берём уже с субтитрами (по ссылке VIDEO_URL), чтобы не терять качество
# картинки и не переделывать субтитры.

import os
import subprocess
import time

import tg
import yt_ops


def get_updates_retry(tries=6, wait=6):
    """getUpdates с повтором на 409 Conflict (когда бота дёргает кто-то ещё)."""
    last = None
    for i in range(tries):
        try:
            return tg.get_updates()
        except Exception as e:
            last = e
            if "409" in str(e):
                print(f"getUpdates 409, повтор {i + 1}/{tries} через {wait}с...")
                time.sleep(wait)
                continue
            raise
    raise last if last is not None else RuntimeError("getUpdates не удался")

WORK = "work"
# Субтитрованный черновик этого видео (чёткий, 1440x1080).
VIDEO_URL = os.environ.get("VIDEO_URL", "https://youtu.be/FqD5mLEaRfc")
MUSIC_GAIN = os.environ.get("MUSIC_GAIN", "0.15")  # тихий фон


def find_music_in_bot():
    """Ищет в сообщениях бота последний аудио-файл от Анны."""
    updates = get_updates_retry()
    best = None
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        f = None
        if msg.get("audio"):
            a = msg["audio"]
            f = {"file_id": a["file_id"], "size": a.get("file_size", 0),
                 "name": a.get("file_name", "music.mp3")}
        elif msg.get("voice"):
            v = msg["voice"]
            f = {"file_id": v["file_id"], "size": v.get("file_size", 0),
                 "name": "voice.ogg"}
        elif msg.get("document") and str(msg["document"].get("mime_type", "")).startswith("audio"):
            d = msg["document"]
            f = {"file_id": d["file_id"], "size": d.get("file_size", 0),
                 "name": d.get("file_name", "music")}
        if f:
            best = {"update_id": u["update_id"], **f}
    return best


def download_music():
    os.makedirs(WORK, exist_ok=True)
    # Приоритет: если задан готовый файл музыки в репозитории (MUSIC_FILE),
    # берём его — не зависим от бота (getUpdates бывает заблокирован 409).
    mf = os.environ.get("MUSIC_FILE")
    if mf and os.path.exists(mf):
        print(f"беру музыку из файла: {mf}")
        return mf
    m = find_music_in_bot()
    if not m:
        tg.send_message("Не вижу в боте аудио-файл с музыкой. Залей трек ещё раз "
                        "(как аудио или файл), и я наложу.")
        raise SystemExit("музыка в боте не найдена")
    if m["size"] and m["size"] > 19 * 1024 * 1024:
        tg.send_message("Твой трек больше 20 МБ — бот не может его скачать. "
                        "Пришли покороче/полегче версию (или ссылку).")
        raise SystemExit("файл музыки > 20 МБ")
    ext = os.path.splitext(m["name"])[1] or ".mp3"
    dst = os.path.join(WORK, "music" + ext)
    tg.download_file(m["file_id"], dst)
    print(f"музыка скачана: {m['name']} ({m['size']} байт)")
    return dst


def download_video():
    os.makedirs(WORK, exist_ok=True)
    raw = os.path.join(WORK, "subbed.mp4")
    if os.path.exists(raw):
        return raw
    base = ["yt-dlp", "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=web_safari,web,tv",
            "--retries", "5", "--fragment-retries", "5"]
    if os.path.exists(os.path.join(WORK, "cookies.txt")):
        base += ["--cookies", os.path.join(WORK, "cookies.txt")]
    cmd = base + ["-S", "res,ext:mp4:m4a", "-f", "bv*+ba/b",
                  "--merge-output-format", "mp4", "-o", raw, VIDEO_URL]
    for a in range(1, 4):
        if subprocess.run(cmd).returncode == 0 and os.path.exists(raw):
            return raw
        print(f"скачивание видео: попытка {a} не удалась")
    raise SystemExit("не удалось скачать субтитрованное видео")


def mix(video, music):
    out = os.path.join(WORK, "final_music.mp4")
    # музыка зациклена на всю длину, тихо; голос — полный. duration=first =
    # длина по голосу (первый вход в amix — дорожка видео).
    filt = (f"[0:a]volume=1.0[a0];"
            f"[1:a]volume={MUSIC_GAIN},afade=t=in:st=0:d=2[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    cmd = ["ffmpeg", "-y", "-i", video, "-stream_loop", "-1", "-i", music,
           "-filter_complex", filt, "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
           out, "-loglevel", "error"]
    subprocess.run(cmd, check=True)
    return out


def main():
    music = download_music()
    video = download_video()
    print("накладываю музыку фоном под голос...")
    out = mix(video, music)
    print("заливаю на YouTube (unlisted)...")
    yt = yt_ops.upload_video(
        out, "Черновик — субтитры + твоя музыка",
        "Черновик на проверку: субтитры + фоновая музыка (пиано). "
        "Без стабилизации и цвета.", privacy="unlisted")
    vid = yt["id"] if isinstance(yt, dict) else yt
    link = f"https://youtu.be/{vid}"
    tg.send_message(f"Готово: субтитры + твоя музыка (пиано) фоном под голос.\n{link}\n"
                    f"Посмотри. Если ок — уберу субтитры и отправлю в Vizard на нарезку.")
    print("ссылка:", link)


if __name__ == "__main__":
    main()
