---
name: vertical-cut
description: Use when converting a horizontal 16:9 video into a vertical 9:16 clip with face tracking, so the speaker stays centered in the frame. Use for making Shorts/Reels from horizontal footage.
---

# vertical-cut

Из горизонтального 16:9 делает вертикаль 9:16 со слежением за лицом.

## Как работает
1. OpenCV находит лицо на кадрах через каждые 0.5 секунды
2. Считает центр лица по каждой точке
3. Сглаживает траекторию, чтобы кадр не дёргался
4. ffmpeg режет окно 9:16 по этой траектории

## Запуск
```bash
python scripts/vertical_cut.py \
  --input outputs/ready/ID/clip.mp4 \
  --output outputs/ready/ID/vertical.mp4 \
  --face-tracking true
```

## Без слежения — только для горизонтали
```bash
ffmpeg -i input.mp4 -vf "scale=1920:1080" -c:a copy output.mp4
```

## Когда лицо не найдено
Резать по центру кадра. Записать в logs/errors.log. Конвейер не останавливать.

## Проверка результата
Соотношение сторон ровно 9:16. Звук на месте. Лицо не уезжает за край.
