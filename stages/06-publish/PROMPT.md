# ЭТАП 06 — ПУБЛИКАЦИЯ
Роль: СММ (brain/ROLES.md)

## ЧТО ЧИТАЮ
- результат этапа 05-qa (state/pipeline.json)

## ЧТО ДЕЛАЮ
- если qa.passed false — НЕ публикую
- если true — публикую те же вертикальные нарезки на ТРИ площадки:
  - **YouTube Shorts** — `scripts/vizard_to_youtube.py` (свои квоты, обложки, плейлист);
  - **Instagram Reels** и **TikTok** — `scripts/publish_socials.py` (через сам Vizard,
    эндпоинт `project/publish-video`; аккаунты Анна один раз подключает в Vizard).
  Те же клипы Vizard и подписи (`captions_freedom.json`), у каждой площадки — свой учёт
  выложенного (`shorts_published.json` / `socials_published.json`), чтобы не дублировать.
- складываю пакет для планера в outputs/ready/[id]/
- отправляю Анне в Telegram уведомление с кнопками «Выпускаем» и «Переделать»

## ЧТО ОТДАЮ
- ссылки по всем площадкам, запись в state/history.json

## КОГДА СТОП
- test-mode.txt = ON → публиковать только в тестовый аккаунт
- две неудачные попытки → в blocked, отчёт, идти дальше
