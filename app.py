"""
OSM + Yandex Panorama Points
=============================
Flask-приложение:
  - показывает карту OpenStreetMap (Leaflet) со всеми сохранёнными точками;
  - админ (по паролю) может добавлять/удалять точки, кликая по карте;
  - при клике по точке (админом при добавлении или любым пользователем на
    сохранённой точке) сервер скачивает панораму Яндекс.Карт для этих
    координат (см. pano_downloader.py, портировано из репозитория
    zer0-dev/yandex-pano-downloader) и отдаёт её как обычную JPG-картинку,
    которая на фронтенде разворачивается в 360°-вьювере (Pannellum).

Хранилище: SQLite (файл points.db создаётся автоматически).
Кэш собранных панорам: static/panoramas/<image_id>_<zoom>.jpg
"""

import csv
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import wraps
from math import asin, cos, radians, sin, sqrt

from flask import Flask, g, jsonify, render_template, request, send_from_directory, session

from pano_downloader import (
    DEFAULT_TILE_LIMIT,
    PanoramaLayerError,
    PanoramaNotFound,
    download_panorama,
    fetch_airship_ids_for_bbox,
    fetch_panorama_meta,
    fetch_panorama_meta_by_id,
)

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "points.db")
PANO_CACHE_DIR = os.path.join(BASE_DIR, "static", "panoramas")
# Category POI marker icons (PNG pins), served from a project-root "markers/"
# folder rather than "static/" so it's easy to swap/update independently.
MARKERS_DIR = os.path.join(BASE_DIR, "markers")

# Кэш панорам чистим при старте, чтобы после правок/перезапуска
# не оставались старые JPG и пользователь всегда видел актуальные данные.
# Отключить можно env DISABLE_PANO_CACHE_CLEAN=1
DISABLE_PANO_CACHE_CLEAN = os.environ.get("DISABLE_PANO_CACHE_CLEAN", "") in {"1", "true", "yes"}


def _maybe_clean_pano_cache() -> None:
    if DISABLE_PANO_CACHE_CLEAN:
        return
    try:
        # удаляем только папку кэша панорам
        if os.path.isdir(PANO_CACHE_DIR):
            import shutil

            shutil.rmtree(PANO_CACHE_DIR)
        os.makedirs(PANO_CACHE_DIR, exist_ok=True)
    except Exception:
        # кэш очистить не удалось — работаем как есть
        pass


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-me-please")

# Пароль администратора (в проде — храните хэш, это упрощённый вариант)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# Уровень зума панорамы: 0 — самый большой/детальный (см. README репозитория
# zer0-dev/yandex-pano-downloader). Чем выше — тем меньше картинка и быстрее скачивание.
DEFAULT_PANO_ZOOM = int(os.environ.get("PANO_ZOOM", "2"))

os.makedirs(PANO_CACHE_DIR, exist_ok=True)


# --------------------------------------------------------------------------
# База данных
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS points (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT DEFAULT '',
                lat         REAL NOT NULL,
                lon         REAL NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        db.commit()


# --------------------------------------------------------------------------
# Вспомогательное: проверка прав администратора
# --------------------------------------------------------------------------

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "Требуются права администратора"}), 403
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------
# Страницы
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        is_admin=bool(session.get("is_admin")),
    )


# --------------------------------------------------------------------------
# Авторизация администратора
# --------------------------------------------------------------------------

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if password and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Неверный пароль"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("is_admin", None)
    return jsonify({"ok": True})


@app.route("/api/session", methods=["GET"])
def session_status():
    return jsonify({"is_admin": bool(session.get("is_admin"))})


# --------------------------------------------------------------------------
# Точки: CRUD
# --------------------------------------------------------------------------

@app.route("/api/points", methods=["GET"])
def list_points():
    """Отдаём все точки — их видят все пользователи сайта."""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, description, lat, lon, created_at FROM points ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/points", methods=["POST"])
@admin_required
def create_point():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Некорректные координаты"}), 400

    title = (data.get("title") or "").strip() or f"Точка {lat:.5f}, {lon:.5f}"
    description = (data.get("description") or "").strip()

    db = get_db()
    cur = db.execute(
        "INSERT INTO points (title, description, lat, lon, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, description, lat, lon, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    return jsonify(
        {
            "id": cur.lastrowid,
            "title": title,
            "description": description,
            "lat": lat,
            "lon": lon,
        }
    ), 201


