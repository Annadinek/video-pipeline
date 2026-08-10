#!/usr/bin/env python3
"""
find_dupes.py — поиск и вырезание дублей (запнулась → повторила фразу заново).

Место в конвейере: 02a-dupes, ПЕРЕД обработкой звука/цвета — чтобы не красить
и не стабилизировать то, что потом вырежем.

ВХОД: outputs/ready/[id]/clip.mp4  (уже скачанный файл; сам НИЧЕГО не качает).
      Ссылку на YouTube используем только для отдельного теста (dupes-test.yml).

ЧТО ДЕЛАЕТ:
  1. Распознаёт речь через Whisper (faster-whisper) с метками времени по СЛОВАМ.
  2. Сохраняет расшифровку в ДВА файла — переиспользуем для субтитров,
     распознавать второй раз не будем:
       outputs/ready/[id]/transcript.srt  (с метками времени)
       outputs/ready/[id]/transcript.txt  (сплошным текстом)
  3. Ищет дубли: фразу, сказанную дважды ПОДРЯД, слово в слово.
  4. Если cut=on — РЕЖЕТ, но вырезанное НЕ выбрасывает:
       outputs/ready/[id]/clip_nodupes.mp4        — обрезанный
       outputs/ready/[id]/clip.mp4                — ИСХОДНИК, не трогаем
       outputs/ready/[id]/cut_out/<время>.mp4     — каждый вырезанный кусок
     Всегда пишет отчёт:
       outputs/ready/[id]/dupes.json         (для машины)
       outputs/ready/[id]/dupes_report.txt   (для человека)

КАК НАХОЖУ ДУБЛЬ (по словам, без процентов):
  Дубль — фраза, сказанная ДВАЖДЫ ПОДРЯД, слово в слово. Ищу повтор, который
  идёт подряд (между версиями 0 слов после чистки от паразитов), совпадает
  слово в слово и длиной ≥ prefix_words слов. Проценты не использую — на
  коротких фразах обманывают. Меряю в СЛОВАХ, не в секундах и не в символах.
  Приём («в каждом ВДОХЕ есть смысл, в каждом ВЗГЛЯДЕ есть смысл») так не
  ловится: между повторами стоит отличающееся слово. Сомневаюсь — не режу.

ЧТО РЕЖУ: только сам повтор — убираю ПЕРВУЮ версию (и паузу перед второй),
оставляю вторую (обычно более полную). Режу по границам слов (word_timestamps).
Исходник не трогаю никогда. Whisper распознаёт немного по-разному от запуска
к запуску — поэтому вырезанное сохраняю отдельными файлами, чтобы можно было
послушать и проверить.

Настройки — presets/dupes.json (сломан/нет файла → умолчания, не падаем).
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
    "prefix_words": 3,           # минимум слов в повторе; фраза короче не считается
    "cut": "on",                 # on — режем; off — только находим и показываем
    "fillers": ["как бы", "ну", "это", "вот", "значит"],
}
BEAM_SIZE = 5
MAX_RUN = 40         # верхний предел длины повтора (слов) — защита от долгого поиска
ZONE_GAP_WORDS = 12  # цепочка перезапусков: макс. разрыв в словах между попытками


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"find_dupes: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def is_on(v):
    return str(v).strip().lower() in ("on", "true", "1", "yes", "да")


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
    cfg["cut_on"] = is_on(cfg.get("cut", "on"))
    cfg["_config_status"] = status
    return cfg


# --- нормализация текста ---
def norm_words(text):
    """Нижний регистр, ё→е, без пунктуации → список слов."""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return t.split()


def normalize(text, fillers):
    """Строку целиком: чистим и убираем паразитов (для расшифровки/фолбэка)."""
    words = norm_words(text)
    return " ".join(drop_fillers([{"w": w} for w in words], fillers)[0])


def drop_fillers(tokens, fillers):
    """
    Убрать слова-паразиты из списка токенов [{w,...}].
    Многословные («как бы») — по последовательности; однословные — по слову.
    Вернуть (список_слов, список_токенов_без_паразитов).
    """
    multis = [tuple(norm_words(f)) for f in fillers if len(norm_words(f)) > 1]
    singles = {norm_words(f)[0] for f in fillers if len(norm_words(f)) == 1}
    words = [t["w"] for t in tokens]
    keep = [True] * len(words)
    # многословные последовательности
    for mf in multis:
        n = len(mf)
        for i in range(len(words) - n + 1):
            if all(keep[i + j] for j in range(n)) and tuple(words[i:i + n]) == mf:
                for j in range(n):
                    keep[i + j] = False
    # однословные
    for i, w in enumerate(words):
        if w in singles:
            keep[i] = False
    kept_tokens = [tokens[i] for i in range(len(tokens)) if keep[i]]
    return [t["w"] for t in kept_tokens], kept_tokens


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


def fmt_file(t):
    """Имя по времени, только латиница/цифры: 00m47.6s."""
    if t < 0:
        t = 0.0
    m = int(t // 60)
    s = t - 60 * m
    return f"{m:02d}m{s:04.1f}s"


# --- ffmpeg ---
def ffprobe_duration(ffprobe, path):
    rc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(rc.stdout.strip())
    except ValueError:
        return 0.0


def extract_wav(ffmpeg, src, wav):
    rc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"не удалось извлечь звук: {rc.stderr.strip()[-300:]}")


def ff_clip(ffmpeg, src, a, b, out):
    """Вырезать [a,b] c перекодировкой (точно по времени, не по опорным кадрам)."""
    rc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", src, "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "192k", out],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"ffmpeg отрезок [{a:.1f},{b:.1f}]: {rc.stderr.strip()[-300:]}")


def ff_keep_concat(ffmpeg, src, keeps, out):
    """
    Собрать обрезанный файл из оставляемых интервалов ОДНИМ проходом через
    filter_complex trim+concat. Рез встык, без переходов/затемнений: concat
    просто стыкует кадры, ffmpeg сам ничего не добавляет. Один прогон
    перекодировки (а не поштучная склейка — она давала артефакты на стыках).
    """
    filt = []
    labels = []
    for i, (a, b) in enumerate(keeps):
        filt.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        filt.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    filt.append("".join(labels) + f"concat=n={len(keeps)}:v=1:a=1[outv][outa]")
    rc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", src, "-filter_complex", ";".join(filt),
         "-map", "[outv]", "-map", "[outa]",
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "192k", out],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"ffmpeg trim+concat: {rc.stderr.strip()[-400:]}")


# --- Whisper ---
def transcribe(wav, cfg):
    """Сегменты [{start,end,text,words:[{w,start,end}]}] через faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die("нет faster-whisper (pip install faster-whisper) → blocked", 3)
    model = WhisperModel(cfg["model"], device="cpu", compute_type=cfg["compute_type"])
    segments, _info = model.transcribe(
        wav, language=cfg["language"], vad_filter=bool(cfg["vad_filter"]),
        beam_size=BEAM_SIZE, word_timestamps=True,
    )
    out = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        words = []
        for w in (seg.words or []):
            words.append({
                "w": w.word,
                "start": float(w.start) if w.start is not None else None,
                "end": float(w.end) if w.end is not None else None,
            })
        out.append({"start": float(seg.start), "end": float(seg.end),
                    "text": text, "words": words})
    return out


