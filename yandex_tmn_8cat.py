#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Yandex Maps – 8 categories ONLY – v8.8
# Changes vs v8.7:
#   * INFINITE scroll: keeps scrolling the results list + clicking "Показать ещё"
#     until no new cards appear (with a stall guard), so it grabs the WHOLE list.
#   * COORDINATES guaranteed: pulled from embedded JSON state first; for any org
#     still missing coords, opens the org card in a tab and reads ll= / JSON.
#   * CSV keeps ONE link column (org_url). Dropped source_category_url.
#
# Categories (Tyumen):
#  pharmacy 184105932 | atm 184105402 | gas_station 184105274 | sports_club 184107297
#  cafe 184106390 | shopping_mall 184108083 | supermarket 184108079 | restaurant 184106394
#
# Usage:
#   python yandex_tmn_8cat.py --city tyumen --ru
#   python yandex_tmn_8cat.py --city tyumen --ru --z 13
#   python yandex_tmn_8cat.py --dry-run

import argparse, time, re, json, csv, sys
from pathlib import Path

CATS = [
    {"key": "pharmacy",      "name_ru": "Аптеки",          "display_en": "Pharmacies",    "slug": "pharmacy",      "id": "184105932"},
    {"key": "atm",           "name_ru": "Банкоматы",       "display_en": "ATMs",          "slug": "atm",           "id": "184105402"},
    {"key": "gas",           "name_ru": "АЗС",             "display_en": "Gas stations",  "slug": "gas_station",   "id": "184105274"},
    {"key": "sport",         "name_ru": "Спорт",           "display_en": "Sport",         "slug": "sports_club",   "id": "184107297"},
    {"key": "cafe",          "name_ru": "Кафе",            "display_en": "Cafes",         "slug": "cafe",          "id": "184106390"},
    {"key": "shopping_mall", "name_ru": "Торговые центры", "display_en": "Shopping malls","slug": "shopping_mall", "id": "184108083"},
    {"key": "groceries",     "name_ru": "Продукты",        "display_en": "Groceries",     "slug": "supermarket",   "id": "184108079"},
    {"key": "restaurants",   "name_ru": "Рестораны",       "display_en": "Restaurants",   "slug": "restaurant",    "id": "184106394"},
]

CITIES = {
    "tyumen": {"id": 55,  "slug": "tyumen", "lat": 57.152985, "lon": 65.534328},
    "moscow": {"id": 213, "slug": "moscow", "lat": 55.7338,   "lon": 37.5881},
}

SSPN_MAP = {
    11: (0.443604, 0.185361), 12: (0.164635, 0.068791), 13: (0.096545, 0.040348),
    14: (0.05, 0.02), 15: (0.018, 0.009), 16: (0.004935, 0.002064),
}


def build_category_url(cat, lat, lon, zoom=14, domain="yandex.ru",
                       city_id=55, city_slug="tyumen", use_stv=False):
    sspn_lon, sspn_lat = SSPN_MAP.get(int(round(zoom)), (0.05, 0.02))
    sll_lon = lon + 0.00057
    sll_lat = lat - 0.0008
    url = (f"https://{domain}/maps/{city_id}/{city_slug}/category/{cat['slug']}/{cat['id']}/"
           f"?ll={lon:.6f}%2C{lat:.6f}"
           f"&sll={sll_lon:.6f}%2C{sll_lat:.6f}"
           f"&sspn={sspn_lon}%2C{sspn_lat}"
           f"&z={zoom}")
    if use_stv:
        url += "&l=stv%2Csta"
    return url


JUNK_STARTS = ("фото", "рейтинг", "отзыв", "оцен", "меню", "цены", "панорама",
               "открыто", "закрыто", "подробнее", "показать", "ещё", "еще",
               "сохранить", "построить", "диетические", "санатор", "на карте")
JUNK_EXACT = {"фото", "", "•", "·", "...", "—", "-", "–"}


def is_junk(s):
    if not s:
        return True
    t = s.lower().strip()
    if len(t) < 2:
        return True
    if t in JUNK_EXACT:
        return True
    return any(t.startswith(j) for j in JUNK_STARTS)


