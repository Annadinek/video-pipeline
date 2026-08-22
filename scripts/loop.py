#!/usr/bin/env python3
"""
loop.py — двигает конвейер на один шаг.

Порядок:
0. Проверить ключи в окружении. Нет — записать в logs/errors.log и выйти с кодом 1.
1. Прочитать state/trigger.txt. STOP — выйти, записать в лог.
2. Прочитать state/pipeline.json.
3. current не пуст — продолжить с его этапа.
   Пуст — взять первое из queue.
   queue пуст — взять первую строку из inbox/links.txt.
   И там пусто — выйти, записать «нечего делать».
4. Открыть stages/NN-name/PROMPT.md текущего этапа.
4.1 Прочитать state/test-mode.txt (ON — публикуем только в тест, OFF — боевой).
5. Выполнить этап. Максимум две попытки.
6. Успех — записать результат, увеличить номер этапа.
   Неудача дважды — в blocked, уведомление в Telegram, очистить current.
7. Этап 05-qa пропускать нельзя. qa.passed false — дальше не идти.
8. Дошли до конца — записать полную запись ролика в state/history.json, очистить current.
9. Записать в logs/.
10. Отчёт: ролик / этап / что сделано / что застряло / что дальше.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# --- пути ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
LOG_DIR = os.path.join(ROOT, "logs")
STAGES_DIR = os.path.join(ROOT, "stages")
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
READY_DIR = os.path.join(ROOT, "outputs", "ready")
INBOX_LINKS = os.path.join(ROOT, "inbox", "links.txt")

TRIGGER = os.path.join(STATE_DIR, "trigger.txt")
TEST_MODE = os.path.join(STATE_DIR, "test-mode.txt")
PIPELINE = os.path.join(STATE_DIR, "pipeline.json")
HISTORY = os.path.join(STATE_DIR, "history.json")

ERRORS_LOG = os.path.join(LOG_DIR, "errors.log")
API_LOG = os.path.join(LOG_DIR, "api-calls.log")
RETRIES_LOG = os.path.join(LOG_DIR, "retries.log")

# Порядок этапов — имена папок в stages/
# Новый порядок (решение Анны 2026-08-20):
# Всё делаем на длинном ГОРИЗОНТАЛЬНОМ: расшифровка → стабилизация → цвет →
# СВОИ субтитры → вставки/зумы/анимации (HyperFrames) → звук (HyperFrames) →
# СНЯТЬ субтитры (чистая версия) → Vizard режет вертикали со СВОИМИ субтитрами.
# Субтитры делаем ДВАЖДЫ: на горизонтали (наши) и на вертикалях (Vizard).
# Шум и обрезку пауз Анна убирает сама в CapCut ДО загрузки — этапа звука-шумодава
# в цепочке НЕТ (02b-audio выведен из списка, скрипт audio_clean.py оставлен).
STAGES = [
    "00-plan",
    "01-script",
    "02a-dupes",       # расшифровка (пословный transcript.json) на горизонтали
    "02c-stab",        # стабилизация
    "03-color",        # цвет и кожа
    "03b-subtitles",   # СВОИ субтитры — на ГОРИЗОНТАЛЬНОМ
    "03c-inserts",     # вставки, зумы, анимации — через HyperFrames (7–10 на видео)
    "03d-audio-hf",    # звук через HyperFrames (шум/обрезка — Анна в CapCut)
    "03e-strip-subs",  # снять субтитры → чистая версия под нарезку
    "03a-cut",         # Vizard режет вертикали со СВОИМИ субтитрами и подписями
    "04-caption",      # подписи
    "05-qa",           # проверка
    "06-publish",      # публикация длинного, затем коротких вертикальных
]
# 02b-audio НАМЕРЕННО не в списке: шум/обрезку Анна делает в CapCut до загрузки.
# Скрипт scripts/audio_clean.py НЕ удалён — просто не вызывается конвейером.

# ДВА КОНВЕЙЕРА, ОДИН РЕПОЗИТОРИЙ (см. CLAUDE.md). loop.py ведёт ТОЛЬКО конвейер
# YouTube. Второй конвейер (Instagram/TikTok) — всё с приставкой reels_/reels- —
# со своим запуском. Защита: любые reels-этапы отфильтровываются из STAGES, чтобы
# этот цикл их никогда не подхватил, даже если кто-то допишет их в список.
STAGES = [s for s in STAGES if not s.startswith("reels-")]

REQUIRED = [
    "VIZARDAI_API_KEY",
    "YT_CLIENT_ID",
    "YT_CLIENT_SECRET",
    "YT_REFRESH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
]


# --- журналы ---
def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_log(path, msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{_stamp()}] {msg}\n")


def log_error(msg):
    _write_log(ERRORS_LOG, msg)


def log_api(msg):
    _write_log(API_LOG, msg)


def log_retry(msg):
    _write_log(RETRIES_LOG, msg)


# --- чтение/запись состояния ---
def read_text(path, default=""):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def first_inbox_link():
    for line in read_text(INBOX_LINKS).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def report(video, stage, done, stuck, nxt):
    print("=== ОТЧЁТ LOOP ===")
    print(f"Ролик:      {video}")
    print(f"Этап:       {stage}")
    print(f"Сделано:    {done}")
    print(f"Застряло:   {stuck}")
    print(f"Дальше:     {nxt}")
    print("==================")


def _vid_dir(current):
    """Папка ролика outputs/ready/[id]/ (id или 'current')."""
    vid = str(current.get("id") or "current")
    d = os.path.join(READY_DIR, vid)
    os.makedirs(d, exist_ok=True)
    return d


def stage_command(stage, current):
    """
    argv для этапа. None — этап-пустышка (для готового видео план/скрипт/подписи
    делаются вне loop). Цепочка файлов в outputs/ready/[id]/.
    """
    d = _vid_dir(current)
    p = lambda name: os.path.join(d, name)
    S = lambda name: os.path.join(SCRIPTS_DIR, name)
    py = sys.executable
    clip, stab, color = p("clip.mp4"), p("clip_stab.mp4"), p("clip_color.mp4")
    subs, inserts, audio, clean = p("clip_subs.mp4"), p("clip_inserts.mp4"), p("clip_audio.mp4"), p("clip_clean.mp4")
    table = {
        "00-plan":       None,   # видео уже есть — план не нужен
        "01-script":     None,   # сценарий уже реализован в видео
        "02a-dupes":     [py, S("find_dupes.py"), "--input", clip],
        "02c-stab":      [py, S("stabilize.py"), "--input", clip, "--output", stab],
        "03-color":      [py, S("color.py"), "--input", stab, "--output", color],
        "03b-subtitles": [py, S("subtitles.py"), "--input", color, "--output", subs],
        "03c-inserts":   [py, S("inserts.py"), "--input", subs, "--output", inserts],
        "03d-audio-hf":  [py, S("audio_hf.py"), "--input", inserts, "--output", audio],
        "03e-strip-subs":[py, S("strip_subs.py"), "--video", color, "--audio", audio, "--output", clean],
        "03a-cut":       [py, S("vizard_cut_file.py"), "--input", clean, "--out-dir", p("clips")],
        "04-caption":    None,   # подписи — отдельный шаг/скрипт позже
        "05-qa":         None,   # проверка — отдельный шаг позже
        "06-publish":    None,   # публикация — отдельный шаг позже
    }
    return table.get(stage, None)


def run_stage(stage, current, mode):
    """Выполнить этап. Возвращает (успех, сообщение)."""
    cmd = stage_command(stage, current)
    if cmd is None:
        return True, "этап-пустышка (делается вне loop)"
    script = cmd[1]
    if not os.path.exists(script):
        return False, f"нет скрипта: {os.path.basename(script)} (напишется на ШАГЕ 3)"
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[-300:]
    return True, "ok"


def main():
    # 0. ключи
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        log_error("Нет ключей: " + ", ".join(missing))
        print("Нет ключей: " + ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    # 1. trigger
    trigger = read_text(TRIGGER)
    if "STOP" in trigger:
        log_error("trigger = STOP, конвейер остановлен вручную")
        report("—", "—", "остановлено кнопкой STOP", "—", "снять STOP в state/trigger.txt")
        return

    # 2. pipeline
    pipeline = read_json(PIPELINE, {"current": None, "queue": [], "blocked": [], "done_today": 0})

    # 3. выбрать ролик
    current = pipeline.get("current")
    if not current:
        if pipeline.get("queue"):
            current = pipeline["queue"].pop(0)
        else:
            link = first_inbox_link()
            if link:
                current = {"id": None, "source": link, "stage_index": 0}
            else:
                log_error("нечего делать: пусто в current, queue, inbox/links.txt")
                write_json(PIPELINE, pipeline)
                report("—", "—", "нечего делать", "—", "положить ссылку в inbox/links.txt")
                return
        pipeline["current"] = current
        write_json(PIPELINE, pipeline)

    # 4-7. Гоним этапы по STAGES один за другим, пока конец или блок.
    mode = read_text(TEST_MODE, "OFF")
    while current.get("stage_index", 0) < len(STAGES):
        si = current["stage_index"]
        stage = STAGES[si]
        prompt_path = os.path.join(STAGES_DIR, stage, "PROMPT.md")
        if not os.path.exists(prompt_path):
            log_error(f"нет файла этапа: {prompt_path}")
            report(current.get("id"), stage, "—", "нет PROMPT.md", "проверить stages/")
            write_json(PIPELINE, pipeline)
            return

        log_api(f"этап {stage}, режим {mode}, ролик {current.get('id')}")
        ok, msg = run_stage(stage, current, mode)

        if ok:
            log_api(f"этап {stage}: OK — {msg}")
            current["stage_index"] = si + 1
            current["attempts"] = 0
            pipeline["current"] = current
            write_json(PIPELINE, pipeline)
            continue

        # неудача этапа
        current["attempts"] = current.get("attempts", 0) + 1
        log_error(f"этап {stage}: не удался ({current['attempts']}/2) — {msg}")
        pipeline["current"] = current
        if current["attempts"] >= 2:  # две попытки — в blocked (правило 15)
            blocked = dict(current, blocked_stage=stage, reason=msg)
            pipeline.setdefault("blocked", []).append(blocked)
            pipeline["current"] = None
            write_json(PIPELINE, pipeline)
            report(current.get("id"), stage, "—", f"в blocked: {msg}", f"починить этап {stage}")
            return
        write_json(PIPELINE, pipeline)
        report(current.get("id"), stage, "—", f"ошибка ({current['attempts']}/2): {msg}", f"повтор {stage}")
        return

    # 8. все этапы пройдены
    finish_video(pipeline, current)
    report(current.get("id"), "готово", "все этапы пройдены", "—", "ролик собран")


def finish_video(pipeline, current):
    """Записать полную запись ролика в history.json и очистить current."""
    history = read_json(HISTORY, {"videos": []})
    record = {
        "id": current.get("id"),
        "topic": current.get("topic"),
        "type": current.get("type"),
        "line": current.get("line"),
        "stages_passed": current.get("stages_passed", [s for s in STAGES]),
        "started": current.get("started"),
        "completed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": current.get("status", "published"),
        "attempts_total": current.get("attempts_total", 1),
        "notes": current.get("notes", ""),
    }
    history["videos"].append(record)
    write_json(HISTORY, history)

    pipeline["current"] = None
    pipeline["done_today"] = pipeline.get("done_today", 0) + 1
    write_json(PIPELINE, pipeline)

    log_api(f"ролик {record['id']} завершён и записан в history.json")
    report(record["id"], "готово", "записан в history.json", "—", "следующий ролик")


if __name__ == "__main__":
    main()
