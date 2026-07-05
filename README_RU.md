# OSM + Яндекс панорамные точки

[English README](README.md) | [Русский README](README_RU.md)

![](screen1.jpg)
![](screen2.jpg)

Flask-приложение: карта OpenStreetMap (Leaflet), на которой администратор
отмечает точки (сохраняются в SQLite и видны всем посетителям). При клике
по точке сервер скачивает панораму Яндекс.Карт для этих координат и
показывает её во встроенном 360°-вьювере (Pannellum).

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
|---------------------|------------------------------------------|----------------|
| `ADMIN_PASSWORD`    | Пароль администратора                    | `admin123`     |
| `FLASK_SECRET_KEY`  | Секрет для сессий Flask                  | небезопасный dev-ключ |
| `PANO_ZOOM`         | Уровень зума панорамы                   | `2` |

## Запуск

```bash
export ADMIN_PASSWORD=мойсекретныйпароль
export FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
python3 app.py
```

Откройте http://localhost:5000

## Как пользоваться

- **Обычный посетитель**: видит все сохранённые точки на карте, клик по точке — открывается панорама (если Яндекс её нашёл поблизости).
- **Администратор**: вход по кнопке «Вход для администратора» → пароль из `ADMIN_PASSWORD` → появляется режим «Добавить точку». После клика по карте открывается форма с названием/описанием и превью панорамы в этой точке. Сохранённые точки доступны всем.

## Структура проекта

```text
panorama-app/
├── app.py                 # Flask-бэкенд: точки (CRUD), сессии, /api/panorama
├── pano_downloader.py      # скачивание/сборка панорам
├── results_8cat_tyumen_v88.csv  # исходные POI для сводки
├── requirements.txt
├── points.db               # создаётся автоматически (SQLite)
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    ├── js/map.js
    └── panoramas/          # кэш собранных JPG-панорам
```

