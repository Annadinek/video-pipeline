#!/usr/bin/env python3
"""
find_dupes.py — поиск дублей (запнулась → повторила фразу заново).

Место в конвейере: 02a-dupes, ПЕРЕД обработкой звука/цвета — чтобы не красить
и не стабилизировать то, что потом вырежем.

ВХОД: outputs/ready/[id]/clip.mp4  (уже скачанный файл; сам НИЧЕГО не качает).
      Ссылка на YouTube — только для отдельного теста (dupes-test.yml).

ЧТО ДЕЛАЕТ:
  1. Распознаёт речь через Whisper (faster-whisper) с метками времени.
  2. Сохраняет расшифровку в ДВА файла — их переиспользуем для субтитров,
     распознавать второй раз не будем:
       outputs/ready/[id]/transcript.srt  (с метками времени)
       outputs/ready/[id]/transcript.txt  (сплошным текстом)
  3. Ищет дубли.
  4. ПОКА НЕ РЕЖЕТ. Только печатает и сохраняет список найденного:
       outputs/ready/[id]/dupes.json         (для машины)
       outputs/ready/[id]/dupes_report.txt   (для человека)

КАК НАХОДИМ ДУБЛЬ (метод — по словам, без процентов):
  Дубль — это когда фраза сказана ДВАЖДЫ ПОДРЯД, слово в слово.
  Значит ищем повтор, который:
   - идёт ПОДРЯД (между двумя версиями 0 слов — после чистки от паразитов);
   - совпадает СЛОВО В СЛОВО (не процент похожести);
   - длиной не меньше prefix_words слов (по умолчанию 4).
  Почему так, а не проценты: на коротких фразах процент обманывает — в трёх
  словах одно отличие сразу даёт треть. И меряем в СЛОВАХ, не в секундах
  (скорость речи гуляет) и не в символах (длинные слова врут).

  Риторический приём («в каждом вдохе есть смысл, в каждом взгляде есть смысл»)
  так НЕ ловится: между повторами стоит отличающееся слово (вдохе/взгляде),
  значит «подряд, слово в слово» не выполняется. Что и требуется:
  лучше пропустить, чем вырезать живое. Плюс ничего не режем — список смотрит человек.

Настройки — presets/dupes.json (сломан/нет файла → умолчания, не падаем).
Перед сравнением чистим: нижний регистр, ё→е, без пунктуации, без слов-паразитов
(ну, это, вот, как бы, значит). Лемматизации нет — намеренно.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "dupes.json")

DEFAULTS = {
    "model": "medium",           # faster-whisper: баланс точности и скорости на CPU
    "compute_type": "int8",      # int8 — мало памяти, годится для GitHub Actions
    "language": "ru",
    "vad_filter": True,          # режет тишину/паузы — точнее границы кусков
    "prefix_words": 4,           # минимум слов в повторе; фраза короче не считается
    "fillers": ["как бы", "ну", "это", "вот", "значит"],
}
BEAM_SIZE = 5
MAX_RUN = 40  # верхний предел длины повтора (слов) — защита от долгого поиска


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"find_dupes: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
    """presets/dupes.json поверх умолчаний. Сломан/нет файла → умолчания, не падаем."""
    cfg = dict(DEFAULTS)
    status = "defaults (нет файла)"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if not isinstance(user, dict):
                raise ValueError("не объект JSON")
            for k in DEFAULTS:
                if k in user and user[k] is not None:
                    cfg[k] = user[k]
            status = "ok (presets/dupes.json)"
        except Exception:
            cfg = dict(DEFAULTS)
            status = "битый файл, взял умолчания"
    if not isinstance(cfg.get("fillers"), list):
        cfg["fillers"] = list(DEFAULTS["fillers"])
    try:
        cfg["prefix_words"] = max(1, int(cfg["prefix_words"]))
    except (TypeError, ValueError):
        cfg["prefix_words"] = DEFAULTS["prefix_words"]
    cfg["_config_status"] = status
    return cfg


# --- нормализация текста ---
def normalize(text, fillers):
    """Нижний регистр, ё→е, без пунктуации, без слов-паразитов, схлопнуть пробелы."""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)  # пунктуацию → пробел
    # многословные паразиты («как бы») убираем первыми, потом однословные
    for f in sorted(fillers, key=lambda x: -len(x.split())):
        f_norm = f.lower().replace("ё", "е").strip()
        if not f_norm:
            continue
        t = re.sub(rf"\b{re.escape(f_norm)}\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --- время ---
def fmt_srt(t):
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_mmss(t):
    if t < 0:
        t = 0.0
    m = int(t // 60)
    s = t - 60 * m
    return f"{m:02d}:{s:04.1f}"


# --- Whisper ---
def extract_wav(ffmpeg, src, wav):
    """16 кГц моно wav — стандартный вход для Whisper."""
    rc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"не удалось извлечь звук: {rc.stderr.strip()[-300:]}")


def transcribe(wav, cfg):
    """Вернуть список сегментов [{start, end, text}] через faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die("нет faster-whisper (pip install faster-whisper) → blocked", 3)
    model = WhisperModel(cfg["model"], device="cpu", compute_type=cfg["compute_type"])
    segments, _info = model.transcribe(
        wav, language=cfg["language"], vad_filter=bool(cfg["vad_filter"]),
        beam_size=BEAM_SIZE,
    )
    out = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            out.append({"start": float(seg.start), "end": float(seg.end), "text": text})
    return out


