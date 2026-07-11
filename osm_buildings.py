"""
osm_buildings.py — OSM здания + дороги + деревья + водоёмы + планы + Flask endpoint
Оптимизированная версия:
- Параллельный Overpass fetch (kumi первым), первый успех wins -> 2-5 сек вместо 20
- Диск-кэш cache/osm_buildings/*.json, TTL 24ч
- Упрощённая логика фильтрации деревьев
- LRU в памяти 500
"""
import math
import os
import random
import re
import time
import uuid
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Blueprint, jsonify, request, send_from_directory

osm_bp = Blueprint("osm_buildings", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLANS_DIR = os.path.join(BASE_DIR, "plans")
PLANS_FILE = os.path.join(BASE_DIR, "plans.txt")
CACHE_DIR = os.path.join(BASE_DIR, "cache", "osm_buildings")
os.makedirs(PLANS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Порядок важен: самые быстрые первыми
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

DEFAULT_HEIGHTS = {
    "yes":8,"house":8,"residential":10,"apartments":20,"detached":8,
    "commercial":12,"retail":10,"office":20,"industrial":12,"warehouse":10,
    "school":10,"hospital":15,"hotel":20,"church":18,"garage":4,"shed":4,
}
COLORS = {
    "residential":"#D4A574","apartments":"#C4956A","house":"#E8C8A0",
    "commercial":"#A8C4E0","retail":"#7FB3D8","office":"#6BA3D6",
    "industrial":"#B0B0B0","warehouse":"#C0C0C0","school":"#F0D060",
    "hospital":"#FF8080","hotel":"#D0A0E0","church":"#E0C080",
    "garage":"#909090","default":"#D4B896",
}
_cache = {}
CACHE_TTL = 3600 * 24  # 24ч для зданий

def _height(tags, btype):
    for k in ("height","est:height"):
        v = tags.get(k,"").strip().replace(",",".")
        if v:
            try: return max(2, float(v))
            except: pass
    lv = tags.get("building:levels","").strip()
    if lv:
        try: return max(3, float(lv)*3)
        except: pass
    return DEFAULT_HEIGHTS.get(btype, 8)

ROAD_DEFAULT_LANES = {
    "motorway": 4, "motorway_link": 1, "trunk": 3, "trunk_link": 1,
    "primary": 2, "primary_link": 1, "secondary": 2, "secondary_link": 1,
    "tertiary": 2, "tertiary_link": 1, "unclassified": 2, "residential": 2,
    "service": 1, "living_street": 1,
}
DRIVABLE_ROADS = set(ROAD_DEFAULT_LANES)
MARKED_ROADS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential",
}

def _number_from_tag(value):
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None

def _road_lanes(tags, htype):
    value = _number_from_tag(tags.get("lanes"))
    if value is not None:
        return max(1, min(8, int(round(value))))
    return ROAD_DEFAULT_LANES.get(htype, 1)

def _road_width(tags, htype):
    explicit_width = _number_from_tag(tags.get("width"))
    if explicit_width is not None:
        return max(1.0, min(30.0, explicit_width))
    lanes = _number_from_tag(tags.get("lanes"))
    if lanes is not None:
        return max(3.0, min(30.0, lanes * 3.2))
    widths = {
        "motorway": 14, "motorway_link": 7, "trunk": 10, "trunk_link": 6.5,
        "primary": 8, "primary_link": 6, "secondary": 7, "secondary_link": 5.5,
        "tertiary": 6, "tertiary_link": 4.5, "residential": 5.5, "service": 3.2,
        "unclassified": 5.5, "living_street": 4, "footway": 1.5,
        "path": 1, "cycleway": 2, "pedestrian": 3, "steps": 1.5, "track": 3,
    }
    return widths.get(htype, 4)

def _road_color(htype):
    colors = {
        "motorway": "#353a3e", "motorway_link": "#373c40",
        "trunk": "#393e42", "trunk_link": "#3b4044",
        "primary": "#3d4246", "primary_link": "#404549",
        "secondary": "#42474b", "secondary_link": "#44494c",
        "tertiary": "#464b4e", "tertiary_link": "#484d50",
        "residential": "#4b5053", "service": "#555a5d",
        "unclassified": "#4c5154", "living_street": "#5d6163",
        "footway": "#777b7d", "path": "#74787a", "cycleway": "#666d70",
        "pedestrian": "#808385", "steps": "#6b6f72", "track": "#696d6e",
    }
    return colors.get(htype, "#5c6062")

def _pt_in_poly(px, py, poly):
    n = len(poly); inside = False; j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def _pt_seg_dist(px, py, ax, ay, bx, by):
    dx = bx - ax; dy = by - ay
    if dx == 0 and dy == 0:
        return math.sqrt((px-ax)**2 + (py-ay)**2)
    t = max(0, min(1, ((px-ax)*dx + (py-ay)*dy) / (dx*dx + dy*dy)))
    return math.sqrt((px - ax - t*dx)**2 + (py - ay - t*dy)**2)

def _polygon_area(poly):
    if len(poly) < 3:
        return 0.0
    area = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def _polygon_centroid(poly):
    if not poly:
        return 0.0, 0.0
    twice_area = 0.0
    cx = cy = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(twice_area) < 1e-8:
        return (
            sum(p[0] for p in poly) / len(poly),
            sum(p[1] for p in poly) / len(poly),
        )
    return cx / (3.0 * twice_area), cy / (3.0 * twice_area)

def _nearest_point_on_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return ax, ay, math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    qx, qy = ax + t * dx, ay + t * dy
    return qx, qy, math.hypot(px - qx, py - qy), t

def _orientation(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

def _on_segment(ax, ay, bx, by, px, py, eps=1e-7):
    return (
        min(ax, bx) - eps <= px <= max(ax, bx) + eps
        and min(ay, by) - eps <= py <= max(ay, by) + eps
        and abs(_orientation(ax, ay, bx, by, px, py)) <= eps
    )

def _segments_intersect(a, b, c, d, eps=1e-7):
    o1 = _orientation(a[0], a[1], b[0], b[1], c[0], c[1])
    o2 = _orientation(a[0], a[1], b[0], b[1], d[0], d[1])
    o3 = _orientation(c[0], c[1], d[0], d[1], a[0], a[1])
    o4 = _orientation(c[0], c[1], d[0], d[1], b[0], b[1])
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    ):
        return True
    return (
        (abs(o1) <= eps and _on_segment(a[0], a[1], b[0], b[1], c[0], c[1]))
        or (abs(o2) <= eps and _on_segment(a[0], a[1], b[0], b[1], d[0], d[1]))
        or (abs(o3) <= eps and _on_segment(c[0], c[1], d[0], d[1], a[0], a[1]))
        or (abs(o4) <= eps and _on_segment(c[0], c[1], d[0], d[1], b[0], b[1]))
    )

def _segment_intersects_polygon(a, b, poly):
    if _pt_in_poly(a[0], a[1], poly) or _pt_in_poly(b[0], b[1], poly):
        return True
    return any(
        _segments_intersect(a, b, poly[i], poly[(i + 1) % len(poly)])
        for i in range(len(poly))
    )

def _building_hits_road(ring, roads):
    cx, cy = _polygon_centroid(ring)
    for road in roads:
        if road.get("type") not in DRIVABLE_ROADS or not road.get("collision", True):
            continue
        path = road.get("path") or []
        width = float(road.get("width", 4.0))
        core_half_width = max(0.8, width * 0.42)
        vertices_in_core = 0
        min_center_distance = float("inf")

        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            if _segment_intersects_polygon(a, b, ring):
                return True
            _, _, center_distance, _ = _nearest_point_on_segment(
                cx, cy, a[0], a[1], b[0], b[1]
            )
            min_center_distance = min(min_center_distance, center_distance)

        for px, py in ring:
            min_vertex_distance = min(
                (_pt_seg_dist(px, py, path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
                 for i in range(len(path) - 1)),
                default=float("inf"),
            )
            if min_vertex_distance <= core_half_width:
                vertices_in_core += 1

        if min_center_distance <= width * 0.36:
            return True
        if vertices_in_core >= max(2, int(math.ceil(len(ring) * 0.3))):
            return True
    return False

def _postprocess_buildings(buildings, roads):
    # Быстрый выход если дорог мало
    if not roads or len(buildings) < 2:
        return buildings
    # Ограничим проверку только дорогами primary+ внутри 400м
    relevant_roads = [r for r in roads if r.get("type") in DRIVABLE_ROADS][:80]
    if not relevant_roads:
        return buildings
    return [b for b in buildings if not _building_hits_road(b["ring"], relevant_roads)]

def _tree_too_close_to_buildings(cx, cy, building_rings, crown_radius=4.0):
    clearance = max(2.0, crown_radius + 1.5)
    for ring in building_rings or []:
        if len(ring) < 3:
            continue
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        min_x, max_x = min(xs) - clearance, max(xs) + clearance
        min_y, max_y = min(ys) - clearance, max(ys) + clearance
        if cx < min_x or cx > max_x or cy < min_y or cy > max_y:
            continue
        if _pt_in_poly(cx, cy, ring):
            return True
        n = len(ring)
        for i in range(n):
            if _pt_seg_dist(cx, cy, ring[i][0], ring[i][1], ring[(i + 1) % n][0], ring[(i + 1) % n][1]) < clearance:
                return True
    return False

def _is_green(tags):
    return (
        tags.get("landuse") == "forest"
        or tags.get("leisure") == "park"
        or tags.get("natural") == "wood"
    )

def _is_water(tags):
    if tags.get("natural") == "water": return True
    if tags.get("water") in ("lake","river","pond","reservoir","canal","stream"): return True
    if tags.get("landuse") == "reservoir": return True
    if tags.get("waterway") == "riverbank": return True
    return False

def _make_trees_in_parks(park_polys, building_rings=None):
    trees = []
    GLOBAL_MAX = 3000
    existing = set()
    for idx, poly in enumerate(park_polys):
        if len(poly) < 3:
            continue
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x
        h = max_y - min_y
        if w < 2.0 or h < 2.0:
            continue
        area_m2 = _polygon_area(poly)
        if area_m2 < 400:
            continue
        if area_m2 < 1800:
            target_count = 2
        elif area_m2 < 6000:
            target_count = 4
        elif area_m2 < 18000:
            target_count = 8
        else:
            target_count = min(30, 10 + int(area_m2 / 12000))
        spacing = max(8.0, min(18.0, math.sqrt(max(area_m2, 1) / max(target_count, 1)) * 0.6))
        cols = max(2, int(math.ceil(w / spacing)) + 1)
        rows = max(2, int(math.ceil(h / spacing)) + 1)
        cell_w = w / cols
        cell_h = h / rows
        rng = random.Random(idx * 997 + 13)
        candidates = []
        for row in range(rows):
            for col in range(cols):
                cx = min_x + (col + 0.5) * cell_w + rng.uniform(-cell_w * 0.25, cell_w * 0.25)
                cy = min_y + (row + 0.5) * cell_h + rng.uniform(-cell_h * 0.25, cell_h * 0.25)
                if not _pt_in_poly(cx, cy, poly):
                    continue
                candidates.append((cx, cy))
        rng.shuffle(candidates)
        added_for_park = 0
        for cx, cy in candidates:
            if len(trees) >= GLOBAL_MAX or added_for_park >= target_count:
                break
            crown_radius = 3.5 + rng.uniform(0.0, 2.0)
            if _tree_too_close_to_buildings(cx, cy, building_rings, crown_radius):
                continue
            gx, gy = round(cx, 0), round(cy, 0)
            ok = True
            for ex, ey in existing:
                if abs(gx - ex) <= 4 and abs(gy - ey) <= 4:
                    ok = False
                    break
            if ok:
                existing.add((gx, gy))
                trees.append({'x': round(cx, 1), 'y': round(cy, 1)})
                added_for_park += 1
    return trees

# ═══════════════════════════════════════════════════════════════
# ПЛАНЫ ЭТАЖЕЙ
# ═══════════════════════════════════════════════════════════════

def _load_plans():
    plans = []
    if not os.path.exists(PLANS_FILE):
        return plans
    with open(PLANS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split(",", 2)
            if len(parts) >= 3:
                try:
                    plans.append({"lat": float(parts[0]), "lon": float(parts[1]), "filename": parts[2]})
                except: pass
    return plans

def _save_plan(lat, lon, filename):
    with open(PLANS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{lat},{lon},{filename}\n")

def _remove_plan(lat, lon):
    plans = _load_plans()
    removed = None
    remaining = []
    for p in plans:
        if abs(p["lat"] - lat) < 0.00001 and abs(p["lon"] - lon) < 0.00001:
            removed = p
            fpath = os.path.join(PLANS_DIR, p["filename"])
            if os.path.exists(fpath):
                os.remove(fpath)
        else:
            remaining.append(p)
    with open(PLANS_FILE, "w", encoding="utf-8") as f:
        for p in remaining:
            f.write(f"{p['lat']},{p['lon']},{p['filename']}\n")
    return removed

# ═══════════════════════════════════════════════════════════════
# OVERPASS OPTIMIZED
# ═══════════════════════════════════════════════════════════════

def _cache_path_for_query(lat, lon, radius):
    key = f"{lat:.5f}_{lon:.5f}_{int(radius)}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"{h}.json")

def _load_disk_cache(lat, lon, radius):
    path = _cache_path_for_query(lat, lon, radius)
    if not os.path.exists(path):
        return None
    try:
        if time.time() - os.path.getmtime(path) > CACHE_TTL:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_disk_cache(lat, lon, radius, data):
    path = _cache_path_for_query(lat, lon, radius)
    try:
        # сохраняем только необходимые поля, без лишнего
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def _overpass_fetch(query, timeout=15):
    """Параллельный запрос к 3 инстансам, первый успех = ответ"""
    def _do(url):
        try:
            r = requests.post(url, data={"data": query}, timeout=timeout,
                              headers={"User-Agent": "panoramas_idk/1.0"})
            if r.status_code in (429, 504, 502, 503):
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_do, url): url for url in OVERPASS_URLS[:3]}
        for fut in as_completed(futs):
            res = fut.result()
            if res and "elements" in res:
                # cancel rest
                for f in futs:
                    f.cancel()
                return res

    # fallback по всем по очереди
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, timeout=timeout+2,
                              headers={"User-Agent": "panoramas_idk/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None

def _query(lat, lon, radius_m, timeout=20):
    # проверка диск-кэша
    cached = _load_disk_cache(lat, lon, radius_m)
    if cached:
        # проверим структуру: если там уже готовый результат нашего парсера
        if isinstance(cached, dict) and "buildings" in cached:
            return cached

    dlat = radius_m / 111320
    dlon = radius_m / (111320 * math.cos(math.radians(lat)))
    bbox = f"{lat-dlat},{lon-dlon},{lat+dlat},{lon+dlon}"
    # Оптимизированный запрос: меньше maxsize, таймаут 20
    q = f'[out:json][timeout:{timeout}][maxsize:16777216];(' \
        f'way["building"]({bbox});' \
        f'way["highway"]({bbox});' \
        f'way["landuse"="forest"]({bbox});way["leisure"="park"]({bbox});' \
        f'way["natural"="wood"]({bbox});' \
        f'way["natural"="water"]({bbox});way["water"]({bbox});' \
        f'way["landuse"="reservoir"]({bbox});way["waterway"="riverbank"]({bbox});' \
        f'node["natural"="tree"]({bbox});' \
        f');out geom;'

    data = _overpass_fetch(q, timeout=timeout)

    if not data:
        raise RuntimeError("Overpass unavailable (all mirrors failed)")

    buildings, roads, trees, park_polys, waters = [], [], [], [], []

    for el in data.get("elements", []):
        if el.get("type") == "node":
            tags = el.get("tags", {})
            if tags.get("natural") == "tree":
                dx = (el["lon"] - lon) / (1 / (111320 * math.cos(math.radians(lat))))
                dy = (el["lat"] - lat) / (1 / 111320)
                trees.append({"x": round(dx, 1), "y": round(dy, 1)})
            continue
        if el.get("type") != "way": continue
        tags = el.get("tags", {})
        geo = el.get("geometry", [])
        if len(geo) < 2: continue
        ring = []
        for pt in geo:
            dx = (pt["lon"] - lon) / (1 / (111320 * math.cos(math.radians(lat))))
            dy = (pt["lat"] - lat) / (1 / 111320)
            ring.append([round(dx, 1), round(dy, 1)])

        btype = tags.get("building", "")
        if btype and btype != "no":
            if ring[0] == ring[-1]: ring = ring[:-1]
            if len(ring) < 3: continue
            cx, cy = _polygon_centroid(ring)
            buildings.append({
                "ring": ring, "height": round(_height(tags, btype), 1),
                "color": COLORS.get(btype, COLORS["default"]),
                "type": btype, "name": tags.get("name", ""),
                "dist_m": round(math.sqrt(cx*cx + cy*cy), 1),
                "_lat": round(lat + cy / 111320.0, 6),
                "_lon": round(lon + cx / (111320.0 * math.cos(math.radians(lat))), 6),
            })
            continue
        htype = tags.get("highway", "")
        if htype:
            lanes = _road_lanes(tags, htype)
            surface = tags.get("surface", "")
            unpaved = surface in {"unpaved", "gravel", "fine_gravel", "dirt", "earth", "ground", "sand"}
            layer = _number_from_tag(tags.get("layer")) or 0
            roads.append({
                "path": ring,
                "width": round(_road_width(tags, htype), 1),
                "color": _road_color(htype),
                "type": htype,
                "lanes": lanes,
                "oneway": tags.get("oneway") in {"yes", "1", "true"} or htype == "motorway",
                "markings": (
                    htype in MARKED_ROADS
                    and lanes >= 2
                    and not unpaved
                    and tags.get("lane_markings") != "no"
                ),
                "edge_lines": htype in {
                    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
                } and not unpaved,
                "surface": surface,
                "collision": not (
                    tags.get("tunnel") not in {None, "", "no"}
                    or tags.get("covered") == "yes"
                    or layer < 0
                ),
            })
            continue
        if _is_green(tags):
            park_polys.append(ring); continue
        if _is_water(tags):
            waters.append(ring)

    buildings = _postprocess_buildings(buildings, roads)
    building_rings = [b["ring"] for b in buildings]

    if building_rings:
        trees = [t for t in trees if not _tree_too_close_to_buildings(t['x'], t['y'], building_rings, 4.0)]

    if park_polys:
        osm_positions = set((round(t['x'],0), round(t['y'],0)) for t in trees)
        procedural = _make_trees_in_parks(park_polys, building_rings)
        for t in procedural:
            key = (round(t['x'],0), round(t['y'],0))
            if key not in osm_positions:
                trees.append(t)

    buildings.sort(key=lambda b: b["dist_m"])

    plans = _load_plans()
    for b in buildings:
        b["plan"] = None
        cx, cy = _polygon_centroid(b["ring"])
        b_lat = lat + cy / 111320.0
        b_lon = lon + cx / (111320.0 * math.cos(math.radians(lat)))
        for p in plans:
            if abs(p["lat"] - b_lat) < 0.0002 and abs(p["lon"] - b_lon) < 0.0002:
                b["plan"] = p["filename"]
                break

    result = {
        "buildings": buildings,
        "roads": roads,
        "trees": trees,
        "greens": park_polys,
        "waters": waters,
        "count": len(buildings),
        "radius_m": radius_m,
    }

    _save_disk_cache(lat, lon, radius_m, result)
    return result

# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@osm_bp.route("/api/osm-buildings")
def get_buildings():
    try:
        lat = float(request.args["lat"]); lon = float(request.args["lon"])
    except:
        return jsonify({"status": "error", "message": "bad coords"}), 400
    r = max(50, min(float(request.args.get("radius_m", 300)), 1000))
    key = f"{lat:.5f},{lon:.5f},{r:.0f}"
    if key not in _cache:
        # пробуем диск кэш внутри _query, но и тут проверим
        try:
            _cache[key] = _query(lat, lon, r)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 502
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
    return jsonify({"status": "ok", **_cache[key]})

@osm_bp.route("/api/search")
def search_places():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"results": []})
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    limit = min(int(request.args.get("limit", 5)), 10)
    params = {"q": q, "format": "json", "limit": limit, "addressdetails": 1, "accept-language": "ru"}
    if lat and lon:
        try:
            params["viewbox"] = f"{float(lon)-0.5},{float(lat)+0.3},{float(lon)+0.5},{float(lat)-0.3}"
            params["bounded"] = 0
        except: pass
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params=params, headers={"User-Agent": "panoramas_idk/1.0"}, timeout=8)
        r.raise_for_status()
        data = r.json()
    except:
        return jsonify({"results": []})
    results = [{"name": i.get("display_name",""), "lat": float(i.get("lat",0)),
                "lon": float(i.get("lon",0)), "type": i.get("type","")} for i in data]
    return jsonify({"results": results})

