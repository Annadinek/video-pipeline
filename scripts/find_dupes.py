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
  3. Ищет дубли: соседние куски с похожим текстом.
  4. ПОКА НЕ РЕЖЕТ. Только печатает и сохраняет список найденного:
       outputs/ready/[id]/dupes.json         (для машины)
       outputs/ready/[id]/dupes_report.txt   (для человека)

КАК ОТЛИЧАЕМ ДУБЛЬ ОТ РИТОРИЧЕСКОГО ПРИЁМА (важно!):
  Дубль   — повторяется ОДНА И ТА ЖЕ фраза целиком, подряд, с коротким разрывом.
  Приём   — повторяется только НАЧАЛО, а концовка расходится
            («снова те же мысли» / «снова те же люди»).
  Поэтому:
   - сравниваем фразу ЦЕЛИКОМ (расстояние Левенштейна по всей строке), не начало;
   - дополнительно требуем, чтобы СОВПАДАЛИ КОНЦОВКИ (последнее слово).
     У приёма концовки разные — он отсеивается.
   - сомневаемся → НЕ считаем дублем. Лучше пропустить, чем вырезать живое.
     Плюс ничего не режем — список смотрит человек.

Настройки — presets/dupes.json (сломан/нет файла → умолчания, не падаем).
Сравнение: нижний регистр, без пунктуации, без слов-паразитов (ну, это, вот,
как бы, значит), затем расстояние Левенштейна. Лемматизации нет — намеренно.
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
    "similarity_threshold": 0.75,  # совпадение текста от 75%
    "max_gap_sec": 1.5,          # разрыв между повторами не больше 1.5 с
    "min_words": 3,              # кусок короче 3 слов не считаем
    "end_word_ratio": 0.8,       # насколько должны совпасть КОНЦОВКИ (последнее слово)
    "fillers": ["как бы", "ну", "это", "вот", "значит"],
}
BEAM_SIZE = 5


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
    cfg["_config_status"] = status
    return cfg


# --- нормализация и сравнение текста ---
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


def levenshtein(a, b):
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def ratio(a, b):
    """Похожесть 0..1 по расстоянию Левенштейна (по символам)."""
    if not a and not b:
        return 1.0
    m = max(len(a), len(b))
    return 1.0 - levenshtein(a, b) / m if m else 1.0


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
def find_dupes(segs, cfg):
    """
    Соседние куски с похожим текстом. Условия дубля:
      - разрыв между ними ≤ max_gap_sec;
      - в каждом ≥ min_words слов (после чистки);
      - похожесть всей фразы ≥ similarity_threshold;
      - КОНЦОВКИ совпадают (последнее слово) ≥ end_word_ratio — иначе это приём.
    Пустые/паразитные куски прозрачны для соседства, но их время входит в разрыв.
    """
    fillers = cfg["fillers"]
    thr = float(cfg["similarity_threshold"])
    max_gap = float(cfg["max_gap_sec"])
    min_words = int(cfg["min_words"])
    end_ratio = float(cfg["end_word_ratio"])

    for s in segs:
        s["norm"] = normalize(s["text"], fillers)
        s["words"] = s["norm"].split()

    dupes = []
    n = len(segs)
    i = 0
    while i < n - 1:
        a = segs[i]
        if len(a["words"]) < min_words:
            i += 1
            continue
        # ближайший непустой сосед (паразитные куски пропускаем)
        j = i + 1
        while j < n and len(segs[j]["words"]) == 0:
            j += 1
        if j >= n:
            break
        b = segs[j]
        gap = round(b["start"] - a["end"], 2)
        matched = False
        if len(b["words"]) >= min_words and gap <= max_gap:
            sim = ratio(a["norm"], b["norm"])
            end_sim = ratio(a["words"][-1], b["words"][-1])
            if sim >= thr and end_sim >= end_ratio:
                dupes.append({
                    "index": len(dupes) + 1,
                    "start": round(a["start"], 2),
                    "end": round(b["end"], 2),
                    "gap": gap,
                    "similarity": round(sim, 3),
                    "end_similarity": round(end_sim, 3),
                    "text_1": a["text"], "text_2": b["text"],
                    "norm_1": a["norm"], "norm_2": b["norm"],
                })
                matched = True
        i = j if matched else i + 1
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
    L.append(f"Пороги:       похожесть ≥{int(cfg['similarity_threshold']*100)}%, "
             f"разрыв ≤{cfg['max_gap_sec']} с, минимум {cfg['min_words']} слова, "
             f"концовки совпадают ≥{int(cfg['end_word_ratio']*100)}%")
    L.append(f"Найдено дублей: {len(dupes)}")
    L.append("")
    if not dupes:
        L.append("Дублей не нашёл. Либо их нет, либо повторы не дотянули до порогов")
        L.append("(тогда ослабь similarity_threshold или увеличь max_gap_sec в presets/dupes.json).")
    for d in dupes:
        L.append(f"[{d['index']}] {fmt_mmss(d['start'])} – {fmt_mmss(d['end'])}   "
                 f"(похожесть {int(d['similarity']*100)}%, концовки {int(d['end_similarity']*100)}%, "
                 f"разрыв {d['gap']} с)")
        L.append(f"    1-я: «{d['text_1']}»")
        L.append(f"    2-я: «{d['text_2']}»")
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
            "similarity_threshold": cfg["similarity_threshold"],
            "max_gap_sec": cfg["max_gap_sec"],
            "min_words": cfg["min_words"],
            "end_word_ratio": cfg["end_word_ratio"],
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
