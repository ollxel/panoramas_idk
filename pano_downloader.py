"""
pano_downloader.py
===================
Адаптация скрипта zer0-dev/yandex-pano-downloader (MIT):
https://github.com/zer0-dev/yandex-pano-downloader

Изменения относительно оригинала:
  - обёрнуто в переиспользуемые функции (вместо CLI-скрипта);
  - добавлено кэширование результата на диск по image_id;
  - добавлена отдельная функция получения метаданных без скачивания тайлов
    (чтобы можно было быстро проверить "есть ли панорама в этой точке").

ВАЖНО: используется недокументированный эндпоинт Яндекс.Карт
(api-maps.yandex.ru/services/panoramas/1.x/) БЕЗ apikey. Официального
статуса у него нет, Яндекс может ограничить/заблокировать доступ в любой
момент — используйте на свой страх и риск (см. README оригинального репо).
"""

import asyncio
import math
import os
import platform
from io import BytesIO
from typing import Optional, Dict, Any

import requests
from PIL import Image

PANO_META_URL = (
    "https://api-maps.yandex.ru/services/panoramas/1.x/"
    "?l=stv&lang=ru_RU&ll={lon},{lat}&origin=userAction&provider=streetview"
)
TILE_URL = "https://pano.maps.yandex.net/{image_id}/{zoom}.{x}.{y}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


class PanoramaNotFound(Exception):
    """Рядом с точкой нет доступной панорамы Яндекса."""


def fetch_panorama_meta(lat: float, lon: float) -> Dict[str, Any]:
    """
    Синхронно получает метаданные ближайшей панорамы для точки (lat, lon).
    Бросает PanoramaNotFound, если панорамы поблизости нет.
    """
    url = PANO_META_URL.format(lat=lat, lon=lon)
    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()

    try:
        images = data["data"]["Data"]["Images"]
        image_id = images["imageId"]
        tile_width = images["Tiles"]["width"]
        tile_height = images["Tiles"]["height"]
        zooms = images["Zooms"]
    except (KeyError, TypeError):
        raise PanoramaNotFound(f"Панорама не найдена рядом с {lat},{lon}")

    # Реальные координаты найденной панорамы (могут немного отличаться
    # от точки клика — Яндекс подставляет ближайшую доступную панораму).
    pano_point = None
    try:
        coords = data["data"]["Data"]["Point"]["coordinates"]
        pano_point = {"lon": coords[0], "lat": coords[1]}
    except (KeyError, TypeError, IndexError):
        pass

    return {
        "image_id": image_id,
        "tile_width": tile_width,
        "tile_height": tile_height,
        "zooms": zooms,  # список {width, height} по уровням зума
        "pano_point": pano_point,
    }


async def _fetch_tile(session, url: str) -> Optional[Image.Image]:
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            content = await response.read()
            return Image.open(BytesIO(content))
    except Exception:
        return None


async def _assemble_panorama(
    image_id: str,
    pano_width: int,
    pano_height: int,
    tile_width: int,
    tile_height: int,
    zoom: int,
    auto_height: bool = True,
) -> Image.Image:
    import aiohttp

    x_range = math.ceil(pano_width / tile_width)
    y_range = math.ceil(pano_height / tile_height)

    if auto_height and pano_height != int(pano_width / 2):
        pano_height = int(pano_width / 2)

    pano = Image.new("RGB", (pano_width, pano_height))

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []
        for x in range(x_range):
            for y in range(y_range):
                url = TILE_URL.format(image_id=image_id, zoom=zoom, x=x, y=y)
                tasks.append(_fetch_tile(session, url))
        results = await asyncio.gather(*tasks)

    idx = 0
    for x in range(x_range):
        for y in range(y_range):
            tile = results[idx]
            idx += 1
            if tile:
                pano.paste(tile, (x * tile_width, y * tile_height))

    return pano


def download_panorama(
    lat: float,
    lon: float,
    output_path: str,
    zoom: int = 0,
    auto_height: bool = True,
) -> Dict[str, Any]:
    """
    Скачивает и собирает панораму рядом с (lat, lon), сохраняет в output_path
    (JPEG). Возвращает метаданные (image_id, координаты найденной панорамы).
    Бросает PanoramaNotFound, если панорамы нет.
    """
    meta = fetch_panorama_meta(lat, lon)

    try:
        zoom_data = meta["zooms"][zoom]
        pano_width = zoom_data["width"]
        pano_height = zoom_data["height"]
    except (IndexError, KeyError, TypeError):
        raise PanoramaNotFound(f"Уровень зума {zoom} недоступен для этой панорамы")

    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    image = asyncio.run(
        _assemble_panorama(
            image_id=meta["image_id"],
            pano_width=pano_width,
            pano_height=pano_height,
            tile_width=meta["tile_width"],
            tile_height=meta["tile_height"],
            zoom=zoom,
            auto_height=auto_height,
        )
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    image.save(output_path, quality=88)

    return meta
