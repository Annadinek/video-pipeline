#!/usr/bin/env python3
# resub.py — заменяет большие вшитые субтитры на маленькие в готовых клипах Vizard.
#
# Зачем: клипы Vizard хорошие, но в них видны СТАРЫЕ вшитые субтитры Анны — крупные,
# края выходят за кадр. Стереть вшитые пиксели начисто нельзя, поэтому:
#   1) обрезаем нижнюю полосу кадра со старыми субтитрами и подтягиваем кадр обратно
#      в вертикаль 9:16 (без искажения, лёгкий зум);
#   2) распознаём речь клипа (faster-whisper, по словам) и рисуем НОВЫЕ маленькие
#      субтитры с зелёной караоке-подсветкой (стиль эталона: белый текст, зелёное
#      текущее слово, тонкая обводка, без плашки, по центру снизу);
#   3) отправляем в бот с подписью.
#
# Один проход ffmpeg на клип: crop → scale → crop → ass. Ключи только из секретов.
# Запуск в GitHub Actions (там есть VIZARDAI_API_KEY и TELEGRAM_BOT_TOKEN).

import os
import re
import subprocess

import requests

import config
import tg

API = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"
PROJECT_ID = os.environ.get("VIZARD_PROJECT_ID", "").strip()
# Какие клипы обрабатывать: 1-based номера через запятую (как в списке в боте).
INDICES = [int(x) for x in os.environ.get("INDICES", "1").split(",") if x.strip()]
# Доля кадра снизу, которую РАЗМЫВАЕМ, чтобы спрятать старые крупные субтитры
# (кадр НЕ обрезаем и НЕ искажаем — просто мажем полосу внизу).
BLUR_BAND = float(os.environ.get("BLUR_BAND", "0.18"))
# Размер новых субтитров (шрифт ASS). «Маленькие» — 44.
FONT_SIZE = int(os.environ.get("FONT_SIZE", "44"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
MAX_WORDS_PER_LINE = 4

# Мусор, который Whisper выдумывает на тишине/шуме — вычищаем.
JUNK = re.compile(r"^\s*[\[(]?\s*(музык\w*|аплодисмент\w*|смех|вдох|субтитры[^)]*|"
                  r"продолжение следует)\s*[\])]?\s*$", re.IGNORECASE)


def query_clips():
    key = config.require_env("VIZARDAI_API_KEY")
    r = requests.get(f"{API}/project/query/{PROJECT_ID}",
                     headers={"VIZARDAI_API_KEY": key}, timeout=60).json()
    if r.get("code") != 2000 or not r.get("videos"):
        raise RuntimeError(f"Vizard query: {r}")
    clips = r["videos"]

    def score(c):
        try:
            return float(c.get("viralScore") or 0)
        except (TypeError, ValueError):
            return 0.0
    clips.sort(key=score, reverse=True)
    return clips


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def transcribe_words(clip_path):
    """faster-whisper по словам, с VAD (у Анны уличный шум)."""
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        clip_path, language="ru", word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 400})
    words = []
    for seg in segments:
        if JUNK.match(seg.text or ""):
            continue
        for w in (seg.words or []):
            t = (w.word or "").strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words, path):
    """Маленькие субтитры с караоке: белый текст, текущее слово зелёное."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,DejaVu Sans,{FONT_SIZE},&H0000FF00,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,60,60,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Группируем слова по строкам (до MAX_WORDS_PER_LINE).
    lines = [words[i:i + MAX_WORDS_PER_LINE]
             for i in range(0, len(words), MAX_WORDS_PER_LINE)]
    events = []
    for group in lines:
        if not group:
            continue
        start = group[0][0]
        end = group[-1][1]
        parts = []
        for (ws, we, t) in group:
            dur_cs = max(int((we - ws) * 100), 1)  # длительность в сотых
            parts.append(f"{{\\kf{dur_cs}}}{t}")
        text = " ".join(parts)
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Def,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    return path


def render(clip_path, ass_path, out_path):
    """Один проход БЕЗ обрезки и искажения: размываем нижнюю полосу со старыми
    субтитрами и поверх вбиваем новые маленькие."""
    b = BLUR_BAND
    # split → нижнюю полосу сильно размыть → вернуть на место → новые субтитры
    fc = (
        f"[0:v]split=2[base][t];"
        f"[t]crop=iw:ih*{b}:0:ih-ih*{b},boxblur=luma_radius=40:luma_power=4[bl];"
        f"[base][bl]overlay=0:H-h[bg];"
        f"[bg]ass={ass_path}[v]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", out_path],
        check=True, capture_output=True)
    return out_path


def detect_bars(src, dur):
    """Ищем ЧЁРНЫЕ полосы (letterbox) сверху/снизу — Vizard иногда их оставляет.
    Возвращаем crop-рамку (w, h, x, y). Ширину не трогаем (тёмный салон машины —
    не полоса), полосы режем только вертикальные и не больше 25% высоты, чтобы
    случайно не обрезать картинку."""
    pr = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
        capture_output=True, text=True).stdout.strip()
    W, Hh = (int(v) for v in pr.split("x"))
    ss = f"{max(dur/2, 1):.1f}"
    r = subprocess.run(
        ["ffmpeg", "-ss", ss, "-i", src, "-vf", "cropdetect=18:2:0",
         "-frames:v", "20", "-f", "null", "-"],
        capture_output=True, text=True)
    ys, hs = [], []
    for _w, h, _x, y in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr):
        ys.append(int(y)); hs.append(int(h))
    if not hs:
        return (W, Hh, 0, 0)
    # берём самую консервативную (наибольшую) высоту контента среди кадров
    y0 = min(ys); h0 = max(hs)
    y0 = max(0, min(y0, Hh))
    h0 = min(h0, Hh - y0)
    # не режем больше 25% высоты суммарно — иначе это не полоса, а ошибка детектора
    if (Hh - h0) > 0.25 * Hh:
        return (W, Hh, 0, 0)
    return (W, h0, 0, y0)


def face_stats(src, dur, model_path, crop=None):
    """Медиана размера лица и его центра по нескольким кадрам (через mediapipe).
    Меряем на кадре ПОСЛЕ обрезки полос (crop), поэтому доли — от чистого контента."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mpy
    from mediapipe.tasks.python import vision
    lm = vision.FaceLandmarker.create_from_options(
        vision.FaceLandmarkerOptions(
            base_options=mpy.BaseOptions(model_asset_path=model_path), num_faces=1))
    fracs, cxs, cys = [], [], []
    n = 10
    for i in range(1, n + 1):
        ss = f"{dur * i / (n + 1):.2f}"
        tmp = "resub/_f.jpg"
        vf = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}" if crop else None
        cmd = ["ffmpeg", "-y", "-ss", ss, "-i", src]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-frames:v", "1", "-q:v", "3", tmp]
        subprocess.run(cmd, check=True, capture_output=True)
        img = cv2.imread(tmp)
        if img is None:
            continue
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                 data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        if not res.face_landmarks:
            continue
        p = res.face_landmarks[0]
        xs = [q.x for q in p]; ys = [q.y for q in p]
        fracs.append(max(ys) - min(ys))
        cxs.append((min(xs) + max(xs)) / 2)
        cys.append((min(ys) + max(ys)) / 2)
    if not fracs:
        return None
    fracs.sort(); cxs.sort(); cys.sort()
    mid = len(fracs) // 2
    return fracs[mid], cxs[mid], cys[mid]