def clean_text(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def org_id_from_href(href):
    if not href:
        return ""
    m = re.search(r'/org/[^/]*/(\d+)', href) or re.search(r'/org/(\d+)', href)
    return m.group(1) if m else ""


def coords_from_url(url):
    """Yandex ll= is lon,lat -> return (lat, lon) as strings, else ('','')."""
    if not url:
        return "", ""
    for pat in (r'll=([\d\.\-]+)%2C([\d\.\-]+)', r'll=([\d\.\-]+),([\d\.\-]+)'):
        m = re.search(pat, url)
        if m:
            return m.group(2), m.group(1)
    return "", ""


def looks_like_captcha(page):
    try:
        url = (page.url or "").lower()
        if "showcaptcha" in url or "/captcha" in url:
            return True
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    markers = ["подтвердите, что запросы отправляли вы",
               "i'm not a robot", "confirm that you", "captcha"]
    return any(mk in body for mk in markers)


# ---------------------------------------------------------------------------
# Coordinate index built from the page's embedded JSON state.
# Yandex embeds org data (with coordinates) in the HTML. We regex the raw HTML
# for objects that contain an org id and a coordinates array. Format can vary,
# so we try a couple of shapes and fall back to per-org clicking later.
# ---------------------------------------------------------------------------
def build_coord_index_from_html(html):
    """Map org_id -> (lat, lon) by scanning the embedded page JSON.

    Yandex uses several shapes; we try the most reliable one: match the org
    permalink/id and pair it with the nearest coordinates array within a window.
    We index by the /org/<slug>/<id> permalink found in the SAME object window,
    which is far more accurate than grabbing any 6+ digit number.
    """
    index = {}
    if not html:
        return index

    # For every coordinates array, search a window around it for an /org/ id.
    coord_iter = list(re.finditer(
        r'"(?:coordinates|coordinate|point)"\s*:\s*[\{\[]?\s*(?:"lon"\s*:\s*)?([\d\.\-]+)\s*,\s*(?:"lat"\s*:\s*)?([\d\.\-]+)', html))
    for m in coord_iter:
        a, b = m.group(1), m.group(2)
        # Yandex stores [lon, lat]; sanity-check ranges to assign correctly.
        try:
            fa, fb = float(a), float(b)
        except ValueError:
            continue
        # lat is in [-90,90]; lon can exceed that. Decide which is which.
        if -90 <= fb <= 90 and abs(fa) > 90:
            lon, lat = a, b
        elif -90 <= fa <= 90 and abs(fb) > 90:
            lon, lat = b, a
        else:
            lon, lat = a, b  # default [lon, lat]

        start = max(0, m.start() - 800)
        window = html[start:m.end() + 800]
        ids = re.findall(r'/org/[^/"\\]+/(\d{6,})', window)
        if not ids:
            ids = re.findall(r'"(?:id|oid|permalink|business_oid)"\s*:\s*"?(\d{8,})"?', window)
        for oid in ids:
            index.setdefault(oid, (lat, lon))
    return index


def scroll_to_bottom(page, max_stalls=6, pause_ms=700, hard_limit=400):
    """Infinite scroll the results list until card count stops growing.

    Returns final card count. Guarded by max_stalls (consecutive no-growth
    iterations) and hard_limit (max total iterations)."""
    list_selectors = [
        ".scroll__container",
        "[class*='scroll__container']",
        ".search-list-view__list",
        "[class*='search-list-view']",
    ]
    card_sel = ("a[href*='/org/']")

    def count():
        try:
            return len(page.query_selector_all(card_sel))
        except Exception:
            return 0

    last = count()
    stalls = 0
    iters = 0
    while stalls < max_stalls and iters < hard_limit:
        iters += 1
        # Try to scroll a real list container; fall back to mouse wheel + End.
        scrolled = False
        for sel in list_selectors:
            el = page.query_selector(sel)
            if el:
                try:
                    page.eval_on_selector(sel, "e => e.scrollBy(0, e.scrollHeight)")
                    scrolled = True
                    break
                except Exception:
                    pass
        if not scrolled:
            page.mouse.wheel(0, 3000)
            try:
                page.keyboard.press("End")
            except Exception:
                pass

        # Click "Показать ещё" if it exists
        for txt in ["Показать ещё", "Показать еще", "Show more", "Ещё"]:
            try:
                btn = page.get_by_text(txt, exact=False).first
                if btn and btn.is_visible(timeout=150):
                    btn.click(timeout=400)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass

        page.wait_for_timeout(pause_ms)
        now = count()
        if now > last:
            last = now
            stalls = 0
            print(f"    …loaded {now} cards", end="\r", flush=True)
        else:
            stalls += 1
    print(f"    scroll done: {last} card links after {iters} iterations        ")
    return last


def parse_cards(page, domain, coord_index):
    """Return list of card dicts.

    Primary strategy: iterate EVERY /org/ anchor on the page (this is what
    actually loads for the whole scrolled list). Deduplicate by org_id, NOT by
    title, so chains with identical names (e.g. 'Доброго дня', 'ВТБ') are kept.
    For each org we try to pull title/address/rating from its nearest snippet
    container, and coords from coord_index.
    """
    rows = []
    seen_ids = set()

    try:
        anchors = page.query_selector_all("a[href*='/org/']")
    except Exception:
        anchors = []

    for a in anchors:
        try:
            href = a.get_attribute("href") or ""
            oid = org_id_from_href(href)
            if not oid or oid in seen_ids:
                continue

            # title: prefer the anchor text; if empty, look for a title node
            title = clean_text(a.inner_text())
            if is_junk(title):
                # try a title element inside the anchor's snippet container
                cont = a.evaluate_handle(
                    "el => el.closest(\"[class*='snippet']\") || el.closest('li') || el.parentElement")
                title_el = None
                try:
                    title_el = cont.as_element().query_selector(
                        "[class*='snippet-view__title'], [class*='title']") if cont else None
                except Exception:
                    title_el = None
                if title_el:
                    title = clean_text(title_el.inner_text())
                if is_junk(title):
                    continue

            seen_ids.add(oid)

            # address & rating from the nearest snippet container (best effort)
            address, rating = "", ""
            try:
                cont = a.evaluate_handle(
                    "el => el.closest(\"[class*='snippet']\") || el.closest('li') || el.parentElement")
                el = cont.as_element() if cont else None
                if el:
                    addr_el = el.query_selector("[class*='address']")
                    if addr_el:
                        address = clean_text(addr_el.inner_text())
                    r_el = el.query_selector("[class*='rating__value'], [class*='business-rating']")
                    if r_el:
                        rating = clean_text(r_el.inner_text())
            except Exception:
                pass

            if href.startswith("/"):
                href = f"https://{domain}" + href
            clean_href = href.split("?")[0]

            lat, lon = "", ""
            if oid in coord_index:
                lat, lon = coord_index[oid]
            if not lat:
                lat, lon = coords_from_url(href)

            rows.append({
                "title": title,
                "address": address,
                "lat": lat,
                "lon": lon,
                "rating": rating,
                "org_url": clean_href,
                "org_id": oid,
            })
        except Exception:
            continue

    return rows


def fetch_coords_for_org(context, org_url, domain, timeout=20000):
    """Open org page in a new tab, read coordinates from URL or embedded JSON."""
    lat, lon = "", ""
    pg = None
    try:
        pg = context.new_page()
        pg.goto(org_url, wait_until="domcontentloaded", timeout=timeout)
        pg.wait_for_timeout(1500)
        # 1) from the resulting URL (often becomes ...?ll=lon,lat...)
        lat, lon = coords_from_url(pg.url)
        # 2) from embedded JSON
        if not lat:
            html = pg.content()
            m = re.search(r'"coordinates"\s*:\s*\[\s*([\d\.\-]+)\s*,\s*([\d\.\-]+)\s*\]', html)
            if m:
                lon, lat = m.group(1), m.group(2)
        # 3) from a "поделиться"/share type link if present
        if not lat:
            m = re.search(r'll=([\d\.\-]+)(?:%2C|,)([\d\.\-]+)', pg.content())
            if m:
                lon, lat = m.group(1), m.group(2)
    except Exception:
        pass
    finally:
        if pg:
            try:
                pg.close()
            except Exception:
                pass
    return lat, lon


def main():
    p = argparse.ArgumentParser(description="Yandex 8cat v8.8")
    p.add_argument("--city", default="tyumen", choices=list(CITIES.keys()))
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--z", type=float, default=14)
    p.add_argument("--headless", dest="headless", action="store_true", help="run headless")
    p.add_argument("--no-headless", dest="headless", action="store_false", help="run headed (default)")
    p.set_defaults(headless=False)
    p.add_argument("--ru", action="store_true", help="use yandex.ru (default)")
    p.add_argument("--com", action="store_true", help="use yandex.com")
    p.add_argument("--stv", action="store_true", help="add l=stv,sta layer (avoid – hides list)")
    p.add_argument("--dry-run", action="store_true", help="only print URLs")
    p.add_argument("--captcha-wait", type=int, default=180, help="seconds to wait for manual captcha")
    p.add_argument("--max-stalls", type=int, default=6, help="stop scrolling after N no-growth passes")
    p.add_argument("--no-org-coords", action="store_true",
                   help="do NOT open org pages for missing coords (faster, less complete)")
    args, _ = p.parse_known_args()

    city = CITIES[args.city]
    lat = args.lat if args.lat is not None else city["lat"]
    lon = args.lon if args.lon is not None else city["lon"]
    domain = "yandex.com" if (args.com and not args.ru) else "yandex.ru"

    print("=== YANDEX 8 CAT v8.8 ===")
    print(f"{args.city}  {domain}  {lat},{lon}  z={args.z}")
    print("Categories ONLY:")

    urls = []
    for cat in CATS:
        url = build_category_url(cat, lat, lon, args.z, domain=domain,
                                 city_id=city["id"], city_slug=city["slug"],
                                 use_stv=args.stv)
        print(f"{cat['name_ru']:<18} {url}")
        urls.append({**cat, "url": url, "lat": lat, "lon": lon})

    Path(f"urls_8cat_{args.city}.json").write_text(
        json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(f"links_8cat_{args.city}.txt", "w", encoding="utf-8") as f:
        for u in urls:
            f.write(f"# {u['name_ru']}\n{u['url']}\n\n")
    print(f"\nSaved {len(urls)} -> urls_8cat_{args.city}.json / links_8cat_{args.city}.txt")

    if args.dry_run:
        print("\nDry-run OK. Remove --dry-run to scrape.")
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Need: pip install playwright && playwright install chromium")
        return

    results = []
    seen = set()

    with sync_playwright() as sp:
        browser = sp.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            locale="ru-RU",
            viewport={"width": 1360, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"))
        page = context.new_page()
        page.set_default_navigation_timeout(45000)

        for i, u in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {u['name_ru']}")
            try:
                page.goto(u["url"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  goto failed: {e}")
                continue

            if looks_like_captcha(page):
                if args.headless:
                    print("  ⚠ captcha + headless — re-run without --headless.")
                    continue
                print(f"  ⚠ captcha — solve it in the window. Waiting up to {args.captcha_wait}s...")
                waited = 0
                while looks_like_captcha(page) and waited < args.captcha_wait:
                    time.sleep(3); waited += 3
                if looks_like_captcha(page):
                    print("  still captcha, skipping."); continue
                print("  captcha cleared.")

            # INFINITE scroll to load the whole list
            scroll_to_bottom(page, max_stalls=args.max_stalls)

            # Build coordinate index from page HTML (fast path)
            coord_index = {}
            try:
                coord_index = build_coord_index_from_html(page.content())
            except Exception:
                pass

            rows = parse_cards(page, domain, coord_index)
            print(f"  cards parsed: {len(rows)}")

            n = 0
            new_rows = []
            for r in rows:
                key = r["org_id"] or r["org_url"]
                if not key or key in seen or not r["title"]:
                    continue
                seen.add(key)
                new_rows.append(r)
                n += 1
            print(f"  accepted {n}  total {len(results) + n}")

            # Fill missing coordinates by opening org pages
            if not args.no_org_coords:
                missing = [r for r in new_rows if not r["lat"] and r["org_url"]]
                if missing:
                    print(f"  fetching coords for {len(missing)} orgs without coords...")
                    for j, r in enumerate(missing, 1):
                        la, lo = fetch_coords_for_org(context, r["org_url"], domain)
                        if la:
                            r["lat"], r["lon"] = la, lo
                        if j % 10 == 0:
                            print(f"    coords {j}/{len(missing)}", end="\r", flush=True)
                        time.sleep(0.4)
                    got = sum(1 for r in new_rows if r["lat"])
                    print(f"  coords resolved: {got}/{len(new_rows)}                 ")

            for r in new_rows:
                results.append({
                    "category_key": u["key"],
                    "query_ru": u["name_ru"],
                    "display_en": u["display_en"],
                    "title": r["title"],
                    "address": r["address"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "rating": r["rating"],
                    "org_url": r["org_url"],
                })
            time.sleep(1.0)

        browser.close()

    out_csv = f"results_8cat_{args.city}_v88.csv"
    out_json = f"results_8cat_{args.city}_v88.json"
    Path(out_json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if results:
        with_coords = sum(1 for r in results if r["lat"])
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\n✓ {len(results)} rows  ({with_coords} with coordinates)")
        print(f"CSV : {out_csv}")
        print(f"JSON: {out_json}")
    else:
        print("\n0 rows – likely captcha / empty list.")
        print("Tips: keep headed, avoid --stv, try --z 13.")


if __name__ == "__main__":
    main()
