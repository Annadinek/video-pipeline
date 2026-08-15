#!/usr/bin/env python3
"""
inserts.py — ВСТАВКИ в вертикальный клип (динамика без приближений кадра).

Идея (разбор образца Анны): за минуту ~10 вставок, все одного вида — карточка
сверху кадра, лицо не закрывается. Приближений/отъездов нет — движение создают
именно вставки.

КАК РАБОТАЕТ:
  - расшифровка УЖЕ есть: transcript.json рядом с клипом (пословные метки от
    этапа субтитров). Переиспользуем, второй раз НЕ распознаём;
  - в presets/inserts.json заданы ФРАЗЫ-связки (Анна произносит их намеренно,
    в обычной речи не встречаются) → что вставить. Ищем фразу ЦЕЛИКОМ (по
    порядку слов), не отдельные слова;
  - вставка появляется СО СЛЕДУЮЩЕГО слова после фразы, держится `seconds`;
  - фразы нет в расшифровке — пропускаем МОЛЧА, этап не блокируем.

ТРИ ВИДА (поле "type"):
  - card       — картинка/видео в углу кадра, до трети ширины, лицо не закрывает
                 (основной вид);
  - fullscreen — на весь экран, лицо уходит на заданные секунды;
  - text       — крупная фраза на плашке (плавно появляется и уходит).
                 Рисуется через libass (в проекте drawtext недоступен).

ОТКУДА КАРТИНКИ:
  - "file": свой файл из assets/inserts/ (обложки книг, скриншоты и т.п.);
  - "search": строка запроса к Pexels (бесплатные видео/картинки без лицензии) —
    машина сама находит и скачивает. Нужен ключ PEXELS_API_KEY (см. SKILL/PROMPT).
    Нет ключа или ничего не нашлось — вставку пропускаем, остальное делаем.

Все настройки — presets/inserts.json (в коде — только умолчания). Запуск:
  scripts/inserts.py --input outputs/ready/[id]/clip_subs.mp4
  scripts/inserts.py --clips-dir outputs/ready/[id]/clips   # по всем клипам Vizard
  scripts/inserts.py --input clip.mp4 --dry-run             # только найти фразы, без рендера
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import NoReturn

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "inserts.json")
CACHE_DIR = os.path.join(ROOT, "assets", "inserts", "_cache")
FONTFILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

PEXELS_PHOTO = "https://api.pexels.com/v1/search"
PEXELS_VIDEO = "https://api.pexels.com/videos/search"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".avi", ".m4v"}

# Глобальные умолчания (переопределяются блоком "_settings" в inserts.json)
SETTINGS_DEFAULTS = {
    "card_width_pct": 33,       # ширина карточки в % от ширины кадра (до трети)
    "margin_pct": 5,            # отступ от края кадра в % от ширины
    "fade_seconds": 0.4,        # плавное появление/уход
    "default_position": "top",  # top / top-left / top-right / bottom-left / bottom-right / center
    "text_font": "DejaVu Sans",
    "text_size_pct": 8,         # размер текста в % от меньшей стороны кадра
    "text_color": "FFFFFF",     # цвет текста RRGGBB
    "plate_color": "000000",    # цвет плашки RRGGBB
    "plate_opacity": 0.6,       # непрозрачность плашки 0..1
    "pexels_orientation": "portrait",  # portrait/landscape/square — под вертикаль
}


def die(msg, code) -> NoReturn:
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"inserts: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# ---------- конфиг ----------
def load_config():
    """Вернуть (settings, phrases). phrases: {фраза: {type, file/search/text, ...}}."""
    settings = dict(SETTINGS_DEFAULTS)
    phrases = {}
    status = "defaults (нет файла)"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("не объект JSON")
            user_settings = data.get("_settings", {})
            if isinstance(user_settings, dict):
                for k in SETTINGS_DEFAULTS:
                    if k in user_settings and user_settings[k] is not None:
                        settings[k] = user_settings[k]
            for key, val in data.items():
                if key.startswith("_"):
                    continue  # служебные ключи (_settings и пр.)
                if isinstance(val, dict) and val.get("type"):
                    phrases[key] = val
            status = f"ok (presets/inserts.json): фраз {len(phrases)}"
        except Exception as e:  # noqa: BLE001
            settings = dict(SETTINGS_DEFAULTS)
            phrases = {}
            status = f"битый файл ({e}), взял умолчания"
    settings["_config_status"] = status
    return settings, phrases


# ---------- расшифровка и поиск фраз ----------
def normalize(text):
    """Слово/фраза → сравнимая форма: нижний регистр, ё→е, без пунктуации."""
    text = str(text).lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_tokens(text):
    n = normalize(text)
    return n.split() if n else []


def flatten_words(segments):
    """Все слова подряд: [{word,start,end}]. Если у сегмента нет пословных меток —
    раскидываем его текст по времени равномерно (как в subtitles.py)."""
    words = []
    for seg in segments:
        raw = seg.get("words") or []
        clean = [{"word": str(w["word"]).strip(),
                  "start": float(w["start"]), "end": float(w["end"])}
                 for w in raw
                 if w.get("word") and str(w["word"]).strip()
                 and w.get("start") is not None and w.get("end") is not None]
        if clean:
            words.extend(clean)
            continue
        toks = str(seg.get("text", "")).split()
        if not toks or seg.get("start") is None or seg.get("end") is None:
            continue
        s0, e0 = float(seg["start"]), float(seg["end"])
        span = max(e0 - s0, 0.01)
        m = len(toks)
        for k, t in enumerate(toks):
            words.append({"word": t, "start": s0 + (k / m) * span,
                          "end": s0 + ((k + 1) / m) * span})
    return words


def find_matches(words, phrase):
    """Все вхождения фразы (по порядку слов). Возвращает список позиций конца фразы:
    [(insert_start_time, matched_text)]. Вставка — со следующего слова после фразы."""
    ptoks = normalize_tokens(phrase)
    if not ptoks:
        return []
    norm = [normalize(w["word"]) for w in words]
    L, N = len(ptoks), len(words)
    hits = []
    i = 0
    while i + L <= N:
        if norm[i:i + L] == ptoks:
            # вставка появляется со СЛЕДУЮЩЕГО слова после фразы
            nxt = i + L
            if nxt < N:
                start = words[nxt]["start"]
            else:
                start = words[i + L - 1]["end"]
            hits.append((float(start), " ".join(w["word"] for w in words[i:i + L])))
            i += L  # не пересекаем одно и то же вхождение дважды
        else:
            i += 1
    return hits


# ---------- Pexels ----------
def pexels_fetch(query, want_video, orientation, api_key):
    """Скачать 1 подходящий файл с Pexels в кэш. Вернуть путь или None (мягко)."""
    if not api_key:
        return None, "нет ключа PEXELS_API_KEY"
    os.makedirs(CACHE_DIR, exist_ok=True)
    tag = "vid" if want_video else "img"
    h = hashlib.sha1(f"{tag}|{orientation}|{query}".encode("utf-8")).hexdigest()[:16]
    # уже качали?
    for ext in list(VIDEO_EXT) + list(IMAGE_EXT):
        cached = os.path.join(CACHE_DIR, f"{h}{ext}")
        if os.path.exists(cached):
            return cached, "из кэша"
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": 1, "orientation": orientation}
    url = PEXELS_VIDEO if want_video else PEXELS_PHOTO
    try:
        r = requests.get(url, headers=headers, params=params, timeout=60)
    except requests.RequestException as e:
        return None, f"сеть Pexels: {e}"
    if r.status_code == 401:
        return None, "Pexels 401 (неверный ключ)"
    if r.status_code == 429:
        return None, "Pexels 429 (лимит запросов)"
    if r.status_code != 200:
        return None, f"Pexels HTTP {r.status_code}"
    data = r.json()
    if want_video:
        vids = data.get("videos") or []
        if not vids:
            return None, "Pexels: ничего не нашлось"
        files = sorted(vids[0].get("video_files", []),
                       key=lambda vf: (vf.get("width") or 0), reverse=True)
        # берём приемлемое по высоте (не гигант): первое с height<=1920 или самое мелкое
        pick = next((vf for vf in files if (vf.get("height") or 0) <= 1920), files[-1] if files else None)
        if not pick or not pick.get("link"):
            return None, "Pexels: нет ссылки на файл"
        link, ext = pick["link"], ".mp4"
    else:
        photos = data.get("photos") or []
        if not photos:
            return None, "Pexels: ничего не нашлось"
        src = photos[0].get("src", {})
        link = src.get("large2x") or src.get("large") or src.get("original")
        if not link:
            return None, "Pexels: нет ссылки на файл"
        ext = os.path.splitext(link.split("?")[0])[1].lower() or ".jpg"
        if ext not in IMAGE_EXT:
            ext = ".jpg"
    dst = os.path.join(CACHE_DIR, f"{h}{ext}")
    try:
        with requests.get(link, stream=True, timeout=300) as resp:
            resp.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        return None, f"скачивание Pexels: {e}"
    return dst, "скачано с Pexels"


# ---------- сборка ----------
def is_video_file(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def ass_color(rrggbb, alpha=0.0):
    """RRGGBB → ASS &HAABBGGRR. alpha: 0=непрозрачно, 1=прозрачно."""
    s = str(rrggbb).strip().lstrip("#")
    if len(s) != 6:
        s = "FFFFFF"
    aa = f"{int(round(max(0.0, min(1.0, alpha)) * 255)):02X}"
    return f"&H{aa}{s[4:6]}{s[2:4]}{s[0:2]}".upper()


def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - 3600 * h - 60 * m
    return f"{h:d}:{m:02d}:{s:05.2f}"


def esc_ass_text(text):
    return (str(text).replace("\\", "").replace("{", "(").replace("}", ")")
            .replace("\n", " ").strip())


def build_text_ass(text_inserts, settings, w, h, path):
    """ASS со всеми текстовыми вставками: крупная строка на плашке, плавно."""
    base = min(w, h)
    fs = max(1, round(base * float(settings["text_size_pct"]) / 100.0))
    mv = max(0, round(h * float(settings["margin_pct"]) / 100.0))
    ms = max(0, round(w * float(settings["margin_pct"]) / 100.0))
    primary = ass_color(settings["text_color"])
    # плашка = непрозрачный бокс (BorderStyle=3), цвет — BackColour с альфой
    plate = ass_color(settings["plate_color"], 1.0 - float(settings["plate_opacity"]))
    fade_ms = int(float(settings["fade_seconds"]) * 1000)
    # Alignment 8 = сверху по центру
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Plate,{settings['text_font']},{fs},{primary},&H000000FF,"
        f"&H00000000,{plate},-1,0,0,0,100,100,0,0,3,16,0,8,{ms},{ms},{mv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for ins in text_inserts:
            s, e = ins["start"], ins["end"]
            txt = "{\\fad(%d,%d)}%s" % (fade_ms, fade_ms, esc_ass_text(ins["text"]))
            f.write(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Plate,,0,0,0,,{txt}\n")


def overlay_xy(position, margin_px):
    """Выражения x:y для overlay по имени позиции."""
    m = margin_px
    return {
        "top": (f"(main_w-overlay_w)/2", f"{m}"),
        "top-center": (f"(main_w-overlay_w)/2", f"{m}"),
        "top-left": (f"{m}", f"{m}"),
        "top-right": (f"main_w-overlay_w-{m}", f"{m}"),
        "bottom-left": (f"{m}", f"main_h-overlay_h-{m}"),
        "bottom-right": (f"main_w-overlay_w-{m}", f"main_h-overlay_h-{m}"),
        "center": (f"(main_w-overlay_w)/2", f"(main_h-overlay_h)/2"),
    }.get(position, (f"(main_w-overlay_w)/2", f"{m}"))


def render(ffmpeg, src, media_inserts, text_ass, out, settings, w, h, clip_dur):
    """Собрать вставки в один проход ffmpeg (одно перекодирование). Текст — ASS,
    картинки/видео — overlay с плавным появлением/уходом."""
    filt = []
    cur = "0:v"

    if text_ass:
        ap = text_ass.replace("\\", "/").replace(":", r"\:")
        filt.append(f"[{cur}]ass={ap}[vtxt]")
        cur = "vtxt"

    margin = max(0, round(w * float(settings["margin_pct"]) / 100.0))
    card_w = max(2, round(w * float(settings["card_width_pct"]) / 100.0))
    fade = float(settings["fade_seconds"])

    # входы: сначала базовый клип (0), затем каждая вставка по порядку (1..N)
    inputs = ["-i", src]
    for ins in media_inserts:
        f = ins["file"]
        if is_video_file(f):
            inputs += ["-stream_loop", "-1", "-t", f"{clip_dur:.3f}", "-i", f]
        else:
            inputs += ["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", f]

    for k, ins in enumerate(media_inserts):
        in_idx = k + 1
        s, e = ins["start"], ins["end"]
        fout_start = max(s, e - fade)
        if ins["type"] == "fullscreen":
            scale = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
            xy = ("0", "0")
        else:  # card
            scale = f"scale={card_w}:-2"
            xy = overlay_xy(ins.get("position") or settings["default_position"], margin)
        lbl = f"ov{k}"
        filt.append(
            f"[{in_idx}:v]{scale},format=rgba,"
            f"fade=t=in:st={s:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={fout_start:.3f}:d={fade:.3f}:alpha=1[{lbl}]"
        )
        nxt = f"m{k}"
        filt.append(
            f"[{cur}][{lbl}]overlay={xy[0]}:{xy[1]}:"
            f"enable='between(t,{s:.3f},{e:.3f})':eof_action=pass[{nxt}]"
        )
        cur = nxt

    if not filt:
        # вставок нет — просто копия (без перекодирования)
        rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                          "-i", src, "-c", "copy", "-movflags", "+faststart", out])
        if rc != 0:
            raise RuntimeError(f"копирование без вставок: {err.strip()[-300:]}")
        return

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
        "-filter_complex", ";".join(filt),
        "-map", f"[{cur}]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-c:a", "copy", "-movflags", "+faststart", out,
    ]
    rc, _, err = run(cmd)
    if rc != 0:
        raise RuntimeError(f"ffmpeg вставки: {err.strip()[-400:]}")


# ---------- обработка клипа ----------
def ffprobe_size_dur(ffprobe, path):
    rc, out, err = run([ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path])
    vals = [x for x in out.strip().splitlines() if x.strip()]
    if rc != 0 or len(vals) < 3:
        raise RuntimeError(f"не удалось прочитать размер/длительность: {err.strip()[-200:]}")
    return int(vals[0]), int(vals[1]), float(vals[2])


def plan_inserts(words, phrases, settings, clip_dur, api_key):
    """Найти вставки по фразам. Возвращает (список_вставок, отчёт_строки)."""
    planned = []
    report = []
    for phrase, spec in phrases.items():
        hits = find_matches(words, phrase)
        if not hits:
            report.append(("—", phrase, spec.get("type"), "не найдено (пропущено молча)"))
            continue
        seconds = float(spec.get("seconds", 4) or 4)
        for start, matched in hits:
            end = min(start + seconds, clip_dur)
            typ = str(spec.get("type", "card")).lower()
            item = {"phrase": phrase, "matched": matched, "start": start,
                    "end": end, "type": typ, "position": spec.get("position")}
            if typ == "text":
                item["text"] = spec.get("text", "")
                if not str(item["text"]).strip():
                    report.append((f"{start:.1f}с", phrase, typ, "нет поля text — пропуск"))
                    continue
                planned.append(item)
                report.append((f"{start:.1f}с", phrase, typ, f"текст: «{item['text']}»"))
                continue
            # card / fullscreen: нужен файл (file) или поиск (search)
            src_desc = None
            f = spec.get("file")
            if f:
                fpath = f if os.path.isabs(f) else os.path.join(ROOT, f)
                if not os.path.exists(fpath):
                    report.append((f"{start:.1f}с", phrase, typ, f"нет файла {f} — пропуск"))
                    continue
                item["file"] = fpath
                src_desc = f"файл {f}"
            elif spec.get("search"):
                want_video = str(spec.get("media", "photo")).lower() == "video"
                got, why = pexels_fetch(spec["search"], want_video,
                                        settings["pexels_orientation"], api_key)
                if not got:
                    report.append((f"{start:.1f}с", phrase, typ,
                                   f"поиск «{spec['search']}»: {why} — пропуск"))
                    continue
                item["file"] = got
                src_desc = f"Pexels «{spec['search']}» ({why})"
            else:
                report.append((f"{start:.1f}с", phrase, typ, "нет ни file, ни search — пропуск"))
                continue
            planned.append(item)
            report.append((f"{start:.1f}с", phrase, typ, src_desc))
    planned.sort(key=lambda x: x["start"])
    return planned, report


def load_transcript(clip_path, explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    d = os.path.dirname(os.path.abspath(clip_path)) or "."
    tj = os.path.join(d, "transcript.json")
    return tj if os.path.exists(tj) else None


def process_clip(ffmpeg, ffprobe, settings, phrases, clip, output, transcript, api_key, dry):
    tj = load_transcript(clip, transcript)
    if not tj:
        # без расшифровки фразы не найти: отдаём копию, этап не блокируем
        if not dry:
            run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", clip, "-c", "copy", "-movflags", "+faststart", output])
        return {"resolution": "?", "planned": [], "report": [],
                "note": "нет transcript.json рядом — вставок нет"}

    with open(tj, encoding="utf-8") as f:
        data = json.load(f)
    words = flatten_words(data.get("segments", []))
    if not words:
        if not dry:
            run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", clip, "-c", "copy", "-movflags", "+faststart", output])
        return {"resolution": "?", "planned": [], "report": [],
                "note": "в transcript.json нет слов — вставок нет"}

    w, h, clip_dur = ffprobe_size_dur(ffprobe, clip)
    planned, report = plan_inserts(words, phrases, settings, clip_dur, api_key)

    if not dry:
        text_inserts = [p for p in planned if p["type"] == "text"]
        media_inserts = [p for p in planned if p["type"] in ("card", "fullscreen")]
        text_ass = None
        if text_inserts:
            work = os.path.join(os.path.dirname(os.path.abspath(clip)) or ".", "work")
            os.makedirs(work, exist_ok=True)
            text_ass = os.path.join(work, "inserts_text.ass")
            build_text_ass(text_inserts, settings, w, h, text_ass)
        render(ffmpeg, clip, media_inserts, text_ass, output, settings, w, h, clip_dur)

    return {"resolution": f"{w}x{h}", "planned": planned, "report": report,
            "note": "", "transcript": tj}


def print_report(clip, res, info):
    print(f"--- {clip} ({res}) ---")
    if info.get("note"):
        print(f"    {info['note']}")
    rep = info.get("report", [])
    if not rep:
        print("    фраз в конфиге нет")
        return
    for when, phrase, typ, what in rep:
        print(f"    [{when}] «{phrase}» → {typ}: {what}")
    print(f"    вставок поставлено: {len(info.get('planned', []))} из {len(rep)} фраз проверено")


def find_clips(clips_dir):
    """Клипы Vizard: подпапки clip_*/clip_subs.mp4 (после субтитров), иначе clip.mp4,
    иначе *.mp4 в корне (кроме результатов вставок)."""
    clips = []
    for name in sorted(os.listdir(clips_dir)):
        sub = os.path.join(clips_dir, name)
        if not os.path.isdir(sub):
            continue
        for cand in ("clip_subs.mp4", "clip.mp4"):
            p = os.path.join(sub, cand)
            if os.path.exists(p):
                clips.append(p)
                break
    if not clips:
        clips = [os.path.join(clips_dir, f) for f in sorted(os.listdir(clips_dir))
                 if f.lower().endswith(".mp4") and not f.endswith("_inserts.mp4")]
    return clips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="один клип (после субтитров): clip_subs.mp4")
    ap.add_argument("--clips-dir", help="папка с клипами (проход по всем)")
    ap.add_argument("--output", help="куда сохранить (по умолчанию clip_inserts.mp4 рядом)")
    ap.add_argument("--transcript", help="transcript.json (по умолчанию рядом с клипом)")
    ap.add_argument("--dry-run", action="store_true",
                    help="только найти фразы и показать план, без рендера")
    args = ap.parse_args()

    if bool(args.input) == bool(args.clips_dir):
        die("укажи ровно одно: --input <файл> ИЛИ --clips-dir <папка>", 2)

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)

    settings, phrases = load_config()
    api_key = os.environ.get("PEXELS_API_KEY")
    if api_key:
        api_key = api_key.strip()

    print("=== ОТЧЁТ inserts ===")
    print(f"Конфиг:  {settings['_config_status']}")
    print(f"Pexels:  {'ключ есть' if api_key else 'ключа нет (search-вставки пропускаются)'}")
    if args.dry_run:
        print("Режим:   dry-run (только поиск фраз, без рендера)")

    if args.clips_dir:
        if not os.path.isdir(args.clips_dir):
            die(f"нет папки клипов: {args.clips_dir}", 2)
        clips = find_clips(args.clips_dir)
        if not clips:
            die(f"в {args.clips_dir} не нашёл клипов", 2)
    else:
        if not os.path.exists(args.input):
            die(f"нет входного файла: {args.input}", 2)
        clips = [args.input]

    total_inserts = 0
    for clip in clips:
        out = args.output if (args.output and len(clips) == 1) else \
            os.path.join(os.path.dirname(os.path.abspath(clip)), "clip_inserts.mp4")
        try:
            info = process_clip(ffmpeg, ffprobe, settings, phrases, clip, out,
                                args.transcript, api_key, args.dry_run)
        except (RuntimeError, ValueError, OSError) as e:
            print(f"--- {clip}: ОШИБКА: {e}")
            continue
        print_report(clip, info["resolution"], info)
        total_inserts += len(info.get("planned", []))
        if not args.dry_run and not info.get("note"):
            print(f"    файл: {out}")

    print(f"ИТОГО вставок по всем клипам: {total_inserts}")
    print("=====================")


if __name__ == "__main__":
    main()