def render_match20(src, out, crop, stats):
    """Кадрируем клип «как эталон 20»: убираем чёрные полосы и приближаем лицо так,
    чтобы оно занимало ту же долю кадра, что в эталоне. БЕЗ искажения (равномерный
    зум), БЕЗ размытых боков. Один проход ffmpeg, постоянная рамка (без дрожания)."""
    target = float(os.environ.get("TARGET_FILL", "0.40"))
    face_y = float(os.environ.get("FACE_Y", "0.45"))
    maxzoom = float(os.environ.get("MAXZOOM", "1.7"))
    frac, cx, cy = stats
    cw, ch, cx0, cy0 = crop
    W, H = 1080, 1920
    # масштаб, чтобы лицо заняло target-долю высоты выходного кадра
    s = target * H / (frac * ch)
    s = min(s, maxzoom)
    # гарантируем, что кадр покрывает 1080x1920 (без чёрных краёв)
    s = max(s, W / cw, H / ch)
    sw, sh = int(round(cw * s)), int(round(ch * s))
    cropx = int(round(cx * sw - W / 2)); cropx = max(0, min(cropx, sw - W))
    cropy = int(round(cy * sh - face_y * H)); cropy = max(0, min(cropy, sh - H))
    fc = (f"crop={cw}:{ch}:{cx0}:{cy0},scale={sw}:{sh},"
          f"crop={W}:{H}:{cropx}:{cropy}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-vf", fc, "-map", "0:v", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", out],
        check=True, capture_output=True)
    return out, s


