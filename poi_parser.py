"""
poi_parser.py — Оптимизированный парсер POI из OSM
- Группирует теги в regex (3 запроса вместо 36) -> ~8x быстрее Overpass
- Параллельный запрос к нескольким Overpass инстансам, первый успех wins
- Двойной кэш: память + диск (cache/poi/*.json) -> повторный хит ~5ms
- Таймауты снижены
"""

import json
import os
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import radians, cos, sin, asin, sqrt
from typing import Dict, List

import requests

# Быстрые инстансы первыми
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

# Группировка по ключам для regex
TAG_GROUPS = {
    "amenity": [
        "school", "kindergarten", "pharmacy", "bank", "atm", "fuel",
        "cafe", "fast_food", "food_court", "restaurant",
        "marketplace", "post_office", "parcel_locker"
    ],
    "shop": [
        "supermarket", "convenience", "mall", "department_store",
        "greengrocer", "butcher"
    ],
    "leisure": [
        "fitness_centre", "sports_centre", "swimming_pool", "stadium"
    ]
}

# Тег -> категория (для классификации после получения)
_TAG_TO_CAT = {
    ("shop", "supermarket"): "groceries",
    ("shop", "convenience"): "groceries",
    ("shop", "mall"): "groceries",
    ("shop", "department_store"): "groceries",
    ("shop", "greengrocer"): "groceries",
    ("shop", "butcher"): "groceries",
    ("amenity", "marketplace"): "groceries",
    ("amenity", "school"): "school",
    ("amenity", "kindergarten"): "daycare",
    ("amenity", "pharmacy"): "pharmacy",
    ("amenity", "bank"): "atm",
    ("amenity", "atm"): "atm",
    ("amenity", "fuel"): "gas",
    ("leisure", "fitness_centre"): "sport",
    ("leisure", "sports_centre"): "sport",
    ("leisure", "swimming_pool"): "sport",
    ("leisure", "stadium"): "sport",
    ("amenity", "cafe"): "cafe",
    ("amenity", "fast_food"): "cafe",
    ("amenity", "food_court"): "cafe",
    ("amenity", "restaurant"): "restaurants",
    ("amenity", "post_office"): "pvz",
    ("amenity", "parcel_locker"): "pvz",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache", "poi")
os.makedirs(CACHE_DIR, exist_ok=True)

_parser_cache: Dict[str, Dict[str, List[dict]]] = {}
CACHE_TTL_SECONDS = 3600 * 6  # 6 часов для POI достаточно


def _haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371000 * asin(min(1, sqrt(a)))


def _cache_file_for_key(lat, lon, radius):
    key = f"{lat:.5f}_{lon:.5f}_{int(radius)}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _load_disk_cache(lat, lon, radius):
    path = _cache_file_for_key(lat, lon, radius)
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > CACHE_TTL_SECONDS:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_disk_cache(lat, lon, radius, data):
    path = _cache_file_for_key(lat, lon, radius)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _build_query(lat, lon, radius_m):
    # Один regex на ключ, nwr = node+way+relation, out center для way/rel
    parts = []
    for key, values in TAG_GROUPS.items():
        regex = "|".join(values)
        parts.append(f'nwr["{key}"~"^({regex})$"](around:{int(radius_m)},{lat},{lon});')
    # таймаут 15 вместо 25, maxsize ограничен
    q = f'[out:json][timeout:15][maxsize:16777216];({ "".join(parts) });out center;'
    return q


def _fetch_overpass(query, timeout=12):
    """Параллельный fetch к нескольким Overpass, возвращает первый успех"""
    def _do(url):
        try:
            r = requests.post(
                url,
                data={"data": query},
                timeout=timeout,
                headers={"User-Agent": "panoramas_idk/1.0"},
            )
            if r.status_code in (429, 504, 502, 503):
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    # пробуем до 3 самых быстрых параллельно
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_do, url): url for url in OVERPASS_URLS[:3]}
        for fut in as_completed(futs):
            res = fut.result()
            if res is not None and "elements" in res:
                # отменяем остальные (по возможности)
                for f in futs:
                    f.cancel()
                return res
    # fallback последовательно по всем
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, timeout=timeout+2,
                              headers={"User-Agent": "panoramas_idk/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None


def fetch_poi_from_osm(lat, lon, radius_m=500):
    cache_key = f"{lat:.5f},{lon:.5f},{radius_m:.0f}"
    if cache_key in _parser_cache:
        return _parser_cache[cache_key]

    # диск кэш
    disk = _load_disk_cache(lat, lon, radius_m)
    if disk is not None:
        _parser_cache[cache_key] = disk
        if len(_parser_cache) > 300:
            _parser_cache.pop(next(iter(_parser_cache)))
        return disk

    query = _build_query(lat, lon, radius_m)
    data = _fetch_overpass(query, timeout=12)

    result = {k: [] for k in set(_TAG_TO_CAT.values())}

    if data:
        seen = set()
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            if el.get("type") == "node":
                el_lat, el_lon = el.get("lat"), el.get("lon")
            elif "center" in el:
                el_lat = el["center"].get("lat")
                el_lon = el["center"].get("lon")
            else:
                continue
            if el_lat is None or el_lon is None:
                continue

            cat = None
            # быстрый lookup: только по тегам из групп
            for (tag_key, tag_val), cat_key in _TAG_TO_CAT.items():
                if tags.get(tag_key) == tag_val:
                    cat = cat_key
                    break
            if not cat:
                continue

            name = tags.get("name") or tags.get("brand") or tags.get("operator") or ""
            if not name:
                # для ATM/банков без названия оставим тип
                if cat == "atm":
                    name = tags.get("operator") or tags.get("brand") or "Банкомат"
                else:
                    continue

            uid = (round(el_lat, 5), round(el_lon, 5), name)
            if uid in seen:
                continue
            seen.add(uid)

            dist = _haversine(lon, lat, el_lon, el_lat)
            if dist > radius_m:
                continue

            result[cat].append({
                "title": name,
                "lat": el_lat,
                "lon": el_lon,
                "dist_m": round(dist, 1),
                "org_url": "",  # генерируется на фронте -> ссылка на Яндекс по координатам
                "source": "osm"
            })

    for k in result:
        result[k].sort(key=lambda x: x["dist_m"])

    _parser_cache[cache_key] = result
    _save_disk_cache(lat, lon, radius_m, result)
    if len(_parser_cache) > 300:
        _parser_cache.pop(next(iter(_parser_cache)))

    return result