# --- запись расшифровки ---
def write_srt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{s['text']}\n\n")


def write_txt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(" ".join(s["text"] for s in segs).strip() + "\n")


# --- поиск дублей ---
def build_tokens(segs, fillers):
    """
    Один поток слов по всему ролику (после чистки), с временем каждого слова.
    Так дубль ловится одинаково и внутри куска, и на стыке двух кусков —
    важно, что слова идут ПОДРЯД, а не в каком куске они оказались.
    """
    tokens = []
    for si, s in enumerate(segs):
        words = normalize(s["text"], fillers).split()
        w = len(words)
        span = max(s["end"] - s["start"], 0.0)
        for k, word in enumerate(words):
            # ровно распределяем слова по времени куска (метки на слово нет)
            t = s["start"] + (k + 0.5) / w * span if w else s["start"]
            tokens.append({"w": word, "seg": si, "t": t, "seg_text": s["text"]})
    return tokens


def find_dupes(segs, cfg):
    """
    Дубль = фраза, сказанная дважды ПОДРЯД, слово в слово, длиной ≥ prefix_words.
    Ищем в потоке слов позицию p, где words[p-L:p] == words[p:p+L] (повтор впритык),
    берём максимальную L; если L ≥ prefix_words — это дубль.
    """
    need = int(cfg["prefix_words"])
    tokens = build_tokens(segs, cfg["fillers"])
    W = [t["w"] for t in tokens]
    n = len(W)

    dupes = []
    p = 1
    while p < n:
        cap = min(MAX_RUN, p, n - p)
        found_L = 0
        for L in range(cap, need - 1, -1):
            if W[p - L:p] == W[p:p + L]:
                found_L = L
                break
        if found_L:
            L = found_L
            first = tokens[p - L:p]
            second = tokens[p:p + L]
            phrase = " ".join(W[p:p + L])
            seg_texts = []
            for tk in first + second:
                if tk["seg_text"] not in seg_texts:
                    seg_texts.append(tk["seg_text"])
            dupes.append({
                "index": len(dupes) + 1,
                "words": L,
                "phrase": phrase,
                "first_start": round(first[0]["t"], 2),
                "second_start": round(second[0]["t"], 2),
                "second_end": round(second[-1]["t"], 2),
                "context": seg_texts,
            })
            p += L  # перешагнуть вторую версию, чтобы не ловить сдвиги
        else:
            p += 1
    return dupes