def make_caption(clip, idx):
    """Короткая подпись к ролику (пока из заголовка; настоящие тексты — отдельно)."""
    title = (clip.get("title") or "").strip()
    title = re.sub(r"\s*\(\d+\)\s*$", "", title)  # убрать «(1)» и т.п.
    return f"Ролик {idx} • {title}"


def main():
    if not PROJECT_ID:
        raise SystemExit("Не задан VIZARD_PROJECT_ID.")
    clips = query_clips()
    os.makedirs("resub", exist_ok=True)
    # Готовые подписи по номеру клипа (если задан файл).
    captions = {}
    cf = os.environ.get("CAPTIONS_FILE", "").strip()
    if cf and os.path.exists(cf):
        import json
        with open(cf, encoding="utf-8") as f:
            captions = json.load(f)
    # Горизонтальное сжатие для «широкого лица» (0.90 = уже на 10%). 0 = выкл.
    squeeze = float(os.environ.get("SQUEEZE", "0") or "0")
    done = 0
    for idx in INDICES:
        if idx < 1 or idx > len(clips):
            print(f"клип {idx}: вне диапазона 1..{len(clips)}")
            continue
        clip = clips[idx - 1]
        src = download(clip["videoUrl"], f"resub/src_{idx}.mp4")
        # Режим «как есть»: отправить ОРИГИНАЛ клипа Vizard в бот, ничего не меняя.
        if os.environ.get("PASSTHROUGH", "0") == "1":
            cap = captions.get(str(idx)) or f"Ролик {idx}"
            size = os.path.getsize(src) / 1e6
            if size <= 49:
                tg.send_video(src, caption=cap)
            else:
                tg.send_message(cap + f"\n(файл {size:.0f} МБ велик для бота)")
            os.remove(src)
            done += 1
            print(f"клип {idx}: оригинал отправлен")
            continue
        # Режим «сырой кадр»: вынуть кадры оригинального клипа Vizard (без обработки),
        # чтобы посмотреть глазами, растянут ли он.
        if os.environ.get("RAW_ONLY", "0") == "1":
            os.makedirs("resub_preview", exist_ok=True)
            # Параметры пикселя/сторон — чтобы поймать «неквадратный пиксель» (SAR).
            pr = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
                 "-of", "default=noprint_wrappers=1", src],
                capture_output=True, text=True)
            print(f"клип {idx} параметры:\n{pr.stdout.strip()}")
            dur = (clip.get("videoMsDuration") or 0) / 1000
            for tag, ss in (("a", "1.5"), ("b", f"{max(dur/2, 2):.1f}")):
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", src, "-frames:v", "1",
                                "-q:v", "3", f"resub_preview/raw{idx}{tag}.jpg"],
                               check=True, capture_output=True)
            os.remove(src)
            done += 1
            print(f"клип {idx}: сырые кадры в resub_preview/")
            continue
        # Режим «как эталон 20»: убрать чёрные полосы + приблизить лицо до той же
        # доли кадра, что в эталоне (клип 20). Без искажения, без размытых боков.
        if os.environ.get("MATCH20", "0") == "1":
            model = os.environ.get("FACE_MODEL", "face_landmarker.task")
            dur = (clip.get("videoMsDuration") or 0) / 1000 or 30
            crop = detect_bars(src, dur)
            if not crop:
                # не удалось определить — берём полный кадр по ffprobe
                pr = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
                    capture_output=True, text=True).stdout.strip()
                w, h = (int(v) for v in pr.split("x"))
                crop = (w, h, 0, 0)
            stats = face_stats(src, dur, model, crop=crop)
            if not stats:
                tg.send_message(f"Ролик {idx}: лицо не нашлось, пропускаю сужение.")
                os.remove(src); continue
            out = f"resub/m20_{idx}.mp4"
            out, s = render_match20(src, out, crop, stats)
            cap = captions.get(str(idx)) or f"Ролик {idx}"
            if os.environ.get("PREVIEW_ONLY", "0") == "1":
                os.makedirs("resub_preview", exist_ok=True)
                for tag, ss in (("a", f"{dur*0.25:.1f}"), ("b", f"{dur*0.6:.1f}")):
                    subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", out, "-frames:v", "1",
                                    "-q:v", "3", f"resub_preview/m20_{idx}{tag}.jpg"],
                                   check=True, capture_output=True)
                print(f"клип {idx}: match20 zoom={s:.2f}, полосы {crop}, кадры в resub_preview/")
            else:
                size = os.path.getsize(out) / 1e6
                if size <= 49:
                    tg.send_video(out, caption=cap)
                else:
                    tg.send_message(cap + f"\n(файл {size:.0f} МБ велик для бота)")
                print(f"клип {idx}: match20 zoom={s:.2f}, отправлен")
            for p in (src, out):
                try:
                    os.remove(p)
                except OSError:
                    pass
            done += 1
            continue
        # Режим «узкое лицо»: делаем лицо у́же, но кадр ОСТАЁТСЯ стандартным 9:16
        # (1080x1920) — тогда ни один плеер не растянет его обратно (из-за этого в
        # прошлый раз лицо снова становилось широким). Как: сначала убираем чёрные
        # полосы, потом растягиваем кадр по ВЫСОТЕ в 1/squeeze раз (лицо становится
        # у́же во столько же), и обрезаем по центру обратно до 1080x1920. Без сжатия
        # ширины наружу, без размытых боков, без чёрных полос. squeeze=0.75 → лицо
        # у́же на ~25%. Берём ОРИГИНАЛ Vizard (качество не теряем).
        if squeeze and squeeze > 0:
            out = f"resub/sq_{idx}.mp4"
            dur = (clip.get("videoMsDuration") or 0) / 1000 or 30
            cw, ch, cx0, cy0 = detect_bars(src, dur)  # рамка без чёрных полос
            H2 = int(round(ch / squeeze / 2) * 2)      # растяжение по высоте
            if H2 < 1920:
                H2 = 1920
            off = int(((H2 - 1920) // 2) // 2 * 2)     # центрируем обрезку
            fc = (f"crop={cw}:{ch}:{cx0}:{cy0},"
                  f"scale={cw}:{H2},crop={cw}:1920:0:{off}")
            subprocess.run(["ffmpeg", "-y", "-i", src,
                            "-vf", fc, "-map", "0:v", "-map", "0:a?",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-c:a", "aac", "-b:a", "128k", out],
                           check=True, capture_output=True)
            cap = captions.get(str(idx)) or f"Ролик {idx}"
            if os.environ.get("PREVIEW_ONLY", "0") == "1":
                os.makedirs("resub_preview", exist_ok=True)
                subprocess.run(["ffmpeg", "-y", "-ss", "1.5", "-i", out, "-frames:v", "1",
                                "-q:v", "3", f"resub_preview/sq{idx}.jpg"],
                               check=True, capture_output=True)
                print(f"клип {idx}: сжат ({squeeze}), кадр в resub_preview/")
            else:
                tg.send_video(out, caption=cap)
                print(f"клип {idx}: сжат ({squeeze}), отправлен")
            for p in (src, out):
                try:
                    os.remove(p)
                except OSError:
                    pass
            done += 1
            continue
        words = transcribe_words(src)
        if not words:
            tg.send_message(f"Ролик {idx}: речь не распозналась, пропускаю.")
            print(f"клип {idx}: нет слов")
            continue
        ass = build_ass(words, f"resub/subs_{idx}.ass")
        out = render(src, ass, f"resub/out_{idx}.mp4")
        cap = make_caption(clip, idx)
        # Режим предпросмотра: вынуть кадры (низ, где субтитры) и НЕ слать в бот —
        # чтобы Клод сам посмотрел глазами, что старых субтитров не видно.
        if os.environ.get("PREVIEW_ONLY", "0") == "1":
            os.makedirs("resub_preview", exist_ok=True)
            dur = (clip.get("videoMsDuration") or 0) / 1000
            for tag, ss in (("a", "1.5"), ("b", f"{max(dur/2, 2):.1f}")):
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", out, "-frames:v", "1",
                                "-q:v", "3", f"resub_preview/r{idx}{tag}.jpg"],
                               check=True, capture_output=True)
            print(f"клип {idx}: {len(words)} слов, кадры в resub_preview/ (в бот не слал)")
            done += 1
            continue
        size = os.path.getsize(out) / 1e6
        if size <= 49:
            tg.send_video(out, caption=cap)
        else:
            tg.send_message(cap + f"\n(файл {size:.0f} МБ великоват для бота)")
        for p in (src, out):
            try:
                os.remove(p)
            except OSError:
                pass
        done += 1
        print(f"клип {idx}: {len(words)} слов, отправлен")
    print(f"ГОТОВО: обработано {done} из {len(INDICES)}.")


if __name__ == "__main__":
    main()