@app.route("/api/points/<int:point_id>", methods=["DELETE"])
@admin_required
def delete_point(point_id):
    db = get_db()
    db.execute("DELETE FROM points WHERE id = ?", (point_id,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# POI (из CSV results_8cat_tyumen_v88.csv) + сводка ближайших объектов
# --------------------------------------------------------------------------

POI_CSV_PATH = os.path.join(BASE_DIR, "results_8cat_tyumen_v88.csv")

# Русские названия категорий для отображения в сводке.
# Соответствие:
#   - pvz -> ПВЗ
#   - groceries -> Продукты
#   - shopping_mall -> Торговые центры
POI_CATEGORIES = {
    "pvz": "ПВЗ",
    "groceries": "Продукты",
    "shopping_mall": "Торговые центры",
    "restaurants": "Рестораны",
    "daycare": "Детские сады",
    "school": "Школы",
    "pharmacy": "Аптеки",
    "atm": "Банкоматы",
    "gas": "АЗС",
    "sport": "Спорт",
    "cafe": "Кафе",
}

_poi_by_category = None
_poi_parse_info = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Расстояние по поверхности земли (Haversine) в метрах.
    """
    r = 6371000.0
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lam = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lam / 2) ** 2
    c = 2 * asin(min(1.0, sqrt(a)))
    return r * c


def _pick_first(row: dict, keys: list[str]) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return ""


def _parse_float(value) -> float | None:
    try:
        v = float(value)
        return v
    except (TypeError, ValueError):
        return None


def _parse_poi_csv_once() -> None:
    global _poi_by_category, _poi_parse_info
    if _poi_by_category is not None:
        return

    poi_by_category = {key: [] for key in POI_CATEGORIES.keys()}
    parse_info = {
        "csv_path": POI_CSV_PATH,
        "loaded": False,
        "fieldnames": None,
        "rows_total": 0,
        "rows_used": 0,
        "by_category": {key: 0 for key in POI_CATEGORIES.keys()},
        "errors": [],
    }

    if not os.path.exists(POI_CSV_PATH):
        _poi_by_category = poi_by_category
        _poi_parse_info = parse_info
        return

    with open(POI_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        parse_info["fieldnames"] = list(reader.fieldnames or [])
        for row in reader:
            parse_info["rows_total"] += 1

            category_key = _pick_first(
                row,
                ["category_key", "categoryKey", "category", "type"],
            ).strip()
            if category_key not in POI_CATEGORIES:
                continue

            lat = _parse_float(_pick_first(row, ["lat", "latitude", "Lat"]))
            lon = _parse_float(_pick_first(row, ["lon", "lng", "longitude", "Lon"]))

            if lat is None or lon is None:
                continue

            title = _pick_first(row, ["title", "name", "object_name"]).strip()
            org_url = _pick_first(row, ["org_url", "orgUrl", "url", "org"]).strip()

            if not title:
                continue

            poi_by_category[category_key].append(
                {"title": title, "lat": lat, "lon": lon, "org_url": org_url}
            )
            parse_info["rows_used"] += 1
            parse_info["by_category"][category_key] += 1

    parse_info["loaded"] = True
    _poi_by_category = poi_by_category
    _poi_parse_info = parse_info


@app.route("/api/poi-summary", methods=["GET"])
def poi_summary():
    """
    Ищет в радиусе radius_m ближайшие объекты по каждой категории.
    Возвращает структуру:
      { radius_m, categories: [{key,name,items:[{title,dist_m,org_url,lat,lon}]}] }
    """
    _parse_poi_csv_once()

    if not _poi_by_category:
        return jsonify({"status": "error", "message": "POI данные не загружены"}), 500
    if _poi_parse_info and not _poi_parse_info.get("loaded"):
        return jsonify({"status": "error", "message": "POI CSV не найден или не прочитан"}), 500

    try:
        target_lat = float(request.args.get("lat"))
        target_lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Некорректные координаты"}), 400

    radius_m = request.args.get("radius_m", type=float, default=500.0)
    if radius_m <= 0:
        radius_m = 100.0

    categories_out = []
    for key, cat_name in POI_CATEGORIES.items():
        items = []
        for poi in _poi_by_category.get(key, []):
            dist_m = _haversine_m(target_lat, target_lon, poi["lat"], poi["lon"])
            if dist_m <= radius_m:
                items.append(
                    {
                        "title": poi["title"],
                        "dist_m": round(dist_m, 1),
                        "org_url": poi.get("org_url") or "",
                        "lat": poi["lat"],
                        "lon": poi["lon"],
                    }
                )
        items.sort(key=lambda x: x["dist_m"])
        categories_out.append(
            {"key": key, "name": cat_name, "count": len(items), "items": items}
        )

    # если данных по всем категориям пусто — возвращаем ошибку с контекстом,
    # чтобы фронт не показывал "0 везде" без причин.
    if _poi_parse_info:
        total_used = sum(_poi_parse_info.get("by_category", {}).values())
        if total_used > 0 and all(c.get("count", 0) == 0 for c in categories_out):
            # не "ошибка", но полезно подсветить
            pass

    return jsonify({"status": "ok", "radius_m": radius_m, "categories": categories_out, "poi_loaded": True})


# --------------------------------------------------------------------------
# Иконки POI-категорий (маркеры внутри панорамного оверлея)
# --------------------------------------------------------------------------

# key = category_key (совпадает с POI_CATEGORIES выше), value = имя файла
# в папке markers/. Категории без записи здесь остаются с дефолтной точкой.
POI_CATEGORY_ICONS = {
    "school": "school.png",
    "atm": "atm.png",
    "cafe": "cafes.png",
    "groceries": "groceries.png",
    "daycare": "kindergarten.png",
    "pharmacy": "pharmacy.png",
    "pvz": "pvz.png",
    "restaurants": "restaraunt.png",
    "shopping_mall": "sc.png",
    "gas": "gas.png",   # иконки пока нет
    "sport": "sport.png" # иконки пока нет
}


@app.route("/markers/<path:filename>")
def marker_icon(filename):
    """Отдаёт PNG-иконки категорий из папки markers/ в корне проекта."""
    return send_from_directory(MARKERS_DIR, filename)


@app.route("/api/poi-icons", methods=["GET"])
def poi_icons():
    """category_key -> URL иконки, только для категорий с реальным файлом на диске."""
    icons = {}
    for key, filename in POI_CATEGORY_ICONS.items():
        if os.path.exists(os.path.join(MARKERS_DIR, filename)):
            icons[key] = f"/markers/{filename}"
    return jsonify(icons)


# --------------------------------------------------------------------------
# Панорамы (портировано из zer0-dev/yandex-pano-downloader, см. pano_downloader.py)
#
# Используется недокументированный эндпоинт Яндекс.Карт без apikey — на свой
# страх и риск, Яндекс не гарантирует стабильность этого способа.
# --------------------------------------------------------------------------

def _cache_path(image_id: str, zoom: int) -> str:
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in image_id)
    filename = f"{safe_id}_{zoom}.jpg"
    return os.path.join(PANO_CACHE_DIR, filename)


@app.route("/api/panorama", methods=["GET"])
def get_panorama():
    """
    Возвращает готовую воздушную панораму: либо по точному panoramaId (id),
    либо по ближайшей sta-панораме рядом с lat/lon.
    """
    pano_id = (request.args.get("id") or request.args.get("pano_id") or "").strip()
    zoom = request.args.get("zoom", type=int, default=DEFAULT_PANO_ZOOM)

    # Сначала лёгкий запрос метаданных — узнаём image_id, не скачивая тайлы,
    # чтобы проверить кэш и не гонять сеть впустую.
    try:
        if pano_id:
            meta = fetch_panorama_meta_by_id(pano_id, layer="sta")
            lat = lon = None
        else:
            try:
                lat = float(request.args.get("lat"))
                lon = float(request.args.get("lon"))
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Некорректные координаты"}), 400
            meta = fetch_panorama_meta(lat, lon, layer="sta")
    except PanoramaNotFound:
        return jsonify({"status": "not_found"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502

    max_width = 8192
    if zoom < len(meta["zooms"]):
        width = meta["zooms"][zoom]["width"]
        while width > max_width and zoom + 1 < len(meta["zooms"]):
            zoom += 1
            width = meta["zooms"][zoom]["width"]

    if zoom != int(request.args.get("zoom", default=DEFAULT_PANO_ZOOM)):
        cache_file = _cache_path(meta["image_id"], zoom)
    else:
        cache_file = _cache_path(meta["image_id"], zoom)
    rel_url = f"/static/panoramas/{os.path.basename(cache_file)}"

    # Надёжность кэша: некоторые JPG могут быть битые (тогда Pannellum/WebGL падает).
    # Поэтому, если файл есть — проверяем валидность. Если битый — пересобираем.
    def _is_valid_jpg(path: str) -> bool:
        try:
            from PIL import Image

            im = Image.open(path)
            im.verify()  # не декодирует полностью, но проверяет целостность
            return True
        except Exception:
            return False

    need_download = not os.path.exists(cache_file) or not _is_valid_jpg(cache_file)

    # Опция: принудительная пересборка, если клиент просит ?force=1
    force = str(request.args.get("force", "")).lower() in {"1", "true", "yes"}
    if force:
        need_download = True

    if need_download:
        try:
            # Если был битый файл — удалим, чтобы не было шансов отдать его снова.
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                except Exception:
                    pass

            download_panorama(
                lat,
                lon,
                cache_file,
                zoom=zoom,
                layer="sta",
                pano_id=pano_id or None,
            )
        except PanoramaNotFound:
            return jsonify({"status": "not_found"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 502


    pano_point = meta.get("pano_point") or {}
    return jsonify(
        {
            "status": "ready",
            "url": rel_url,
            "panorama_id": meta.get("panorama_id"),
            "image_id": meta["image_id"],
            "pano_lat": pano_point.get("lat"),
            "pano_lon": pano_point.get("lon"),
            "name": meta.get("name") or "",
            "height": meta.get("height"),
        }
    )


def _point_from_airship_meta(meta):
    pano_point = meta.get("pano_point") or {}
    lat = pano_point.get("lat")
    lon = pano_point.get("lon")
    if lat is None or lon is None:
        return None

    timestamp = meta.get("timestamp")
    captured_at = None
    if timestamp:
        captured_at = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()

    title = meta.get("name") or "Воздушная панорама"
    description_parts = []
    if meta.get("height") is not None:
        description_parts.append(f"Высота: {round(float(meta['height']))} м")
    if captured_at:
        description_parts.append(f"Дата съёмки: {captured_at}")

    return {
        "id": meta.get("panorama_id"),
        "title": title,
        "description": " · ".join(description_parts),
        "lat": lat,
        "lon": lon,
        "height": meta.get("height"),
        "captured_at": captured_at,
    }


@app.route("/api/sky-panoramas", methods=["GET"])
def list_sky_panoramas():
    """
    Возвращает доступные воздушные панорамы Яндекса в текущем bbox карты.
    bbox: west,south,east,north (lon/lat).
    """
    raw_bbox = request.args.get("bbox", "")
    try:
        west, south, east, north = [float(value) for value in raw_bbox.split(",")]
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Некорректный bbox"}), 400

    zoom = request.args.get("zoom", type=int, default=12)
    max_tiles = request.args.get("max_tiles", type=int, default=DEFAULT_TILE_LIMIT)
    max_tiles = max(1, min(max_tiles, 256))
    max_ids = request.args.get("max_ids", type=int, default=500)
    max_ids = max(1, min(max_ids, 1000))

    try:
        tile_data = fetch_airship_ids_for_bbox(
            west,
            south,
            east,
            north,
            map_zoom=zoom,
            max_tiles=max_tiles,
        )
    except PanoramaLayerError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502

    pano_ids = tile_data["ids"][:max_ids]
    points = []

    def load_meta(pano_id):
        return fetch_panorama_meta_by_id(pano_id, layer="sta")

    # Снижаем параллелизм, чтобы не упираться в лимиты "too many open files"
    # при серии запросов с фронта.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(load_meta, pano_id): pano_id for pano_id in pano_ids}
        for future in as_completed(futures):
            try:
                point = _point_from_airship_meta(future.result())
            except Exception:
                continue
            if not point:
                continue
            if south <= point["lat"] <= north and west <= point["lon"] <= east:
                points.append(point)

    points.sort(key=lambda point: (point["title"], point["id"] or ""))

    return jsonify(
        {
            "status": "ok",
            "points": points,
            "tile_zoom": tile_data["tile_zoom"],
            "tile_count": tile_data["tile_count"],
            "partial": bool(tile_data["partial"] or len(tile_data["ids"]) > len(pano_ids)),
            "checked_ids": len(pano_ids),
            "total_tile_ids": len(tile_data["ids"]),
        }
    )


# --------------------------------------------------------------------------

if __name__ == "__main__":
    _maybe_clean_pano_cache()
    init_db()
    app.run(debug=True, host="0.0.0.0", port=8080)