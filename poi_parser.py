"""
poi_parser.py — Быстрый парсер заведений из OSM
================================================
Один запрос Overpass на ВСЕ категории сразу → фильтрация на клиенте.
На основе underfmc/parser, оптимизирован.
"""

import time
import requests
from math import radians, cos, sin, asin, sqrt
from typing import Dict, List

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Теги → category_key. Один запрос забирает всё сразу.
_TAG_TO_CAT = {
    # groceries
    ("shop", "supermarket"): "groceries",
    ("shop", "convenience"): "groceries",
    ("shop", "mall"): "groceries",
    ("shop", "department_store"): "groceries",
    ("shop", "greengrocer"): "groceries",
    ("shop", "butcher"): "groceries",
    ("amenity", "marketplace"): "groceries",
    # school
    ("amenity", "school"): "school",
    # daycare
    ("amenity", "kindergarten"): "daycare",
    # pharmacy
    ("amenity", "pharmacy"): "pharmacy",
    # atm
    ("amenity", "bank"): "atm",
    ("amenity", "atm"): "atm",
    # gas
    ("amenity", "fuel"): "gas",
    # sport
    ("leisure", "fitness_centre"): "sport",
    ("leisure", "sports_centre"): "sport",
    ("leisure", "swimming_pool"): "sport",
    ("leisure", "stadium"): "sport",
    # cafe
    ("amenity", "cafe"): "cafe",
    ("amenity", "fast_food"): "cafe",
    ("amenity", "food_court"): "cafe",
    # restaurants
    ("amenity", "restaurant"): "restaurants",
    # pvz
    ("amenity", "post_office"): "pvz",
    ("amenity", "parcel_locker"): "pvz",
}

_parser_cache: Dict[str, Dict[str, List[dict]]] = {}


def _haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1; dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371000 * asin(min(1, sqrt(a)))


def fetch_poi_from_osm(lat, lon, radius_m=500):
    """
    Один запрос Overpass → все категории сразу.
    Возвращает {category_key: [{title, lat, lon, dist_m, org_url}]}
    """
    cache_key = f"{lat:.5f},{lon:.5f},{radius_m:.0f}"
    if cache_key in _parser_cache:
        return _parser_cache[cache_key]

    # Собираем один запрос на все теги
    parts = []
    for (key, val) in _TAG_TO_CAT:
        parts.append(f'node["{key}"="{val}"](around:{radius_m},{lat},{lon});')
        parts.append(f'way["{key}"="{val}"](around:{radius_m},{lat},{lon});')

    query = f'[out:json][timeout:25];({"".join(parts)});out center;'

    data = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, timeout=35,
                              headers={"User-Agent": "panoramas_idk/1.0"})
            if r.status_code in (429, 504):
                time.sleep(1)
                continue
            r.raise_for_status()
            data = r.json()
            break
        except:
            time.sleep(0.3)

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

            # Определяем категорию
            cat = None
            for (tag_key, tag_val), cat_key in _TAG_TO_CAT.items():
                if tags.get(tag_key) == tag_val:
                    cat = cat_key
                    break
            if not cat:
                continue

            name = tags.get("name", tags.get("brand", ""))
            if not name:
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
                "org_url": "",
            })

    for k in result:
        result[k].sort(key=lambda x: x["dist_m"])

    _parser_cache[cache_key] = result
    if len(_parser_cache) > 200:
        _parser_cache.pop(next(iter(_parser_cache)))

    return result