# --- запись расшифровки ---
def write_srt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{s['text']}\n\n")


def write_txt(segs, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(" ".join(s["text"] for s in segs).strip() + "\n")


def write_transcript_json(segs, path, cfg, duration):
    """Расшифровка с пословными метками — переиспользуется субтитрами (03b), не распознаём второй раз."""
    data = {
        "language": cfg["language"],
        "duration": round(duration, 3),
        "segments": [
            {
                "start": round(s["start"], 3),
                "end": round(s["end"], 3),
                "text": s["text"],
                "words": [
                    {"word": w["w"], "start": w["start"], "end": w["end"]}
                    for w in (s.get("words") or [])
                ],
            }
            for s in segs
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# --- токены со временем по словам ---
def build_tokens(segs, fillers):
    """
    Один поток слов по всему ролику (после чистки), с временем каждого слова
    (по word_timestamps). Так дубль ловится одинаково и внутри куска, и на
    стыке двух кусков — важно, что слова идут ПОДРЯД.
    """
    raw = []
    for s in segs:
        ws = s.get("words") or []
        if ws:
            for wd in ws:
                for sub in norm_words(wd["w"]):
                    raw.append({"w": sub, "start": wd["start"], "end": wd["end"],
                                "seg_text": s["text"], "seg_start": s["start"],
                                "seg_end": s["end"]})
        else:  # нет пословных меток — раскидываем слова по времени куска
            words = norm_words(s["text"])
            n = len(words)
            span = max(s["end"] - s["start"], 0.0)
            for k, w in enumerate(words):
                raw.append({"w": w,
                            "start": s["start"] + (k / n) * span if n else s["start"],
                            "end": s["start"] + ((k + 1) / n) * span if n else s["end"],
                            "seg_text": s["text"], "seg_start": s["start"],
                            "seg_end": s["end"]})
    _, tokens = drop_fillers(raw, fillers)
    # подстраховка: если у слова нет времени — берём границы куска
    for t in tokens:
        if t["start"] is None:
            t["start"] = t["seg_start"]
        if t["end"] is None:
            t["end"] = t["seg_end"]
    return tokens


def detect_repeats(tokens, need):
    """
    Найти повторы: позиция p, где words[p-L:p] == words[p:p+L] (повтор впритык),
    берём максимальную L; если L ≥ need — дубль. Сами интервалы резки считает
    compute_zones (по зонам перезапусков), тут — только факт повтора.
    """
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
            seg_texts = []
            for tk in first + second:
                if tk["seg_text"] not in seg_texts:
                    seg_texts.append(tk["seg_text"])
            dupes.append({
                "index": len(dupes) + 1,
                "words": L,
                "phrase": " ".join(W[p:p + L]),
                "first_idx": p - L,
                "second_idx": p,
                "first_start": round(first[0]["start"], 3),
                "second_start": round(second[0]["start"], 3),
                "context": seg_texts,
            })
            p += L  # перешагнуть вторую версию
        else:
            p += 1
    return dupes


def compute_zones(tokens, dupes):
    """
    Собрать ЗОНЫ перезапусков и вернуть интервалы времени для сплошного реза.
    Правило (по Анне): если идёт цепочка перезапусков — режем от конца последней
    нормальной фразы (= начало первой попытки) до начала последней полной попытки.
    Одним куском: паузы и оборванные слоги внутри зоны уходят вместе со всем,
    потому что режем по ВРЕМЕНИ, а не по словам.

    Как строим зону: якорь = первые 2 слова повторяемой фразы. Находим все места,
    где начинается попытка (якорь), группируем близкие (разрыв ≤ ZONE_GAP_WORDS
    слов) в цепочку. Оставляем цепочку, где есть хотя бы 2 попытки и хотя бы один
    подтверждённый повтор. Режем [начало первой попытки, начало последней].
    Последнюю (полную) попытку оставляем.
    """
    if not dupes:
        return []
    words = [t["w"] for t in tokens]
    n = len(tokens)
    seed_positions = set()
    for d in dupes:
        seed_positions.add(d["first_idx"])
        seed_positions.add(d["second_idx"])

    anchors = {tuple(d["phrase"].split()[:2]) for d in dupes if len(d["phrase"].split()) >= 2}
    starts = set()
    for a in anchors:
        la = len(a)
        for i in range(n - la + 1):
            if tuple(words[i:i + la]) == a:
                starts.add(i)
    if not starts:
        return []

    idxs = sorted(starts)
    groups = [[idxs[0]]]
    for i in idxs[1:]:
        if i - groups[-1][-1] <= ZONE_GAP_WORDS:
            groups[-1].append(i)
        else:
            groups.append([i])

    zones = []
    for g in groups:
        if len(g) < 2:
            continue
        if not any(g[0] <= sp <= g[-1] for sp in seed_positions):
            continue  # в зоне нет подтверждённого повтора — не режем
        cut_from = tokens[g[0]]["start"]        # начало первой попытки
        cut_to = tokens[g[-1]]["start"]         # начало последней (полной) попытки
        if cut_to > cut_from:
            zones.append({"from": round(cut_from, 3), "to": round(cut_to, 3),
                          "attempts": len(g)})
    return zones


def find_dupes(segs, cfg):
    """Обёртка для тестов: вернуть найденные повторы."""
    tokens = build_tokens(segs, cfg["fillers"])
    return detect_repeats(tokens, int(cfg["prefix_words"]))


# --- резка ---
def merge_intervals(intervals):
    """Слить пересекающиеся/касающиеся интервалы [(a,b)] → отсортированный список."""
    ivs = sorted((a, b) for a, b in intervals if b > a)
    merged = []
    for a, b in ivs:
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def do_cut(ffmpeg, ffprobe, src, zones, out_trimmed, cut_out_dir):
    """Вырезать зоны перезапусков сплошными кусками: сохранить каждый в cut_out/,
    собрать обрезанный файл одним проходом (рез встык, без затемнений).
    Возвращает (duration_before, duration_after, cut_seconds, cut_pieces)."""
    dur = ffprobe_duration(ffprobe, src)
    cuts = merge_intervals([(z["from"], z["to"]) for z in zones])
    cuts = [(max(0.0, a), min(dur, b)) for a, b in cuts if min(dur, b) > max(0.0, a)]

    os.makedirs(cut_out_dir, exist_ok=True)
    pieces = []
    for a, b in cuts:
        name = f"{fmt_file(a)}__{fmt_file(b)}.mp4"
        ff_clip(ffmpeg, src, a, b, os.path.join(cut_out_dir, name))
        pieces.append({"from": round(a, 2), "to": round(b, 2),
                       "seconds": round(b - a, 2), "file": f"cut_out/{name}"})

    # оставляемые интервалы (дополнение к вырезанным)
    keeps = []
    prev = 0.0
    for a, b in cuts:
        if a > prev + 1e-3:
            keeps.append((prev, a))
        prev = b
    if dur - prev > 1e-3:
        keeps.append((prev, dur))

    if not cuts or not keeps:
        shutil.copyfile(src, out_trimmed)
    else:
        ff_keep_concat(ffmpeg, src, keeps, out_trimmed)

    cut_seconds = round(sum(p["seconds"] for p in pieces), 2)
    dur_after = round(dur - cut_seconds, 2)
    return round(dur, 2), dur_after, cut_seconds, pieces


# --- отчёты ---
def build_report(input_path, srt_path, txt_path, segs, dupes, cfg, duration, cutinfo):
    L = []
    L.append("=== ДУБЛИ ===")
    L.append(f"Файл:         {input_path}")
    L.append(f"Модель:       faster-whisper {cfg['model']} ({cfg['compute_type']}, "
             f"язык {cfg['language']}, VAD {'вкл' if cfg['vad_filter'] else 'выкл'})")
    L.append(f"Конфиг:       {cfg['_config_status']}")
    L.append(f"Расшифровка:  {os.path.basename(srt_path)} + {os.path.basename(txt_path)} "
             "(переиспользуем для субтитров)")
    L.append(f"Правило:      фраза сказана дважды ПОДРЯД, слово в слово, "
             f"минимум {cfg['prefix_words']} слова (меряем в словах)")
    L.append(f"Резка (cut):  {'ВКЛ' if cfg['cut_on'] else 'выкл — только показываю'}")
    L.append(f"Найдено дублей: {len(dupes)}")
    if cutinfo:
        L.append(f"Вырезано:     {cutinfo['cut_count']} кусков, {cutinfo['cut_seconds']} сек")
        L.append(f"Длительность: было {fmt_mmss(cutinfo['duration_before'])} → "
                 f"стало {fmt_mmss(cutinfo['duration_after'])}")
        L.append(f"Обрезанный:   {os.path.basename(cutinfo['trimmed'])} "
                 "(исходник clip.mp4 не тронут)")
        L.append("Вырезанное:   в папке cut_out/ — можно послушать, что убрали")
    else:
        L.append(f"Длительность: {fmt_mmss(duration)} мин, кусков распознано: {len(segs)}")
    L.append("")
    if not dupes:
        L.append("Дублей не нашёл. Значит фраз, сказанных дважды подряд слово в слово, нет")
        L.append(f"(если повтор короче {cfg['prefix_words']} слов — не считаю; "
                 "поменяй prefix_words в presets/dupes.json).")
    pieces = (cutinfo or {}).get("pieces", [])
    if pieces:
        L.append("ВЫРЕЗАННЫЕ КУСКИ (зона перезапусков целиком: повторы + паузы + обрывки):")
        for j, pc in enumerate(pieces, 1):
            phrases = [d["phrase"] for d in dupes
                       if pc["from"] - 0.2 <= d["first_start"] <= pc["to"] + 0.2]
            L.append(f"[{j}] {fmt_mmss(pc['from'])}–{fmt_mmss(pc['to'])} "
                     f"({pc['seconds']} сек) → {pc['file']}")
            if phrases:
                L.append(f"    повторы внутри: «" + "» / «".join(phrases) + "»")
        L.append("")
    if dupes:
        L.append("НАЙДЕННЫЕ ПОВТОРЫ (слово в слово подряд):")
        for d in dupes:
            L.append(f"  • {d['words']} слов: «{d['phrase']}»")
        L.append("")
    if cfg["cut_on"]:
        L.append("Резал зону целиком, встык, без затемнений. Вырезанное сохранено —")
        L.append("послушай cut_out/ и стыки в clip_nodupes.mp4; если не так — поправлю.")
    else:
        L.append("Ничего не вырезано (cut выкл). Посмотри список и скажи, что резать.")
    L.append("=" * 44)
    return "\n".join(L)


def write_pipeline(out_dir, info):
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
    cur["dupes"] = info
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip.mp4 (уже скачан)")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)
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
    trimmed = os.path.join(out_dir, "clip_nodupes.mp4")
    cut_out_dir = os.path.join(out_dir, "cut_out")

    wav = os.path.join(work_dir, "audio16k.wav")
    extract_wav(ffmpeg, args.input, wav)

    segs = transcribe(wav, cfg)
    if not segs:
        die("Whisper не распознал ни одного куска речи (пустая расшифровка)", 4)

    duration = max((s["end"] for s in segs), default=0.0)
    write_srt(segs, srt_path)
    write_txt(segs, txt_path)
    write_transcript_json(segs, os.path.join(out_dir, "transcript.json"), cfg, duration)

    tokens = build_tokens(segs, cfg["fillers"])
    dupes = detect_repeats(tokens, int(cfg["prefix_words"]))
    zones = compute_zones(tokens, dupes)

    cutinfo = None
    if cfg["cut_on"] and zones:
        db, da, cs, pieces = do_cut(ffmpeg, ffprobe, args.input, zones,
                                    trimmed, cut_out_dir)
        cutinfo = {"duration_before": db, "duration_after": da, "cut_seconds": cs,
                   "cut_count": len(pieces), "pieces": pieces, "trimmed": trimmed}
    elif cfg["cut_on"]:
        # дублей нет — обрезанный = копия исходника, чтобы дальше по конвейеру был один вход
        shutil.copyfile(args.input, trimmed)
        cutinfo = {"duration_before": round(duration, 2), "duration_after": round(duration, 2),
                   "cut_seconds": 0.0, "cut_count": 0, "pieces": [], "trimmed": trimmed}

    machine = {
        "input": args.input,
        "model": cfg["model"],
        "settings": {"prefix_words": cfg["prefix_words"], "cut": cfg["cut_on"],
                     "fillers": cfg["fillers"]},
        "duration_sec": round(duration, 2),
        "segments_count": len(segs),
        "dupes_found": len(dupes),
        "cut": bool(cutinfo),
        "dupes": dupes,
    }
    if cutinfo:
        machine.update({
            "trimmed_file": os.path.basename(trimmed),
            "duration_before": cutinfo["duration_before"],
            "duration_after": cutinfo["duration_after"],
            "cut_seconds": cutinfo["cut_seconds"],
            "cut_count": cutinfo["cut_count"],
            "cut_pieces": cutinfo["pieces"],
        })
    with open(dupes_json, "w", encoding="utf-8") as f:
        json.dump(machine, f, ensure_ascii=False, indent=2)
        f.write("\n")

    report = build_report(args.input, srt_path, txt_path, segs, dupes, cfg, duration, cutinfo)
    with open(dupes_txt, "w", encoding="utf-8") as f:
        f.write(report + "\n")

    pinfo = {"dupes_found": len(dupes), "cut": bool(cutinfo),
             "transcript_srt": os.path.basename(srt_path),
             "transcript_txt": os.path.basename(txt_path)}
    if cutinfo:
        pinfo.update({"trimmed_file": os.path.basename(trimmed),
                      "cut_seconds": cutinfo["cut_seconds"], "cut_count": cutinfo["cut_count"]})
    wrote = write_pipeline(out_dir, pinfo)

    print(report)
    print(f"\nФайлы: {srt_path}")
    print(f"       {txt_path}")
    print(f"       {dupes_json}")
    print(f"       {dupes_txt}")
    if cutinfo:
        print(f"       {trimmed}  (обрезанный)")
        print(f"       {cut_out_dir}/  (вырезанные куски)")
    print(f"pipeline.json: {'обновлён (current.dupes)' if wrote else 'не трогал (нет current с этим id)'}")


if __name__ == "__main__":
    main()
