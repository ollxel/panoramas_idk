"""
osm_buildings.py — OSM здания + дороги + деревья + водоёмы + планы + Flask endpoint
"""
import math, os, time, uuid, requests
from flask import Blueprint, jsonify, request, send_from_directory

osm_bp = Blueprint("osm_buildings", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLANS_DIR = os.path.join(BASE_DIR, "plans")
PLANS_FILE = os.path.join(BASE_DIR, "plans.txt")
os.makedirs(PLANS_DIR, exist_ok=True)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
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

def _road_width(tags, htype):
    lanes = tags.get("lanes","")
    if lanes:
        try: return max(3, float(lanes)*3.5)
        except: pass
    widths = {"motorway":14,"trunk":10,"primary":8,"secondary":7,"tertiary":6,"residential":5,"service":3,"unclassified":5,"living_street":4,"footway":1.5,"path":1,"cycleway":2,"pedestrian":3,"steps":1.5,"track":3}
    return widths.get(htype, 4)

def _road_color(htype):
    colors = {"motorway":"#e892a2","trunk":"#f9b29c","primary":"#fcd6a4","secondary":"#f7fabf","tertiary":"#ffffff","residential":"#ffffff","service":"#eeeeee","unclassified":"#ffffff","living_street":"#ffe8e8","footway":"#999999","path":"#aaaaaa","cycleway":"#6699ff","pedestrian":"#cccccc","steps":"#888888","track":"#c8a882"}
    return colors.get(htype, "#cccccc")

def _pt_in_poly(px, py, poly):
    n = len(poly); inside = False; j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def _is_green(tags):
    if tags.get("landuse") in ("forest","grass","meadow","village_green","recreation_ground"):
        return True
    if tags.get("leisure") in ("park","garden","recreation_ground"):
        return True
    if tags.get("natural") in ("wood","grassland","scrub"):
        return True
    return False

def _is_water(tags):
    if tags.get("natural") == "water": return True
    if tags.get("water") in ("lake","river","pond","reservoir","canal","stream"): return True
    if tags.get("landuse") == "reservoir": return True
    if tags.get("waterway") == "riverbank": return True
    return False

def _make_trees_in_parks(park_polys):
    """
    Grid-based деревья с зазорами. Плотность зависит от размера парка.
    Маленькие скверы → плотно. Большие парки → с зазорами но покрывают всю территорию.
    """
    import random
    trees = []
    GLOBAL_MAX = 2000

    for idx, poly in enumerate(park_polys):
        if len(poly) < 3: continue
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x; h = max_y - min_y
        if w < 2.0 or h < 2.0: continue

        area = w * h
        # Плотность: sqrt(area) * коэф — даёт больше деревьев на больших площадях
        # но не линейно, а с убывающей плотностью
        gap = max(3.0, min(8.0, math.sqrt(area) * 0.1))  # зазор 3-8м в зависимости от размера
        target = max(5, int(area / (gap * gap * 1.5)))  # ~45% покрытие решёткой
        target = min(target, 500)

        rng = random.Random(idx * 997 + 13)
        placed = []

        # Grid-based placement: равномерно по bounding box, потом проверяем in_poly + зазор
        cols = max(1, int(w / gap))
        rows = max(1, int(h / gap))
        cell_w = w / cols
        cell_h = h / rows

        for row in range(rows):
            for col in range(cols):
                if len(placed) >= target or len(trees) >= GLOBAL_MAX:
                    break
                # Центр ячейки + случайный сдвиг внутри ячейки
                cx = min_x + (col + 0.5) * cell_w + rng.uniform(-cell_w * 0.35, cell_w * 0.35)
                cy = min_y + (row + 0.5) * cell_h + rng.uniform(-cell_h * 0.35, cell_h * 0.35)
                if not _pt_in_poly(cx, cy, poly):
                    continue
                # Проверяем зазор
                ok = True
                for px, py in placed:
                    if (cx - px) ** 2 + (cy - py) ** 2 < gap * gap * 0.8:
                        ok = False; break
                if ok:
                    placed.append((cx, cy))
                    trees.append({"x": round(cx, 1), "y": round(cy, 1)})

    return trees

# ═══════════════════════════════════════════════════════════════
# ПЛАНЫ ЭТАЖЕЙ (plans.txt + plans/)
# ═══════════════════════════════════════════════════════════════

def _load_plans():
    """Читает plans.txt → [{lat, lon, filename}]"""
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
    """Удаляет план по координатам."""
    plans = _load_plans()
    removed = None
    remaining = []
    for p in plans:
        if abs(p["lat"] - lat) < 0.00001 and abs(p["lon"] - lon) < 0.00001:
            removed = p
            # Удаляем файл
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
# OVERPASS + OSM
# ═══════════════════════════════════════════════════════════════

def _query(lat, lon, radius_m, timeout=45):
    dlat = radius_m / 111320
    dlon = radius_m / (111320 * math.cos(math.radians(lat)))
    bbox = f"{lat-dlat},{lon-dlon},{lat+dlat},{lon+dlon}"
    q = f'[out:json][timeout:{timeout}][maxsize:32000000];(' \
        f'way["building"]({bbox});' \
        f'way["highway"]({bbox});' \
        f'way["landuse"="forest"]({bbox});way["leisure"="park"]({bbox});' \
        f'way["leisure"="garden"]({bbox});way["leisure"="recreation_ground"]({bbox});' \
        f'way["natural"="wood"]({bbox});way["natural"="grassland"]({bbox});way["natural"="scrub"]({bbox});' \
        f'way["landuse"="grass"]({bbox});way["landuse"="meadow"]({bbox});' \
        f'way["landuse"="village_green"]({bbox});way["landuse"="recreation_ground"]({bbox});' \
        f'way["natural"="water"]({bbox});way["water"]({bbox});' \
        f'way["landuse"="reservoir"]({bbox});way["waterway"="riverbank"]({bbox});' \
        f'node["natural"="tree"]({bbox});' \
        f');out geom;'

    data = None; errors = []
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, timeout=timeout+15, headers={"User-Agent":"panoramas_idk/1.0"})
            r.raise_for_status()
            data = r.json(); break
        except Exception as e:
            errors.append(f"{url}: {e}"); time.sleep(1)
    if not data:
        raise RuntimeError("Overpass unavailable: " + "; ".join(errors[-2:]))

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
            cx = sum(p[0] for p in ring) / len(ring)
            cy = sum(p[1] for p in ring) / len(ring)
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
            roads.append({"path": ring, "width": _road_width(tags, htype), "color": _road_color(htype), "type": htype})
            continue
        if _is_green(tags):
            park_polys.append(ring); continue
        if _is_water(tags):
            waters.append(ring)

    if not trees and park_polys:
        trees = _make_trees_in_parks(park_polys)

    buildings.sort(key=lambda b: b["dist_m"])

    # Добавляем планы к зданиям
    plans = _load_plans()
    for b in buildings:
        b["plan"] = None
        cx = sum(p[0] for p in b["ring"]) / len(b["ring"])
        cy = sum(p[1] for p in b["ring"]) / len(b["ring"])
        # Переводим обратно в lat/lon для сравнения с plans.txt
        b_lat = lat + cy / 111320.0
        b_lon = lon + cx / (111320.0 * math.cos(math.radians(lat)))
        for p in plans:
            if abs(p["lat"] - b_lat) < 0.0002 and abs(p["lon"] - b_lon) < 0.0002:
                b["plan"] = p["filename"]
                break

    return {"buildings": buildings, "roads": roads, "trees": trees, "waters": waters, "count": len(buildings), "radius_m": radius_m}

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
        try: _cache[key] = _query(lat, lon, r)
        except Exception as e: return jsonify({"status": "error", "message": str(e)}), 502
        if len(_cache) > 500: _cache.pop(next(iter(_cache)))
    return jsonify({"status": "ok", **_cache[key]})

@osm_bp.route("/api/plans", methods=["GET"])
def list_plans():
    """Все планы."""
    return jsonify({"plans": _load_plans()})

@osm_bp.route("/api/plans/upload", methods=["POST"])
def upload_plan():
    """Загрузка плана этажа. Форма: lat, lon, file."""
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
    _cache.clear()  # сбрасываем кэш чтобы планы обновились
    return jsonify({"status": "ok", "filename": filename, "lat": lat, "lon": lon})

@osm_bp.route("/api/plans/delete", methods=["POST"])
def delete_plan():
    """Удаление плана по координатам."""
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
    """Отдаёт файл плана."""
    return send_from_directory(PLANS_DIR, filename)
