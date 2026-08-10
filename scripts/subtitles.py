#!/usr/bin/env python3
"""
subtitles.py — субтитры в кадр (вжигаются в картинку).

Вход:  outputs/ready/[id]/clip_color.mp4
       outputs/ready/[id]/transcript.json  (пословные метки от find_dupes.py —
       НЕ распознаём второй раз)
Выход: outputs/ready/[id]/clip_subs.mp4

ЧТО ДЕЛАЕТ:
  - группирует слова по 3–5 на экран (words_per_screen), по пословным меткам;
  - каждая группа появляется по мере речи (от начала первого слова до конца
    последнего);
  - крупный белый текст с чёрной обводкой, внизу кадра, с отступом от края —
    читается на телефоне на любом фоне;
  - собирает субтитры в ASS (полный контроль шрифта/размера/обводки/позиции)
    и ВЖИГАЕТ в картинку через ffmpeg (libass). Звук не трогаем.

Размеры — в ПРОЦЕНТАХ от высоты кадра, чтобы одинаково читалось на любом
разрешении (горизонталь/вертикаль). Все настройки — presets/subtitles.json.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "presets", "subtitles.json")

DEFAULTS = {
    "font": "DejaVu Sans",
    "bold": True,
    "font_size_pct": 7.5,       # высота шрифта в % от высоты кадра (с запасом для телефона)
    "text_color": "FFFFFF",     # белый текст (RRGGBB)
    "outline_color": "000000",  # чёрная обводка (RRGGBB)
    "outline_pct": 0.5,         # толщина обводки в % от высоты кадра
    "shadow_pct": 0.0,          # тень в % от высоты кадра
    "margin_bottom_pct": 12,    # отступ снизу в % от высоты кадра
    "margin_side_pct": 6,       # боковые отступы в % от ширины кадра
    "words_per_screen": 4,      # слов на экран (3–5)
    "highlight": "on",          # подсветка звучащего слова: on/off
    "highlight_color": "FFFF00",  # цвет подсветки (RRGGBB), по умолчанию жёлтый
}


def die(msg, code):
    print(msg, file=sys.stderr)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"subtitles: {msg}\n")
    except OSError:
        pass
    sys.exit(code)


def load_config():
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
            status = "ok (presets/subtitles.json)"
        except Exception:
            cfg = dict(DEFAULTS)
            status = "битый файл, взял умолчания"
    try:
        cfg["words_per_screen"] = max(1, int(cfg["words_per_screen"]))
    except (TypeError, ValueError):
        cfg["words_per_screen"] = DEFAULTS["words_per_screen"]
    cfg["highlight_on"] = str(cfg.get("highlight", "on")).strip().lower() not in ("off", "false", "0", "no", "нет")
    cfg["_config_status"] = status
    return cfg


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def video_size(ffprobe, path):
    rc, out, _ = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path,
    ])
    if rc != 0 or "x" not in out:
        raise RuntimeError("не удалось прочитать размер видео")
    w, h = out.strip().split("x")[:2]
    return int(w), int(h)


def ass_color(rrggbb):
    """RRGGBB → ASS &H00BBGGRR (непрозрачный)."""
    s = str(rrggbb).strip().lstrip("#")
    if len(s) != 6:
        s = "FFFFFF"
    rr, gg, bb = s[0:2], s[2:4], s[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def ass_time(t):
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - 3600 * h - 60 * m
    return f"{h:d}:{m:02d}:{s:05.2f}"


def esc(text):
    """Экранирование для строки Dialogue в ASS."""
    return (text.replace("\\", "").replace("{", "(").replace("}", ")")
            .replace("\n", " ").strip())


def make_groups(segments, n):
    """
    Слова → группы по n на экран (по пословным меткам). Каждая группа — список
    слов [{word,start,end}]. Если пословных меток нет, раскидываем слова куска
    по его времени равномерно.
    """
    groups = []
    for seg in segments:
        words = seg.get("words") or []
        clean = [{"word": str(w["word"]).strip(),
                  "start": float(w["start"]), "end": float(w["end"])}
                 for w in words
                 if w.get("word") and str(w["word"]).strip()
                 and w.get("start") is not None and w.get("end") is not None]
        if clean:
            for i in range(0, len(clean), n):
                grp = clean[i:i + n]
                if grp:
                    groups.append(grp)
        else:
            toks = str(seg.get("text", "")).split()
            if not toks:
                continue
            s0, e0 = float(seg["start"]), float(seg["end"])
            span = max(e0 - s0, 0.01)
            m = len(toks)
            synth = [{"word": t,
                      "start": s0 + (k / m) * span,
                      "end": s0 + ((k + 1) / m) * span}
                     for k, t in enumerate(toks)]
            for i in range(0, m, n):
                grp = synth[i:i + n]
                if grp:
                    groups.append(grp)
    groups.sort(key=lambda g: g[0]["start"])
    return groups


def highlight_line(group, j, hl_tag, base_tag):
    """Строка группы: слово j — цветом подсветки, остальные — базовым."""
    parts = []
    for idx, w in enumerate(group):
        wt = esc(w["word"])
        if idx == j:
            parts.append(f"{hl_tag}{wt}{base_tag}")
        else:
            parts.append(wt)
    return " ".join(parts)


def make_events(groups, cfg):
    """
    Из групп собрать события субтитров [start, end, text].
    highlight on → на каждое слово своё событие (текущее слово — жёлтым),
    подсветка «переезжает» от слова к слову. off → одно событие на группу.
    """
    hl_tag = "{\\c" + ass_color(cfg["highlight_color"]) + "}"
    base_tag = "{\\c" + ass_color(cfg["text_color"]) + "}"
    events = []
    for grp in groups:
        if cfg["highlight_on"] and len(grp) >= 1:
            for j, w in enumerate(grp):
                start = w["start"]
                # текущее слово подсвечено, пока не началось следующее (строка не мигает)
                end = grp[j + 1]["start"] if j + 1 < len(grp) else w["end"]
                if end <= start:
                    end = start + 0.15
                events.append([start, end, highlight_line(grp, j, hl_tag, base_tag)])
        else:
            start = grp[0]["start"]
            end = grp[-1]["end"]
            if end <= start:
                end = start + 0.15
            events.append([start, end, " ".join(esc(w["word"]) for w in grp)])
    events.sort(key=lambda e: e[0])
    # не даём соседним событиям налезать друг на друга
    for i in range(len(events) - 1):
        if events[i][1] > events[i + 1][0]:
            events[i][1] = max(events[i][0] + 0.05, events[i + 1][0] - 0.02)
    return events


def build_ass(events, cfg, w, h, path):
    fs = max(1, round(h * float(cfg["font_size_pct"]) / 100.0))
    outline = max(0, round(h * float(cfg["outline_pct"]) / 100.0))
    shadow = max(0, round(h * float(cfg["shadow_pct"]) / 100.0))
    mv = max(0, round(h * float(cfg["margin_bottom_pct"]) / 100.0))
    ms = max(0, round(w * float(cfg["margin_side_pct"]) / 100.0))
    bold = -1 if cfg.get("bold") else 0
    primary = ass_color(cfg["text_color"])
    outline_c = ass_color(cfg["outline_color"])

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{cfg['font']},{fs},{primary},&H000000FF,{outline_c},"
        f"&H00000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"{ms},{ms},{mv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for start, end, text in events:
            # text уже подготовлен make_events (экранирован, с тегами подсветки) — не трогаем
            f.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}\n")
    return {"font_px": fs, "outline_px": outline, "margin_bottom_px": mv}


def burn(ffmpeg, src, ass_path, out):
    # путь к ass в фильтре: экранируем спецсимволы фильтрографа
    p = ass_path.replace("\\", "/").replace(":", r"\:")
    rc, _, err = run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", src, "-vf", f"ass={p}",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-c:a", "copy", "-movflags", "+faststart", out,
    ])
    if rc != 0:
        raise RuntimeError(f"ffmpeg вжигание субтитров: {err.strip()[-400:]}")


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
    cur["subtitles"] = info
    with open(pipeline, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="outputs/ready/[id]/clip_color.mp4")
    ap.add_argument("--output", help="куда сохранить (по умолчанию clip_subs.mp4 рядом)")
    ap.add_argument("--transcript", help="transcript.json (по умолчанию рядом с input)")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        die("нет ffmpeg/ffprobe → blocked", 3)
    if not os.path.exists(args.input):
        die(f"нет входного файла: {args.input}", 2)

    out_dir = os.path.dirname(os.path.abspath(args.input)) or "."
    transcript = args.transcript or os.path.join(out_dir, "transcript.json")
    if not os.path.exists(transcript):
        die(f"нет расшифровки {transcript} — сначала прогнать find_dupes.py "
            "(распознаём только там, второй раз не распознаём) → blocked", 4)
    output = args.output or os.path.join(out_dir, "clip_subs.mp4")
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    cfg = load_config()
    with open(transcript, encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", [])
    if not segments:
        die("в transcript.json нет сегментов", 4)

    w, h = video_size(ffprobe, args.input)
    groups = make_groups(segments, cfg["words_per_screen"])
    events = make_events(groups, cfg)
    if not events:
        die("не удалось собрать субтитры из расшифровки", 4)

    ass_path = os.path.join(work_dir, "subs.ass")
    style = build_ass(events, cfg, w, h, ass_path)
    burn(ffmpeg, args.input, ass_path, output)

    info = {"groups": len(groups), "events": len(events),
            "words_per_screen": cfg["words_per_screen"],
            "highlight": cfg["highlight_on"], "highlight_color": cfg["highlight_color"],
            "font_px": style["font_px"], "outline_px": style["outline_px"],
            "margin_bottom_px": style["margin_bottom_px"], "resolution": f"{w}x{h}"}
    wrote = write_pipeline(out_dir, info)

    print("=== ОТЧЁТ subtitles ===")
    print(f"Конфиг:       {cfg['_config_status']}")
    print(f"Вход:         {args.input}  ({w}x{h})")
    print(f"Расшифровка:  {transcript} (переиспользована, не распознавал заново)")
    print(f"Субтитров:    {len(groups)} строк по ≤{cfg['words_per_screen']} слов")
    print(f"Подсветка:    {'ВКЛ, цвет #' + cfg['highlight_color'] + ' (жёлтым звучащее слово)' if cfg['highlight_on'] else 'выкл'}"
          + (f", событий {len(events)}" if cfg['highlight_on'] else ""))
    print(f"Шрифт:        {cfg['font']}"
          f"{' bold' if cfg.get('bold') else ''}, {style['font_px']} px "
          f"({cfg['font_size_pct']}% высоты)")
    print(f"Обводка:      {style['outline_px']} px, цвет #{cfg['outline_color']}, текст #{cfg['text_color']}")
    print(f"Положение:    внизу, отступ {style['margin_bottom_px']} px от края")
    print(f"Файл:         {output}")
    print(f"pipeline.json: {'обновлён (current.subtitles)' if wrote else 'не трогал (нет current с этим id)'}")
    print("=======================")


if __name__ == "__main__":
    main()
