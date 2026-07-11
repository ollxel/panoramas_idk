#!/usr/bin/env python3
"""
recolor_markers.py
Перекрашивает серые PNG-пины из markers/ в пастельные цвета по категориям.

Логика:
 - читает marker_colors.json (категория -> hex)
 - для каждого файла делает colorize: 
   тёмная тень -> темнее пастельного (0.6), 
   средняя яркость (~90-110) -> сам пастельный,
   белая подсветка (255) -> остаётся белой
   Это сохраняет объём и блики оригинального пина.
 - сохраняет поверх оригиналов (делает backup в markers/original_gray/ при первом запуске)

Требует Pillow.
"""
import json
import os
from PIL import Image, ImageOps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "marker_colors.json")
MARKERS_DIR = os.path.join(BASE_DIR, "markers")
BACKUP_DIR = os.path.join(MARKERS_DIR, "original_gray")

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # убрать _meta
    cats = {k: v for k, v in data.items() if not k.startswith("_")}
    return cats

def ensure_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for fname in os.listdir(MARKERS_DIR):
        if not fname.lower().endswith(".png"):
            continue
        src = os.path.join(MARKERS_DIR, fname)
        dst = os.path.join(BACKUP_DIR, fname)
        if not os.path.exists(dst):
            # копируем оригинал один раз
            im = Image.open(src)
            im.save(dst)
            print(f"backup {fname} -> original_gray/")

def recolor_image(input_path, output_path, pastel_hex, midpoint=100):
    pastel_rgb = hex_to_rgb(pastel_hex)
    dark_rgb = tuple(int(c * 0.58) for c in pastel_rgb)  # темнее для теней

    im = Image.open(input_path).convert("RGBA")
    alpha = im.getchannel('A')
    gray = im.convert('L')

    # colorize: 0 -> dark_rgb, midpoint -> pastel_rgb, 255 -> white
    # midpoint подбирается по средней яркости серого пина (~85-110)
    colorized = ImageOps.colorize(
        gray,
        black=dark_rgb,
        white=(255, 255, 255),
        mid=pastel_rgb,
        blackpoint=0,
        whitepoint=255,
        midpoint=midpoint
    )
    colorized = colorized.convert("RGBA")
    colorized.putalpha(alpha)
    colorized.save(output_path, "PNG")

def main():
    cats = load_config()
    ensure_backup()

    print(f"Найдено {len(cats)} категорий для перекраски")
    for cat, info in cats.items():
        file_name = info.get("file")
        color = info.get("color")
        if not file_name or not color:
            continue
        src_path = os.path.join(MARKERS_DIR, file_name)
        # для перекраски всегда берём оригинал серый из backup если есть, чтобы не накапливать ошибку
        backup_src = os.path.join(BACKUP_DIR, file_name)
        if os.path.exists(backup_src):
            src_path = backup_src

        if not os.path.exists(src_path):
            print(f"[!] {cat}: файл {file_name} не найден -> пропускаем")
            continue

        dst_path = os.path.join(MARKERS_DIR, file_name)

        # подбираем midpoint индивидуально по яркости (для более светлых изображений midpoint чуть выше)
        # быстро оценим среднюю яркость
        im_tmp = Image.open(src_path).convert("L")
        # средняя яркост без прозрачных? упрощённо вся картинка
        # берём гистограмму
        # Если в имени sc и sport - они темнее (mean ~79), midpoint 75-85, иначе 100-105
        if file_name in ("sc.png", "sport.png"):
            mp = 82
        elif file_name in ("groceries.png", "kindergarten.png"):
            mp = 95
        else:
            mp = 102

        recolor_image(src_path, dst_path, color, midpoint=mp)
        print(f"[OK] {cat:15} {file_name:20} -> {color}")

    print("\nГотово! Все PNG в markers/ теперь пастельные.")

if __name__ == "__main__":
    main()
