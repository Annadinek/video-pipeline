# ЭТАП 02 — НАРЕЗКА
Роль: Режиссёр (brain/ROLES.md)

## ЧТО ЧИТАЮ
- inbox/links.txt
- state/pipeline.json

## ЧТО ДЕЛАЮ
Нарезка через скилл `.claude/skills/using-vizard-api/`.
Vizard берёт ссылку YouTube напрямую, скачивание не требуется.
Затем вертикаль 9:16 со слежением за лицом через скилл `.claude/skills/vertical-cut/`.
Горизонталь 16:9 — без слежения.

## ЧТО ОТДАЮ
- файлы в outputs/ready/[id]/
- запись в state/pipeline.json

## КОГДА СТОП
- Vizard не ответил дважды
- две неудачные попытки → в blocked, отчёт, идти дальше
