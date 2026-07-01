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

import os
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, g, jsonify, render_template, request, session

from pano_downloader import PanoramaNotFound, download_panorama, fetch_panorama_meta

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "points.db")
PANO_CACHE_DIR = os.path.join(BASE_DIR, "static", "panoramas")

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
    Возвращает готовую панораму для точки (lat, lon): либо отдаёт закэшированный
    файл, либо скачивает/собирает её на лету через pano_downloader и кэширует.
    """
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Некорректные координаты"}), 400

    zoom = request.args.get("zoom", type=int, default=DEFAULT_PANO_ZOOM)

    # Сначала лёгкий запрос метаданных — узнаём image_id, не скачивая тайлы,
    # чтобы проверить кэш и не гонять сеть впустую.
    try:
        meta = fetch_panorama_meta(lat, lon)
    except PanoramaNotFound:
        return jsonify({"status": "not_found"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502

    cache_file = _cache_path(meta["image_id"], zoom)
    rel_url = f"/static/panoramas/{os.path.basename(cache_file)}"

    if not os.path.exists(cache_file):
        try:
            download_panorama(lat, lon, cache_file, zoom=zoom)
        except PanoramaNotFound:
            return jsonify({"status": "not_found"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 502

    pano_point = meta.get("pano_point") or {}
    return jsonify(
        {
            "status": "ready",
            "url": rel_url,
            "image_id": meta["image_id"],
            "pano_lat": pano_point.get("lat"),
            "pano_lon": pano_point.get("lon"),
        }
    )


# --------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=8080)
