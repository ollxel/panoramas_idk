# OSM + Яндекс панорамные точки

[English README](README.md) | [Русский README](README_RU.md)

![](screen1.png)

Flask-приложение, которое объединяет карту Leaflet/OpenStreetMap, загрузку панорам Яндекс.Карт, экспериментальный 3D-вид зданий OSM, сводку ближайших POI и загрузку планов этажей. Проект позволяет изучать место и с карты, и со стороны улицы, переключаясь между панорамой и 3D-режимом.

## Как это работает с панорамами

Скачивание и сборка панорамы портированы из репозитория
[zer0-dev/yandex-pano-downloader](https://github.com/zer0-dev/yandex-pano-downloader)
(файл `pano_downloader.py`, лицензия MIT).

1. Запрос к `https://api-maps.yandex.ru/services/panoramas/1.x/?l=stv&ll={lon},{lat}&provider=streetview`
   — отдаёт метаданные ближайшей панорамы (`imageId`, размеры тайлов, уровни зума).
   **Apikey не требуется**.
2. Тайлы панорамы скачиваются с `https://pano.maps.yandex.net/{imageId}/{zoom}.{x}.{y}`
   и склеиваются в одну JPEG-картинку.
3. Готовая картинка кэшируется в `static/panoramas/` и раздаётся Flask'ом как
   обычный статический файл.
4. На фронтенде эта equirectangular-картинка разворачивается в интерактивную
   360°-панораму библиотекой [Pannellum](https://pannellum.org/) (CDN, без ключей).

## Установка

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

Переменные окружения (можно задать через `.env` + `python-dotenv`, либо
экспортом в шелле):

| Переменная         | Назначение                              | По умолчанию   |
|--------------------|-----------------------------------------|----------------|
| `ADMIN_PASSWORD`   | Пароль администратора                   | `admin123`     |
| `FLASK_SECRET_KEY` | Секрет для сессий Flask                 | небезопасный dev-ключ |
| `PANO_ZOOM`        | Уровень зума панорамы                   | `2` |
| `DISABLE_PANO_CACHE_CLEAN` | Отключить очистку кэша панорам при старте | `0` |

## Запуск

```bash
export ADMIN_PASSWORD=мойсекретныйпароль
export FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 app.py
```

Откройте http://localhost:8080

![](screen2.png)

## Как пользоваться

- **Обычный посетитель**: видит карту, сохранённые точки, может открыть панораму или экспериментальный 3D-вид OSM для выбранной локации.
- **Администратор**: вход по кнопке «Вход для администратора», пароль из `ADMIN_PASSWORD`, затем можно добавлять и удалять точки. В интерфейсе также доступны сводка ближайших POI и загрузка планов этажей для зданий.

## Структура проекта

```text
panorama-app/
├── app.py                      # Flask-бэкенд: точки, сессии, API панорам и POI
├── osm_buildings.py            # здания OSM, дороги, деревья, вода, планы и API 3D-данных
├── poi_parser.py               # быстрый парсер POI по запросам Overpass
├── pano_downloader.py          # скачивание/сборка панорам
├── results_8cat_tyumen_v88.csv # исходные POI для сводки
├── requirements.txt
├── points.db                   # создаётся автоматически (SQLite)
├── markers/                    # иконки POI-маркеров
├── plans/                      # загруженные планы этажей
├── templates/
│   └── index.html
└── static/
    ├── css/
    ├── js/
    └── panoramas/              # кэш собранных JPG-панорам
```

