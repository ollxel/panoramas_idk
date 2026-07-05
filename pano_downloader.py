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
import re
import time
from functools import lru_cache
from io import BytesIO
from typing import Optional, Dict, Any, Iterable, List, Tuple

import requests
from PIL import Image

PANO_META_URL = (
    "https://api-maps.yandex.ru/services/panoramas/1.x/"
    "?l={layer}&lang=ru_RU&ll={lon},{lat}&origin=userAction&provider=streetview"
)
PANO_META_BY_ID_URL = (
    "https://api-maps.yandex.ru/services/panoramas/1.x/"
    "?l={layer}&lang=ru_RU&oid={pano_id}&origin=userAction&provider=streetview"
)
TILE_URL = "https://pano.maps.yandex.net/{image_id}/{zoom}.{x}.{y}"
YANDEX_MAPS_BOOTSTRAP_URL = "https://yandex.ru/maps/?l=stv"
AIRSHIP_TILE_URL = "https://vec01.core-stv-renderer.maps.yandex.net/3.x/tiles"
AIRSHIP_ID_RE = re.compile(rb"\d{10}_\d{9}_\d{2}_\d{10}")
YANDEX_MERCATOR_E = 0.0818191908426
MIN_TILE_ZOOM = 6
MAX_AIRSHIP_TILE_ZOOM = 12
DEFAULT_TILE_LIMIT = int(os.environ.get("SKY_TILE_MAX_TILES", "96"))
STV_VERSION_TTL_SECONDS = int(os.environ.get("YANDEX_STV_VERSION_TTL_SECONDS", "3600"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_stv_version_cache: Dict[str, Any] = {"value": None, "expires_at": 0.0}


class PanoramaNotFound(Exception):
    """Рядом с точкой нет доступной панорамы Яндекса."""


class PanoramaLayerError(Exception):
    """Не удалось получить служебные данные слоя панорам Яндекса."""


def _parse_panorama_response(data: Dict[str, Any], lookup: str) -> Dict[str, Any]:
    try:
        raw_data = data["data"]["Data"]
        images = raw_data["Images"]
        image_id = images["imageId"]
        tile_width = images["Tiles"]["width"]
        tile_height = images["Tiles"]["height"]
        zooms = images["Zooms"]
    except (KeyError, TypeError):
        raise PanoramaNotFound(f"Панорама не найдена: {lookup}")

    pano_point = None
    name = ""
    height = None
    try:
        point = raw_data["Point"]
        coords = point["coordinates"]
        pano_point = {"lon": coords[0], "lat": coords[1]}
        name = point.get("name") or ""
        if len(coords) > 2:
            height = coords[2]
    except (KeyError, TypeError, IndexError):
        pass

    return {
        "panorama_id": raw_data.get("panoramaId"),
        "timestamp": raw_data.get("timestamp"),
        "image_id": image_id,
        "tile_width": tile_width,
        "tile_height": tile_height,
        "zooms": zooms,
        "pano_point": pano_point,
        "name": name,
        "height": height,
    }


def fetch_panorama_meta(lat: float, lon: float, layer: str = "sta") -> Dict[str, Any]:
    """
    Синхронно получает метаданные ближайшей панорамы для точки (lat, lon).
    Бросает PanoramaNotFound, если панорамы поблизости нет.
    """
    url = PANO_META_URL.format(lat=lat, lon=lon, layer=layer)
    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()
    return _parse_panorama_response(data, f"{lat},{lon}")


@lru_cache(maxsize=4096)
def fetch_panorama_meta_by_id(pano_id: str, layer: str = "sta") -> Dict[str, Any]:
    """Получает метаданные конкретной панорамы по panoramaId/oid."""
    url = PANO_META_BY_ID_URL.format(pano_id=pano_id, layer=layer)
    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()
    return _parse_panorama_response(data, pano_id)


def fetch_yandex_stv_version(force: bool = False) -> str:
    """
    Берёт актуальную версию слоя панорам из bootstrap-страницы Яндекс.Карт.
    Версия обязательна для vector tiles с иконками воздушных панорам.
    """
    env_version = os.environ.get("YANDEX_STV_VERSION")
    if env_version and not force:
        return env_version

    now = time.time()
    cached_version = _stv_version_cache.get("value")
    if cached_version and not force and now < float(_stv_version_cache.get("expires_at", 0.0)):
        return cached_version

    response = requests.get(YANDEX_MAPS_BOOTSTRAP_URL, headers=HEADERS, timeout=12)
    response.raise_for_status()
    match = re.search(r'"stv":\{"version":"([^"]+)"', response.text)
    if not match:
        raise PanoramaLayerError("Не удалось найти версию слоя stv в Яндекс.Картах")

    version = match.group(1)
    _stv_version_cache["value"] = version
    _stv_version_cache["expires_at"] = now + STV_VERSION_TTL_SECONDS
    return version


def _lon_to_tile_x(lon: float, zoom: int) -> int:
    tile_count = 2 ** zoom
    x = int(math.floor((lon + 180.0) / 360.0 * tile_count))
    return max(0, min(tile_count - 1, x))


def _lat_to_yandex_tile_y(lat: float, zoom: int) -> int:
    tile_count = 2 ** zoom
    clamped_lat = max(-85.0, min(85.0, lat))
    phi = math.radians(clamped_lat)
    sin_phi = math.sin(phi)
    mercator = math.log(
        math.tan(math.pi / 4.0 + phi / 2.0)
        * ((1.0 - YANDEX_MERCATOR_E * sin_phi) / (1.0 + YANDEX_MERCATOR_E * sin_phi))
        ** (YANDEX_MERCATOR_E / 2.0)
    )
    y = int(math.floor((0.5 - mercator / (2.0 * math.pi)) * tile_count))
    return max(0, min(tile_count - 1, y))


def _tile_range_for_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
    zoom: int,
) -> List[Tuple[int, int]]:
    if east < west:
        east = west
    x_min = _lon_to_tile_x(west, zoom)
    x_max = _lon_to_tile_x(east, zoom)
    y_min = _lat_to_yandex_tile_y(north, zoom)
    y_max = _lat_to_yandex_tile_y(south, zoom)
    if y_max < y_min:
        y_min, y_max = y_max, y_min
    return [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]


def _choose_airship_tile_zoom(
    west: float,
    south: float,
    east: float,
    north: float,
    map_zoom: int,
    max_tiles: int,
) -> Tuple[int, List[Tuple[int, int]], bool]:
    target_zoom = max(MIN_TILE_ZOOM, min(MAX_AIRSHIP_TILE_ZOOM, int(map_zoom or MAX_AIRSHIP_TILE_ZOOM)))
    for tile_zoom in range(target_zoom, MIN_TILE_ZOOM - 1, -1):
        tiles = _tile_range_for_bbox(west, south, east, north, tile_zoom)
        if len(tiles) <= max_tiles:
            return tile_zoom, tiles, False
    tiles = _tile_range_for_bbox(west, south, east, north, MIN_TILE_ZOOM)
    return MIN_TILE_ZOOM, tiles[:max_tiles], len(tiles) > max_tiles


@lru_cache(maxsize=4096)
def fetch_airship_tile_ids(x: int, y: int, zoom: int, version: str) -> Tuple[str, ...]:
    """Возвращает panoramaId воздушных шаров из одного vector tile слоя sta."""
    params = {
        "l": "sta",
        "x": x,
        "y": y,
        "z": zoom,
        "v": version,
        "format": "protobuf",
        "lang": "ru_RU",
    }
    response = requests.get(AIRSHIP_TILE_URL, params=params, headers=HEADERS, timeout=10)
    if response.status_code in (204, 404):
        return tuple()
    response.raise_for_status()
    ids = {match.decode("ascii") for match in AIRSHIP_ID_RE.findall(response.content)}
    return tuple(sorted(ids))


def fetch_airship_ids_for_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
    map_zoom: int = MAX_AIRSHIP_TILE_ZOOM,
    max_tiles: int = DEFAULT_TILE_LIMIT,
) -> Dict[str, Any]:
    """
    Ищет panoramaId воздушных панорам в bbox через слой sta vector tiles.
    Координаты bbox передаются как lon/lat: west, south, east, north.
    """
    version = fetch_yandex_stv_version()
    tile_zoom, tiles, partial = _choose_airship_tile_zoom(
        west, south, east, north, map_zoom, max_tiles
    )
    pano_ids = set()
    for x, y in tiles:
        pano_ids.update(fetch_airship_tile_ids(x, y, tile_zoom, version))
    return {
        "ids": sorted(pano_ids),
        "tile_zoom": tile_zoom,
        "tile_count": len(tiles),
        "partial": partial,
        "version": version,
    }