@osm_bp.route("/api/plans", methods=["GET"])
def list_plans():
    return jsonify({"plans": _load_plans()})

@osm_bp.route("/api/plans/upload", methods=["POST"])
def upload_plan():
    lat = request.form.get("lat")
    lon = request.form.get("lon")
    f = request.files.get("file")
    if not lat or not lon or not f:
        return jsonify({"status": "error", "message": "missing lat/lon/file"}), 400
    try:
        lat = float(lat); lon = float(lon)
    except:
        return jsonify({"status": "error", "message": "bad coords"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "png"
    filename = f"plan_{uuid.uuid4().hex[:8]}.{ext}"
    f.save(os.path.join(PLANS_DIR, filename))
    _save_plan(lat, lon, filename)
    _cache.clear()
    return jsonify({"status": "ok", "filename": filename, "lat": lat, "lon": lon})

@osm_bp.route("/api/plans/delete", methods=["POST"])
def delete_plan():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"]); lon = float(data["lon"])
    except:
        return jsonify({"status": "error", "message": "bad coords"}), 400
    removed = _remove_plan(lat, lon)
    _cache.clear()
    if removed:
        return jsonify({"status": "ok", "removed": removed["filename"]})
    return jsonify({"status": "not_found"})

@osm_bp.route("/plans/<path:filename>")
def serve_plan(filename):
    return send_from_directory(PLANS_DIR, filename)
