# OSM + Yandex Panorama Points

[English README](README.md) | [Russian README](README_RU.md)

![](screen1.png)

A Flask application that combines a Leaflet/OpenStreetMap map, Yandex panorama retrieval, an experimental 3D view of OSM buildings, nearby POI summaries, and floor-plan uploads. The project is designed for exploring a place from both the map and the street level, with the ability to switch between panorama and 3D modes.

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
| `DISABLE_PANO_CACHE_CLEAN` | Disable automatic panorama cache cleanup on startup | `0` |

## Running

```bash
export ADMIN_PASSWORD=my_secure_password
export FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 app.py
```

Open:

http://localhost:8080

![](screen2.png)

## Usage

- **Visitor:** Can browse the map, view saved points, and open a panorama or an experimental 3D OSM scene for a location.
- **Administrator:** Use the login panel, enter the password from `ADMIN_PASSWORD`, and then add or delete points. The interface can also show nearby POI summaries and load floor-plan images for buildings.

## Project Structure

```text
panorama-app/
├── app.py                      # Flask backend: points, sessions, panorama API, POI summary API
├── osm_buildings.py            # OSM buildings, roads, trees, water, plans, and 3D data API
├── poi_parser.py               # Fast POI parser based on Overpass queries
├── pano_downloader.py          # Panorama downloading and stitching
├── results_8cat_tyumen_v88.csv # Source POI dataset
├── requirements.txt
├── points.db                   # Automatically created SQLite database
├── markers/                    # POI marker icons
├── plans/                      # Uploaded floor-plan images
├── templates/
│   └── index.html
└── static/
    ├── css/
    ├── js/
    └── panoramas/              # Cache of generated panorama JPEGs
```