async def _fetch_tile(session, url: str, semaphore: asyncio.Semaphore, retries: int = 2) -> Image.Image:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            async with semaphore:
                async with session.get(url, timeout=10) as response:
                    response.raise_for_status()
                    content = await response.read()
                    tile = Image.open(BytesIO(content))
                    tile.load()
                    return tile
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(0.2)
    raise RuntimeError(f"Failed to download tile {url}: {last_error}")


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
    semaphore = asyncio.Semaphore(8)
    connector = aiohttp.TCPConnector(limit=16, limit_per_host=16)
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector,
        timeout=timeout,
    ) as session:
        tasks = []
        for x in range(x_range):
            for y in range(y_range):
                url = TILE_URL.format(image_id=image_id, zoom=zoom, x=x, y=y)
                tasks.append(_fetch_tile(session, url, semaphore))
        results = await asyncio.gather(*tasks)

    idx = 0
    for x in range(x_range):
        for y in range(y_range):
            tile = results[idx]
            idx += 1
            if tile is None:
                raise RuntimeError(
                    f"Panorama assembly failed: missing tile at x={x}, y={y}, image_id={image_id}, zoom={zoom}"
                )
            pano.paste(tile, (x * tile_width, y * tile_height))

    return pano


def download_panorama(
    lat: Optional[float],
    lon: Optional[float],
    output_path: str,
    zoom: int = 0,
    auto_height: bool = True,
    layer: str = "sta",
    pano_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Скачивает и собирает панораму рядом с (lat, lon) или по pano_id,
    сохраняет в output_path (JPEG). Возвращает метаданные.
    Бросает PanoramaNotFound, если панорамы нет.
    """
    if pano_id:
        meta = fetch_panorama_meta_by_id(pano_id, layer=layer)
    else:
        if lat is None or lon is None:
            raise ValueError("Нужны либо lat/lon, либо pano_id")
        meta = fetch_panorama_meta(lat, lon, layer=layer)

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


def download_panorama_by_id(
    pano_id: str,
    output_path: str,
    zoom: int = 0,
    auto_height: bool = True,
    layer: str = "sta",
) -> Dict[str, Any]:
    """Скачивает и собирает конкретную панораму по panoramaId/oid."""
    return download_panorama(
        None,
        None,
        output_path,
        zoom=zoom,
        auto_height=auto_height,
        layer=layer,
        pano_id=pano_id,
    )
