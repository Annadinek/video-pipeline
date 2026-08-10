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
import sys
from datetime import datetime, timezone

# --- пути ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
LOG_DIR = os.path.join(ROOT, "logs")
STAGES_DIR = os.path.join(ROOT, "stages")
INBOX_LINKS = os.path.join(ROOT, "inbox", "links.txt")

TRIGGER = os.path.join(STATE_DIR, "trigger.txt")
TEST_MODE = os.path.join(STATE_DIR, "test-mode.txt")
PIPELINE = os.path.join(STATE_DIR, "pipeline.json")
HISTORY = os.path.join(STATE_DIR, "history.json")

ERRORS_LOG = os.path.join(LOG_DIR, "errors.log")
API_LOG = os.path.join(LOG_DIR, "api-calls.log")
RETRIES_LOG = os.path.join(LOG_DIR, "retries.log")

# Порядок этапов — имена папок в stages/
# Порядок важен: сначала ВСЯ обработка на длинном ГОРИЗОНТАЛЬНОМ видео
# (расшифровка, звук, стабилизация, цвет), затем Vizard режет вертикаль 9:16
# (03a-cut), и ТОЛЬКО ПОТОМ субтитры (03b) — на каждом вертикальном куске.
# Субтитры нельзя рисовать до нарезки: Vizard обрезает бока под 9:16 и всё
# нарисованное поехало бы.
STAGES = [
    "00-plan",
    "01-script",
    "02a-dupes",     # расшифровка (пословный transcript.json) на горизонтали
    "02b-audio",     # звук + резка пауз
    "02c-stab",      # стабилизация
    "03-color",      # цвет
    "03a-cut",       # Vizard режет вертикаль 9:16 (субтитры Vizard выключены)
    "03b-subtitles", # свои субтитры с подсветкой — на вертикальных кусках
    "04-caption",
    "05-qa",
    "06-publish",
]

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

    stage_index = current.get("stage_index", 0)
    if stage_index >= len(STAGES):
        # 8. дошли до конца — записать в history и очистить current
        finish_video(pipeline, current)
        return

    stage_name = STAGES[stage_index]
    prompt_path = os.path.join(STAGES_DIR, stage_name, "PROMPT.md")

    # 4. открыть PROMPT.md
    if not os.path.exists(prompt_path):
        log_error(f"нет файла этапа: {prompt_path}")
        report(current.get("id"), stage_name, "—", "нет PROMPT.md", "проверить stages/")
        return

    # 4.1 режим
    mode = read_text(TEST_MODE, "OFF")
    log_api(f"этап {stage_name}, режим {mode}, ролик {current.get('id')}")

    # 5-7. Выполнение этапа делает оркестратор конвейера (Claude Code читает PROMPT.md
    # и выполняет задачу этапа). Здесь — каркас: состояние, попытки, журналы.
    # Реальная работа этапа подключается тут:
    #     result = run_stage(stage_name, prompt_path, current, mode)
    # Пока каркас только продвигает закладку и ведёт учёт попыток.

    attempts = current.get("attempts", 0)
    # Заготовка: этап считается требующим выполнения оркестратором.
    # Каркас фиксирует, что дошли до этого этапа, и на этом останавливается,
    # чтобы оркестратор выполнил PROMPT.md. Логику успеха/неудачи ниже
    # подключит реальный run_stage.

    report(
        current.get("id"),
        stage_name,
        f"этап открыт (PROMPT.md), режим {mode}, попытка {attempts + 1}",
        "—",
        f"выполнить задачу этапа {stage_name} по его PROMPT.md",
    )
    write_json(PIPELINE, pipeline)


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