# --- отчёты ---
def build_report(input_path, srt_path, txt_path, segs, dupes, cfg, duration):
    L = []
    L.append("=== ДУБЛИ (черновой список, ничего не вырезано) ===")
    L.append(f"Файл:         {input_path}")
    L.append(f"Модель:       faster-whisper {cfg['model']} ({cfg['compute_type']}, "
             f"язык {cfg['language']}, VAD {'вкл' if cfg['vad_filter'] else 'выкл'})")
    L.append(f"Конфиг:       {cfg['_config_status']}")
    L.append(f"Расшифровка:  {os.path.basename(srt_path)} + {os.path.basename(txt_path)} "
             "(переиспользуем для субтитров)")
    L.append(f"Длительность: {fmt_mmss(duration)} мин, кусков распознано: {len(segs)}")
    L.append(f"Правило:      фраза сказана дважды ПОДРЯД, слово в слово, "
             f"минимум {cfg['prefix_words']} слова (меряем в словах)")
    L.append(f"Найдено дублей: {len(dupes)}")
    L.append("")
    if not dupes:
        L.append("Дублей не нашёл. Значит фраз, сказанных дважды подряд слово в слово, нет")
        L.append(f"(если повтор короче {cfg['prefix_words']} слов — не считаю; поменяй prefix_words в presets/dupes.json).")
    for d in dupes:
        L.append(f"[{d['index']}] {fmt_mmss(d['first_start'])} – {fmt_mmss(d['second_end'])}   "
                 f"(повтор {d['words']} слов подряд)")
        L.append(f"    повтор: «{d['phrase']}» — сказано дважды подряд")
        for ctx in d["context"]:
            L.append(f"    в речи: «{ctx}»")
        L.append("")
    L.append("Ничего не вырезано. Посмотри список и скажи, где я не прав —")
    L.append("режем только после твоей проверки.")
    L.append("=" * 52)
    return "\n".join(L)


def write_pipeline(out_dir, dupes_info):
    """Записать сводку в state/pipeline.json (current.dupes), если id совпал."""
    pipeline = os.path.join(ROOT, "state", "pipeline.json")
    if not os.path.exists(pipeline):
        return False
    vid = os.path.basename(os.path.abspath(out_dir))
    try:
        with open(pipeline, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    cur = data.get("current")
    if not isinstance(cur, dict) or cur.get("id") != vid:
        return False
    cur["dupes"] = dupes_info
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4 (уже скачан)")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        die("нет ffmpeg → blocked", 3)
    if not os.path.exists(args.input):
        die(f"нет входного файла: {args.input}", 2)

    cfg = load_config()
    out_dir = os.path.dirname(os.path.abspath(args.input)) or "."
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    srt_path = os.path.join(out_dir, "transcript.srt")
    txt_path = os.path.join(out_dir, "transcript.txt")
    dupes_json = os.path.join(out_dir, "dupes.json")
    dupes_txt = os.path.join(out_dir, "dupes_report.txt")

    wav = os.path.join(work_dir, "audio16k.wav")
    extract_wav(ffmpeg, args.input, wav)

    segs = transcribe(wav, cfg)
    if not segs:
        die("Whisper не распознал ни одного куска речи (пустая расшифровка)", 4)

    write_srt(segs, srt_path)
    write_txt(segs, txt_path)

    duration = max((s["end"] for s in segs), default=0.0)
    dupes = find_dupes(segs, cfg)

    machine = {
        "input": args.input,
        "model": cfg["model"],
        "settings": {
            "prefix_words": cfg["prefix_words"],
            "fillers": cfg["fillers"],
        },
        "duration_sec": round(duration, 2),
        "segments_count": len(segs),
        "dupes_found": len(dupes),
        "cut": False,
        "dupes": dupes,
    }
    with open(dupes_json, "w", encoding="utf-8") as f:
        json.dump(machine, f, ensure_ascii=False, indent=2)
        f.write("\n")

    report = build_report(args.input, srt_path, txt_path, segs, dupes, cfg, duration)
    with open(dupes_txt, "w", encoding="utf-8") as f:
        f.write(report + "\n")

    wrote = write_pipeline(out_dir, {
        "dupes_found": len(dupes), "cut": False,
        "transcript_srt": os.path.basename(srt_path),
        "transcript_txt": os.path.basename(txt_path),
    })

    print(report)
    print(f"\nФайлы: {srt_path}")
    print(f"       {txt_path}")
    print(f"       {dupes_json}")
    print(f"       {dupes_txt}")
    print(f"pipeline.json: {'обновлён (current.dupes)' if wrote else 'не трогал (нет current с этим id)'}")


if __name__ == "__main__":
    main()
