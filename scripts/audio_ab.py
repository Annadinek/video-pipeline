#!/usr/bin/env python3
"""
audio_ab.py — сравнение «старые настройки против новых» на настоящем видео.

Зачем: перед тем как менять обработку звука для всех роликов, надо увидеть
цифрами, что стало лучше, а что хуже — на реальной записи, а не на пробнике.

Что делает:
  1) прогоняет один и тот же кусок звука двумя цепочками —
     СТАРОЙ (как было до правок) и НОВОЙ (как в audio_clean.py сейчас);
  2) меряет для каждой: громкость LUFS, истинный пик dBTP, разброс LRA,
     уровень фона в паузах, потерю в полосе 4-8 кГц (верх голоса);
  3) проверяет, в каком режиме сработал loudnorm — линейном (просто громче)
     или динамическом (сжимает звук сам, голос становится плоским);
  4) кладёт рядом два файла для прослушивания: old.m4a и new.m4a.

Запуск:
  python scripts/audio_ab.py --input clip.mp4 --outdir outputs/ab

Только Python 3, ffmpeg и стандартная библиотека. Исходник не трогает.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audio_clean import COMPAND, HIGHPASS, LIMITER, LOUDNORM_LRA, STRENGTH  # noqa: E402

TARGET_I, TARGET_TP = -14, -1

# Как было до правок — зафиксировано здесь, чтобы сравнение не «поехало»,
# когда настройки в audio_clean.py поменяются ещё раз.
OLD_DENOISE = "afftdn=nr=24:nf=-20"
OLD_COMPAND = "compand=attacks=0.02:decays=0.3:points=-70/-70|-30/-18|-15/-10|0/-5:gain=0"

CHAINS = {
    "old": f"{HIGHPASS},{OLD_DENOISE},{OLD_COMPAND}",
    "new": f"{HIGHPASS},{STRENGTH['high']},{COMPAND},{LIMITER}",
}
MAX_PAUSES = 12          # сколько пауз берём для замера фона
BAND_HIGH = "bandpass=f=6000:width_type=h:w=2000"   # 4-8 кГц — верх голоса
BAND_REF = "bandpass=f=650:width_type=h:w=350"      # 300-1000 Гц — опора


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def need(name):
    path = shutil.which(name)
    if not path:
        sys.exit(f"Нет {name}. Поставь ffmpeg и повтори.")
    return path


def ln_measure(ffmpeg, src, pre=""):
    """1-й проход loudnorm: что за материал на входе."""
    chain = (pre + "," if pre else "") + (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={LOUDNORM_LRA}:print_format=json")
    _, _, err = run([ffmpeg, "-hide_banner", "-i", src, "-af", chain, "-f", "null", "-"])
    a, b = err.rfind("{"), err.rfind("}")
    if a == -1 or b <= a:
        sys.exit(f"loudnorm не отдал измерения: {err.strip()[-300:]}")
    return json.loads(err[a:b + 1])


def render(ffmpeg, src, pre, out):
    """2-й проход loudnorm. Возвращает режим: Linear или Dynamic."""
    m = ln_measure(ffmpeg, src, pre)
    chain = pre + "," + (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}:linear=true:print_format=summary")
    rc, _, err = run([ffmpeg, "-y", "-hide_banner", "-i", src, "-af", chain,
                      "-vn", "-c:a", "aac", "-b:a", "192k", out])
    if rc != 0:
        sys.exit(f"ffmpeg не смог обработать звук: {err.strip()[-300:]}")
    mode = re.search(r"Normalization Type:\s*(\w+)", err)
    return {
        "mode": mode.group(1) if mode else "?",
        "pre_i": float(m["input_i"]), "pre_tp": float(m["input_tp"]),
        "pre_lra": float(m["input_lra"]),
    }


def find_pauses(ffmpeg, src):
    """Где в исходнике паузы — по ним будем мерить фон."""
    _, _, err = run([ffmpeg, "-hide_banner", "-i", src,
                     "-af", "silencedetect=noise=-35dB:d=0.45", "-f", "null", "-"])
    pauses, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start:\s*([-\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end:\s*([-\d.]+)", line)
        if m and start is not None:
            end = float(m.group(1))
            if end - start > 0.4:
                pauses.append((start + 0.15, end - 0.15))
            start = None
    return pauses[:MAX_PAUSES]


def rms_db(ffmpeg, path, start, dur, pre=""):
    """Средний уровень куска, дБ. Пусто/тишина → None."""
    chain = (pre + "," if pre else "") + "astats=metadata=1:reset=0"
    _, _, err = run([ffmpeg, "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                     "-i", path, "-af", chain, "-f", "null", "-"])
    vals = [float(m) for m in re.findall(r"RMS level dB:\s*(-?\d+\.?\d*)", err)]
    return vals[-1] if vals else None


def pause_floor(ffmpeg, path, pauses):
    vals = [v for s, e in pauses
            if (v := rms_db(ffmpeg, path, s, e - s)) is not None and v > -120]
    if not vals:
        return None
    vals.sort()
    keep = vals[: max(1, int(len(vals) * 0.8))]      # без самых громких «пауз»
    return sum(keep) / len(keep)


def band_balance(ffmpeg, path):
    """Насколько верх (4-8 кГц) тише опоры (300-1000 Гц), дБ."""
    hi = rms_db(ffmpeg, path, 0, 10 ** 6, BAND_HIGH)
    ref = rms_db(ffmpeg, path, 0, 10 ** 6, BAND_REF)
    return None if hi is None or ref is None else hi - ref


def line(name, d):
    def num(v, fmt):
        return format(v, fmt) if v is not None else "  н/д"
    return (f"{name:<12}{num(d['I'], '>8.1f')}{num(d['TP'], '>8.1f')}{num(d['LRA'], '>7.1f')}"
            f"{num(d['floor'], '>14.1f')}{num(d['band'], '>12.1f')}   {d.get('mode', '-')}")


def main():
    ap = argparse.ArgumentParser(description="Сравнение старых и новых настроек звука")
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="outputs/ab")
    args = ap.parse_args()

    ffmpeg = need("ffmpeg")
    if not os.path.exists(args.input):
        sys.exit(f"Нет файла: {args.input}")
    os.makedirs(args.outdir, exist_ok=True)

    pauses = find_pauses(ffmpeg, args.input)
    print(f"Файл:   {args.input}")
    print(f"Пауз для замера фона: {len(pauses)}")

    src = ln_measure(ffmpeg, args.input)
    rows = {"исходник": {
        "I": float(src["input_i"]), "TP": float(src["input_tp"]),
        "LRA": float(src["input_lra"]),
        "floor": pause_floor(ffmpeg, args.input, pauses),
        "band": band_balance(ffmpeg, args.input), "mode": "-"}}

    for name, pre in CHAINS.items():
        out = os.path.join(args.outdir, f"{name}.m4a")
        info = render(ffmpeg, args.input, pre, out)
        m = ln_measure(ffmpeg, out)
        rows[name] = {
            "I": float(m["input_i"]), "TP": float(m["input_tp"]),
            "LRA": float(m["input_lra"]),
            "floor": pause_floor(ffmpeg, out, pauses),
            "band": band_balance(ffmpeg, out), "mode": info["mode"],
            "pre": [info["pre_i"], info["pre_tp"], info["pre_lra"]],
        }
        print(f"Готов:  {out}")

    print()
    print("СТАРЫЕ:", CHAINS["old"])
    print("НОВЫЕ: ", CHAINS["new"])
    print()
    print(f"{'':<12}{'LUFS':>8}{'dBTP':>8}{'LRA':>7}{'фон в паузах':>14}{'верх 4-8к':>12}   loudnorm")
    for name in ("исходник", "old", "new"):
        print(line({"old": "старые", "new": "новые"}.get(name, name), rows[name]))

    if rows["old"]["band"] is not None and rows["new"]["band"] is not None:
        d = rows["new"]["band"] - rows["old"]["band"]
        print(f"\nВерх голоса: новые настройки {'ярче' if d > 0 else 'глуше'} "
              f"старых на {abs(d):.1f} дБ")
    if rows["old"]["floor"] is not None and rows["new"]["floor"] is not None:
        d = rows["new"]["floor"] - rows["old"]["floor"]
        print(f"Фон в паузах: новые настройки {'тише' if d < 0 else 'громче'} "
              f"старых на {abs(d):.1f} дБ")
    print(f"Разброс громкости LRA: было {rows['old']['LRA']:.1f}, "
          f"стало {rows['new']['LRA']:.1f} LU")

    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
