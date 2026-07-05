# OSM + Yandex Panorama Points

[English README](README.md) | [Russian README](README_RU.md)

A Flask application featuring an OpenStreetMap (Leaflet) map where an administrator can place points (stored in SQLite and visible to all visitors). When a user clicks on a point, the server downloads the corresponding Yandex Maps panorama for those coordinates and displays it in an embedded 360° viewer powered by Pannellum.

## How Panorama Retrieval Works

Panorama downloading and stitching are ported from the repository
[zer0-dev/yandex-pano-downloader](https://github.com/zer0-dev/yandex-pano-downloader)
(`pano_downloader.py`, MIT License).

1. A request is sent to:

   ```
   https://api-maps.yandex.ru/services/panoramas/1.x/?l=stv&ll={lon},{lat}&provider=streetview
   ```

   This returns metadata for the nearest panorama (`imageId`, tile dimensions, zoom levels).
   **No API key is required.**

2. Panorama tiles are downloaded from:

   ```
   https://pano.maps.yandex.net/{imageId}/{zoom}.{x}.{y}
   ```

   and stitched into a single JPEG image.

3. The resulting image is cached in `static/panoramas/` and served by Flask as a regular static file.

4. On the frontend, the equirectangular image is rendered as an interactive 360° panorama using
   [Pannellum](https://pannellum.org/) (loaded via CDN, no API keys required).

## Installation

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Environment variables (can be set via a `.env` file using `python-dotenv` or exported directly in the shell):

| Variable | Description | Default |
|----------|-------------|---------|
| `ADMIN_PASSWORD` | Administrator password | `admin123` |
| `FLASK_SECRET_KEY` | Secret key for Flask sessions | Insecure development key |
| `PANO_ZOOM` | Panorama zoom level | `2` |

## Running

```bash
export ADMIN_PASSWORD=my_secure_password
export FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 app.py
```

Open:

http://localhost:5000

## Usage

- **Visitor:** Can view all saved points on the map. Clicking a point opens the panorama (if Yandex provides one nearby).
- **Administrator:** Click **"Administrator Login"**, enter the password specified in `ADMIN_PASSWORD`, and the **"Add Point"** mode becomes available. Clicking on the map opens a form where you can enter a title, description, and preview the panorama for that location. Saved points become visible to all visitors.

## Project Structure

```text
panorama-app/
├── app.py                      # Flask backend: points (CRUD), sessions, /api/panorama
├── pano_downloader.py          # Panorama downloading and stitching
├── results_8cat_tyumen_v88.csv # Source POI dataset
├── requirements.txt
├── points.db                   # Automatically created SQLite database
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    ├── js/map.js
    └── panoramas/              # Cache of generated panorama JPEGs
```
