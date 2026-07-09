(function () {
  "use strict";

  const appEl = document.getElementById("app");
  const isAdmin = appEl.dataset.isAdmin === "true";

  // ------------------------------------------------------------------
  // Язык UI (по умолчанию русский)
  // ------------------------------------------------------------------
  const LANG = {

    ru: {
      appTitle: "Точки с панорамами",
      adminLogin: "Вход для администратора",
      btnAddPoint: "Добавить точку",
      btnClose: "Закрыть",
      loadingPanorama: "Загрузка панорамы…",
    },
    en: {
      appTitle: "Panorama Points",
      adminLogin: "Admin login",
      btnAddPoint: "Add point",
      btnClose: "Close",
      loadingPanorama: "Loading panorama…",
    },
  };

  const langRuBtn = document.getElementById("btn-lang-ru");
  const langEnBtn = document.getElementById("btn-lang-en");
  let currentLang = (localStorage.getItem("lang") || "ru").toLowerCase();
  if (currentLang !== "en" && currentLang !== "ru") currentLang = "ru";

  function getI18n(key) {
    const t = LANG[currentLang];
    switch (key) {
      case "currentLocation":
        return currentLang === "en" ? "Current location" : "Текущее местоположение";
      case "close":
        return t.btnClose;
      case "deletePoint":
        return currentLang === "en" ? "Delete point" : "Удалить точку";
      case "appTitle":
        return t.appTitle;
      case "adminLoginTitle":
        return currentLang === "en" ? "Admin login" : "Вход администратора";
      case "login":
        return currentLang === "en" ? "Login" : "Войти";
      case "cancel":
        return currentLang === "en" ? "Cancel" : "Отмена";
      default:
        return "";
    }
  }


  function setLang(lang) {

    currentLang = lang === "en" ? "en" : "ru";
    // переводим все элементы с data-i18n до вычислений/рендеров
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const value = getI18n(key);
      if (value) el.textContent = value;
    });

    localStorage.setItem("lang", currentLang);

    if (langRuBtn) langRuBtn.classList.toggle("active", currentLang === "ru");
    if (langEnBtn) langEnBtn.classList.toggle("active", currentLang === "en");

    const t = LANG[currentLang];

    const adminStatusEl = document.getElementById("admin-status");
    if (adminStatusEl) {
      // В RU шаблон уже содержит текст. В EN — заменяем.
      if (currentLang === "en") {
        const isAdminNow = appEl.dataset.isAdmin === "true";
        adminStatusEl.textContent = isAdminNow ? "Admin mode: enabled" : "Admin mode: disabled";
      }
    }

    // Применяем текст для всех элементов с data-i18n уже в начале setLang()
    const titleEl = document.getElementById("app-title");
    if (titleEl) titleEl.textContent = t.appTitle;


    const loginBtn = document.getElementById("btn-login");
    if (loginBtn) loginBtn.textContent = t.adminLogin;

    // если нужны данные по data-i18n уже из шаблона, то они проставлены в начале setLang


    const addModeBtn = document.getElementById("btn-add-mode");
    if (addModeBtn) {
      // не трогаем addModeActive (он ниже объявляется)
      addModeBtn.textContent = t.btnAddPoint;
    }


    const loadingEl = document.getElementById("view-panorama-loading");
    if (loadingEl) {
      const p = loadingEl.querySelector("p");
      if (p) p.textContent = t.loadingPanorama;
    }
  }

  if (langRuBtn) langRuBtn.addEventListener("click", () => setLang("ru"));
  if (langEnBtn) langEnBtn.addEventListener("click", () => setLang("en"));

  // addModeActive объявляется ниже, поэтому initial setLang без изменения текста кнопки add
  setLang(currentLang);


  // ------------------------------------------------------------------
  // Карта (Leaflet + OSM)
  // ------------------------------------------------------------------

  // Тюмень по умолчанию (можно переопределить data-map-center в шаблоне)
  const centerAttr = appEl.dataset.mapCenter || "57.1509,65.5273";
  const [centerLat, centerLon] = centerAttr.split(",").map((v) => parseFloat(v.trim()));
  const map = L.map("map").setView([centerLat, centerLon], 11);


  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const markers = new Map(); // db points markers: id -> L.Marker
  const airMarkers = new Map(); // panorama_id -> L.Marker (air pano markers)

  function markerPopupHtml(point) {
    return `<b>${escapeHtml(point.title)}</b>`;
  }

  function airMarkerPopupHtml(point) {
    // point: { id, title, description, lat, lon }
    return `<b>${escapeHtml(point.title || "Аэропанорама")}</b>`;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function _toRadians(deg) {
    return (deg * Math.PI) / 180;
  }

  function _toDegrees(rad) {
    return (rad * 180) / Math.PI;
  }

  function _normalizeAngle(deg) {
    return ((deg % 360) + 360) % 360;
  }

  function computeBearing(lat1, lon1, lat2, lon2) {
    const phi1 = _toRadians(lat1);
    const phi2 = _toRadians(lat2);
    const deltaLambda = _toRadians(lon2 - lon1);
    const y = Math.sin(deltaLambda) * Math.cos(phi2);
    const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(deltaLambda);
    return _normalizeAngle(_toDegrees(Math.atan2(y, x)));
  }

  function addMarker(point) {
    const marker = L.marker([point.lat, point.lon]).addTo(map);
    marker.bindPopup(markerPopupHtml(point));
    marker.on("click", () => openViewModal(point));
    markers.set(point.id, marker);
  }

  function clearAirMarkers() {
    airMarkers.forEach((m) => map.removeLayer(m));
    airMarkers.clear();
  }

  function addAirMarker(point) {
    // point: air pano { id, title, description?, lat, lon, height?, captured_at? }
    const key = point.id || `${point.lat}_${point.lon}`;
    const marker = L.marker([point.lat, point.lon]).addTo(map);
    marker.bindPopup(airMarkerPopupHtml(point));
    marker.on("click", () => openAirPano(point));
    airMarkers.set(key, marker);
  }

  async function loadPoints() {
    const res = await fetch("/api/points");
    const points = await res.json();
    markers.forEach((m) => map.removeLayer(m));
    markers.clear();
    points.forEach(addMarker);
  }

  async function loadAirPanoMarkers() {
    const bounds = map.getBounds();
    const west = bounds.getWest();
    const south = bounds.getSouth();
    const east = bounds.getEast();
    const north = bounds.getNorth();

    const bbox = `${west},${south},${east},${north}`;

    let data;
    try {
      const res = await fetch(`/api/sky-panoramas?bbox=${encodeURIComponent(bbox)}`);
      data = await res.json();
    } catch (e) {
      return;
    }
    if (!data || data.status !== "ok") return;

    clearAirMarkers();
    (data.points || []).forEach(addAirMarker);
  }

  function debounce(fn, delayMs) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), delayMs);
    };
  }

  loadPoints();

  // первоначальная подгрузка air-panorama markers
  loadAirPanoMarkers();

  // обновляем air-panorama markers при перемещении карты (debounce)
  map.on("moveend", debounce(() => loadAirPanoMarkers(), 500));

  // ------------------------------------------------------------------
  // Текущее местоположение (геолокация)
  // ------------------------------------------------------------------
  const currentLocationBtn = document.getElementById("btn-current-location");
  if (currentLocationBtn) {
    currentLocationBtn.addEventListener("click", () => {
      if (!navigator.geolocation) {
        alert("Геолокация не поддерживается этим браузером.");
        return;
      }

      currentLocationBtn.disabled = true;
      currentLocationBtn.textContent = currentLang === "en" ? "Locating…" : "Определяем…";


      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          const lat = pos.coords.latitude;
          const lon = pos.coords.longitude;

          // Центрируем карту на текущее местоположение
          map.setView([lat, lon], 16);
          // чтобы при смене языка не было скачка — текст кнопки задаём через i18n
          currentLocationBtn.textContent = currentLocationBtn.dataset.i18n ? getI18n(currentLocationBtn.dataset.i18n) : currentLocationBtn.textContent;

          try {
            // Обновляем боковую сводку по текущим координатам
            await loadPoiSummaryForLatLon(lat, lon);
          } catch (_) {}

          // Подхватим ближайшие air-panorama маркеры вокруг новой области
          try {
            await loadAirPanoMarkers();
          } catch (_) {}

          currentLocationBtn.disabled = false;
          currentLocationBtn.textContent = currentLang === "en" ? "Current location" : "Текущее местоположение";

        },
        (err) => { 
          console.warn("Geolocation error:", err);
          alert("Не удалось получить геолокацию. Разрешите доступ к местоположению.");
          currentLocationBtn.disabled = false;
          currentLocationBtn.textContent = currentLocationBtn.dataset.i18n ? getI18n(currentLocationBtn.dataset.i18n) : "Текущее местоположение";
        },

        { enableHighAccuracy: true, timeout: 10000, maximumAge: 2000 }
      );
    });
  }

  // ------------------------------------------------------------------
  // Модалки: общие помощники
  // ------------------------------------------------------------------

  function showModal(id) {
    document.getElementById(id).classList.remove("hidden");
  }
  function hideModal(id) {
    document.getElementById(id).classList.add("hidden");
  }

  function renderPoiSummary(data, centerPoint) {
    const poiEl = document.getElementById("poi-summary");
    if (!poiEl) return;

    clearPoiMarkers();

    if (!data || data.status !== "ok") {
    poiEl.innerHTML = `<p class="hint">${currentLang === "en" ? "Summary unavailable" : "Сводка недоступна"}</p>`;

      return;
    }

    const categories = (data.categories || []).filter((c) => (c.count || 0) > 0);
    if (!categories.length) {
      poiEl.innerHTML = `<p class="hint">В радиусе ${data.radius_m}м объектов не найдено</p>`;
      return;
    }

    poiEl.innerHTML = "";
    const allItems = [];
    categories.forEach((cat) => {
      const count = cat.count || 0;
      const items = cat.items || [];

      const wrapper = document.createElement("div");
      wrapper.className = "poi-category";

      const head = document.createElement("div");
      head.className = "poi-category-head";

      const name = document.createElement("div");
      name.className = "name";
      name.textContent = `${cat.name}`;

      const c = document.createElement("div");
      c.className = "count";
      c.textContent = `${count}`;

      head.appendChild(name);
      head.appendChild(c);

      const list = document.createElement("div");
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = currentLang === "en" ? "not found" : "не найдено";
        list.appendChild(empty);
      } else {
        items.forEach((it, itemIndex) => {
          const p = document.createElement("div");
          p.className = "poi-item";

              const title = it.title ? it.title : "Объект";
          const dist = it.dist_m != null ? it.dist_m : "";
          const bearing = centerPoint && it.lat != null && it.lon != null ? computeBearing(centerPoint.lat, centerPoint.lon, it.lat, it.lon) : null;

          const poiItem = {
            category: cat.key,
            title,
            dist_m: dist,
            bearing,
            lat: it.lat,
            lon: it.lon,
          };
          allItems.push(poiItem);
          if (itemIndex === 0 && bearing != null) {
            currentPoiItems.push(poiItem);
          }

          const titleHtml = it.org_url
            ? `<a href="${escapeHtml(it.org_url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
            : escapeHtml(title);

          const distSpan = document.createElement("span");
          distSpan.className = "dist";
          distSpan.textContent = `— ${dist}м`;
          if (bearing != null) {
            distSpan.style.cursor = "pointer";
            distSpan.style.color = "var(--accent)";
            distSpan.addEventListener("click", () => {
              focusPoiYaw(bearing);
            });
          }

          p.innerHTML = titleHtml + " ";
          p.appendChild(distSpan);
          list.appendChild(p);
        });
      }

      wrapper.appendChild(head);
      wrapper.appendChild(list);
      poiEl.appendChild(wrapper);
    });

    if (centerPoint && currentPoiItems.length) {
      renderPoiOverlay(currentPoiItems);
    }
  }

  let currentPoiItems = [];
  let poiOverlay = null;
  let poiOverlayFrame = null;

  // category_key -> icon URL (e.g. "school" -> "/markers/school.png"),
  // fetched once from the backend. Categories with no icon fall back to the
  // plain dot marker.
  let poiCategoryIcons = {};

  async function loadPoiCategoryIcons() {
    try {
      const res = await fetch("/api/poi-icons");
      poiCategoryIcons = await res.json();
    } catch (e) {
      poiCategoryIcons = {};
    }
  }
  loadPoiCategoryIcons();

  function clearPoiMarkers() {
    currentPoiItems = [];
    renderPoiOverlay([]);
  }

  function ensurePoiOverlay(container) {
    if (!container) return null;
    if (!poiOverlay || poiOverlay.parentNode !== container) {
      destroyPoiOverlay();
      poiOverlay = document.createElement("div");
      poiOverlay.className = "panorama-poi-overlay";
      poiOverlay.style.position = "absolute";
      poiOverlay.style.top = "0";
      poiOverlay.style.left = "0";
      poiOverlay.style.right = "0";
      poiOverlay.style.bottom = "0";
      poiOverlay.style.pointerEvents = "none";
      poiOverlay.style.zIndex = "12";
      container.appendChild(poiOverlay);
    }
    return poiOverlay;
  }

  function destroyPoiOverlay() {
    if (poiOverlayFrame) {
      cancelAnimationFrame(poiOverlayFrame);
      poiOverlayFrame = null;
    }
    if (poiOverlay && poiOverlay.parentNode) {
      poiOverlay.parentNode.removeChild(poiOverlay);
    }
    poiOverlay = null;
  }

  function renderPoiOverlay(items) {
    currentPoiItems = items || [];
    if (!poiOverlay) return;
    poiOverlay.innerHTML = "";
    currentPoiItems.forEach((item, index) => {
      if (item.bearing == null) return;
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = "pano-poi-marker";
      marker.style.position = "absolute";
      marker.style.pointerEvents = "auto";
      marker.style.padding = "0";
      marker.style.cursor = "pointer";
      marker.dataset.index = index;

      const iconUrl = poiCategoryIcons[item.category];
      const titleHtml = escapeHtml(item.title);
      const distM = item.dist_m != null && item.dist_m !== "" ? `${escapeHtml(String(item.dist_m))}м` : "";

      // Метраж показываем сразу под маркером, без наведения.
      const tooltipHtml = `
        <span class="pano-poi-tooltip">${titleHtml}</span>
        <span class="pano-poi-distance-always">${distM}</span>
      `;

      if (iconUrl) {
        marker.classList.add("pano-poi-marker--icon");
        marker.innerHTML = `<img class="pano-poi-icon" src="${iconUrl}" alt="" />${tooltipHtml}`;
      } else {
        marker.innerHTML = `<span class="pano-poi-dot"></span>${tooltipHtml}`;
      }

      marker.addEventListener("click", () => {
        if (item.bearing != null) {
          focusPoiYaw(item.bearing);
        }
      });
      poiOverlay.appendChild(marker);
    });
    updatePoiOverlay();
    startPoiOverlayLoop();
  }

  function updatePoiOverlay() {
    if (!poiOverlay || !viewViewer) return;
    const yawDeg = _normalizeAngle(viewViewer.getYaw());
    const hfovDeg = viewViewer.getHfov();
    const pitchDeg = typeof viewViewer.getPitch === "function" ? viewViewer.getPitch() : 0;
    const rect = poiOverlay.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    if (!width || !height) return;

    // Pannellum doesn't expose a vertical FOV getter, so we derive one the
    // same way a standard perspective camera would, from hfov + aspect ratio.
    const hfov = _toRadians(hfovDeg);
    const vfov = 2 * Math.atan(Math.tan(hfov / 2) * (height / width));
    const pitch = _toRadians(pitchDeg);
    const tanHalfV = Math.tan(vfov / 2);
    const margin = 12; // px — hide markers once they'd render past the edge

    // Horizontal and vertical are handled as two independent axes on
    // purpose: horizontal position/visibility depends only on yaw (exactly
    // as before pitch was ever involved), vertical position/visibility
    // depends only on pitch. Mixing the two (a full 3D rotation) produces
    // technically "correct" perspective, but it also drags markers
    // sideways as you tilt, which reads as random/diagonal motion. This
    // keeps each marker's movement predictable: tilt up -> it slides down;
    // pan right -> it slides left; nothing moves diagonally on its own.
    Array.from(poiOverlay.children).forEach((marker) => {
      const index = Number(marker.dataset.index);
      const item = currentPoiItems[index];
      if (!item || item.bearing == null) {
        marker.style.display = "none";
        return;
      }

      // Horizontal: pure function of yaw.
      let diff = item.bearing - yawDeg;
      diff = ((diff + 180) % 360) - 180;
      if (Math.abs(diff) > hfovDeg / 2) {
        marker.style.display = "none";
        return;
      }
      const x = width / 2 + (diff / hfovDeg) * width;

      // Vertical: pure function of pitch. POIs are treated as sitting on
      // the horizon (elevation 0), so their angle relative to the camera
      // is simply -pitch — this is the world-fixed direction that slides
      // down when you look up and up when you look down.
      const angleFromCenter = -pitch;
      const ndcY = Math.tan(angleFromCenter) / tanHalfV;
      if (Math.abs(ndcY) > 1) {
        marker.style.display = "none";
        return;
      }
      const y = height / 2 - (ndcY * height) / 2;
      if (y <= margin || y >= height - margin) {
        marker.style.display = "none";
        return;
      }

      marker.style.display = "block";
      marker.style.left = `${x}px`;
      marker.style.top = `${y}px`;
      // Icon pins are teardrop-shaped with the point at the bottom, so they
      // need to sit ABOVE (x,y) with their tip touching it. The plain dot
      // marker keeps its original (smaller, centered-ish) anchoring.
      marker.style.transform = marker.classList.contains("pano-poi-marker--icon")
        ? "translate(-50%, -100%)"
        : "translateX(-50%)";

      // метраж теперь рисуется сразу под маркером в renderPoiOverlay()
    });
  }

  function startPoiOverlayLoop() {
    if (poiOverlayFrame) return;
    const tick = () => {
      updatePoiOverlay();
      poiOverlayFrame = requestAnimationFrame(tick);
    };
    tick();
  }

  function focusPoiYaw(angle) {
    if (!viewViewer || typeof viewViewer.setYaw !== "function") return;
    viewViewer.setYaw(_normalizeAngle(angle), 700);
  }



  async function loadPoiSummaryForLatLon(lat, lon) {
    const poiEl = document.getElementById("poi-summary");
    if (!poiEl) return;

    poiEl.innerHTML = `<p class="hint">Ищем ближайшие объекты…</p>`;

    let data;
    try {
      const res = await fetch(
    	  `/api/poi-summary?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(
          lon
        )}&radius_m=500`
      );
      data = await res.json();
    } catch (e) {
      poiEl.innerHTML = `<p class="hint">Не удалось получить сводку.</p>`;
      return;
    }
    renderPoiSummary(data, currentViewPoint);
  }

  document.querySelectorAll(".js-close-modal").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const modal = e.target.closest(".modal");
      modal.classList.add("hidden");
      destroyViewers();
    });
  });

  // ------------------------------------------------------------------
  // Панорамы (Pannellum + наш /api/panorama)
  // ------------------------------------------------------------------

  let previewViewer = null;
  let viewViewer = null;

  function destroyViewers() {
    if (previewViewer) {
      previewViewer.destroy();
      previewViewer = null;
    }
    if (viewViewer) {
      viewViewer.destroy();
      viewViewer = null;
    }
    currentPoiItems = [];
    destroyPoiOverlay();
  }

  function showPanoramaFallback(containerId, url, statusEl, loadingEl) {
    const container = document.getElementById(containerId);
    if (!container) return null;
    container.innerHTML = "";
    const img = document.createElement("img");
    img.src = url;
    img.alt = currentLang === "en" ? "Panorama" : "Панорама";
    img.style.width = "100%";
    img.style.height = "100%";
    img.style.objectFit = "contain";
    img.style.display = "block";
    img.addEventListener("load", () => {
      statusEl.classList.add("hidden");
      if (loadingEl) loadingEl.classList.add("hidden");
    });
    img.addEventListener("error", () => {
      statusEl.textContent = currentLang === "en" ? "Panorama failed to load." : "Не удалось загрузить панораму.";
      statusEl.classList.remove("hidden");
      if (loadingEl) loadingEl.classList.add("hidden");
    });
    container.appendChild(img);
    return { viewer: null, fallback: true };
  }

  /**
   * Запрашивает панораму у бэкенда и монтирует её в Pannellum-контейнер.
   * containerId — id div-контейнера, statusElId — id элемента для статуса.
   * Возвращает объект { viewer } либо null, если панорамы нет/ошибка.
   */
  async function loadPanoramaInto(containerId, statusElId, lat, lon, options = {}) {
    // Гарантированно пересоздаём WebGL-вьюер (иначе при WebGL context lost Pannellum остаётся в поломанном состоянии)
    destroyViewers();

    const statusEl = document.getElementById(statusElId);
    const container = document.getElementById(containerId);
    const loadingElId = options.loadingElId || null;
    const loadingEl = loadingElId ? document.getElementById(loadingElId) : null;

    container.innerHTML = "";
    ensurePoiOverlay(container);
    if (loadingEl) loadingEl.classList.remove("hidden");

    statusEl.textContent = currentLang === "en" ? "Searching for panorama nearby…" : "Ищем панораму рядом с точкой…";


    statusEl.classList.remove("hidden");

    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    if (options.force) {
      params.set("force", "1");
    }

    let data;
    try {
      const res = await fetch(`/api/panorama?${params.toString()}`);
      data = await res.json();
    } catch (err) {
      statusEl.textContent = "Не удалось связаться с сервером панорам.";
      if (loadingEl) loadingEl.classList.add("hidden");
      return null;
    }

    if (data.status === "not_found") {
      statusEl.textContent = "Рядом с этой точкой панорама Яндекса не найдена.";
      if (loadingEl) loadingEl.classList.add("hidden");
      return null;
    }
    if (data.status === "error") {
      statusEl.textContent = "Ошибка получения панорамы: " + (data.message || "неизвестно");
      if (loadingEl) loadingEl.classList.add("hidden");
      return null;
    }

    statusEl.classList.add("hidden");
    if (loadingEl) loadingEl.classList.add("hidden");

    let viewer;
    try {
      viewer = pannellum.viewer(containerId, {
        type: "equirectangular",
        panorama: data.url,
        autoLoad: true,
        showControls: true,
        compass: false,
        autoRotate: false,
        useCanvas: true,
        hotSpots: [],
      });
      ensurePoiOverlay(container);
    } catch (err) {
      console.warn("Pannellum failed to initialize, falling back to static image.", err);
      return showPanoramaFallback(containerId, data.url, statusEl, loadingEl);
    }

    const canvas = container.querySelector("canvas");
    if (canvas) {
      canvas.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        console.warn("WebGL context lost on panorama canvas, switching to fallback image.");
        destroyViewers();
        showPanoramaFallback(containerId, data.url, statusEl, loadingEl);
      });
    }

    if (viewer && typeof viewer.on === "function") {
      viewer.on("load", () => {
        if (currentPoiItems.length) {
          renderPoiOverlay(currentPoiItems);
        }
      });
      viewer.on("error", (message) => {
        console.warn("Pannellum emitted error, switching to fallback image:", message);
        destroyViewers();
        showPanoramaFallback(containerId, data.url, statusEl, loadingEl);
      });
    }

    try {
      const imgEl = container.querySelector("img");
      if (imgEl) {
        imgEl.addEventListener("error", () => {
          statusEl.textContent = currentLang === "en" ? "Panorama tiles failed to load" : "Не удалось догрузить панораму";
          statusEl.classList.remove("hidden");
        });
      }
    } catch (_) {}

    return { viewer, data };
  }

  // ------------------------------------------------------------------
  // Просмотр существующей точки (доступно всем)
  // ------------------------------------------------------------------

  let currentViewPoint = null;

  async function openViewModal(point) {
    currentViewPoint = point;
    document.getElementById("view-point-title").textContent = point.title;
    document.getElementById("view-point-description").textContent = point.description || "";

    // очистка прошлого экрана сразу
    const loadingEl = document.getElementById("view-panorama-loading");
    const statusEl = document.getElementById("view-panorama-status");
    const panoEl = document.getElementById("view-panorama");
    if (panoEl) panoEl.innerHTML = "";
    if (loadingEl) loadingEl.classList.remove("hidden");
    if (statusEl) {
      statusEl.textContent = "";
      statusEl.classList.remove("hidden");
    }

    showModal("view-point-modal");


    const result = await loadPanoramaInto(
      "view-panorama",
      "view-panorama-status",
      point.lat,
      point.lon,
      { loadingElId: "view-panorama-loading" }
    );

    if (result) viewViewer = result.viewer;

    // обновляем боковую сводку по координатам точки
    if (point && point.lat != null && point.lon != null) {
      loadPoiSummaryForLatLon(point.lat, point.lon);
    }
  }

  async function openAirPano(panoPoint) {
    // air pano не удаляется, просто открываем и грузим сводку по pano lat/lon
    currentViewPoint = panoPoint;
    document.getElementById("view-point-title").textContent = panoPoint.title || (currentLang === "en" ? "Air panorama" : "Воздушная панорама");

    document.getElementById("view-point-description").textContent = panoPoint.description || "";
    showModal("view-point-modal");

    const result = await loadPanoramaInto(
      "view-panorama",
      "view-panorama-status",
      panoPoint.lat,
      panoPoint.lon,
      { loadingElId: "view-panorama-loading" }
    );
    if (result) viewViewer = result.viewer;

    if (panoPoint && panoPoint.lat != null && panoPoint.lon != null) {
      loadPoiSummaryForLatLon(panoPoint.lat, panoPoint.lon);
    }
  }

  const reloadBtn = document.getElementById("view-point-reload");
  if (reloadBtn) {
    reloadBtn.addEventListener("click", async () => {
      if (!currentViewPoint || currentViewPoint.lat == null || currentViewPoint.lon == null) return;

      // принудительно обновим панораму
      const statusEl = document.getElementById("view-panorama-status");
      const loadingEl = document.getElementById("view-panorama-loading");
      const panoEl = document.getElementById("view-panorama");
      if (panoEl) panoEl.innerHTML = "";
      if (loadingEl) loadingEl.classList.remove("hidden");
      if (statusEl) statusEl.textContent = currentLang === "en" ? "Reloading panorama…" : "Перезагрузка панорамы…";

      destroyViewers();
      const result = await loadPanoramaInto(
        "view-panorama",
        "view-panorama-status",
        currentViewPoint.lat,
        currentViewPoint.lon,
        { loadingElId: "view-panorama-loading", force: true }
      );
      if (result) viewViewer = result.viewer;

      if (currentViewPoint && currentViewPoint.lat != null && currentViewPoint.lon != null) {
        loadPoiSummaryForLatLon(currentViewPoint.lat, currentViewPoint.lon);
      }
    });
  }

  const deleteBtn = document.getElementById("view-point-delete");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!currentViewPoint) return;
      if (!confirm(`Удалить точку "${currentViewPoint.title}"?`)) return;
      await fetch(`/api/points/${currentViewPoint.id}`, { method: "DELETE" });
      hideModal("view-point-modal");
      destroyViewers();
      loadPoints();
    });
  }

  // ------------------------------------------------------------------
  // Админ: вход/выход
  // ------------------------------------------------------------------

  const loginBtn = document.getElementById("btn-login");
  if (loginBtn) {
    loginBtn.addEventListener("click", () => {
      document.getElementById("login-error").classList.add("hidden");
      document.getElementById("login-password").value = "";
      showModal("login-modal");
    });
  }

  const loginSubmit = document.getElementById("login-submit");
  if (loginSubmit) {
    loginSubmit.addEventListener("click", async () => {
      const password = document.getElementById("login-password").value;
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const data = await res.json();
      if (data.ok) {
        location.reload();
      } else {
        const err = document.getElementById("login-error");
        err.textContent = data.error || "Ошибка входа";
        err.classList.remove("hidden");
      }
    });
  }

  const logoutBtn = document.getElementById("btn-logout");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" });
      location.reload();
    });
  }

  // ------------------------------------------------------------------
  // Админ: добавление точки по клику на карте
  // ------------------------------------------------------------------

  let addModeActive = false;
  let pendingCoords = null;

  const addModeBtn = document.getElementById("btn-add-mode");
  if (addModeBtn) {
    const t = LANG[currentLang];
    if (!addModeActive) addModeBtn.textContent = t.btnAddPoint;

    addModeBtn.addEventListener("click", () => {
      addModeActive = !addModeActive;
      addModeBtn.classList.toggle("active", addModeActive);
      addModeBtn.textContent = addModeActive
        ? "Кликните по карте…"
        : "Добавить точку";
    });
  }


  if (isAdmin) {
    map.on("click", (e) => {
      if (!addModeActive) return;
      pendingCoords = { lat: e.latlng.lat, lon: e.latlng.lng };
      openAddPointModal(pendingCoords);

      // выключаем режим добавления после клика
      addModeActive = false;
      addModeBtn.classList.remove("active");
      addModeBtn.textContent = "Добавить точку";
    });
  }

  async function openAddPointModal(coords) {
    document.getElementById("add-point-coords").textContent =
      `${coords.lat.toFixed(6)}, ${coords.lon.toFixed(6)}`;
    document.getElementById("add-point-title").value = "";
    document.getElementById("add-point-description").value = "";
    showModal("add-point-modal");

    const result = await loadPanoramaInto(
      "panorama-preview",
      "panorama-status",
      coords.lat,
      coords.lon
    );
    if (result) previewViewer = result.viewer;
  }

  const addPointSubmit = document.getElementById("add-point-submit");
  if (addPointSubmit) {
    addPointSubmit.addEventListener("click", async () => {
      if (!pendingCoords) return;
      const title = document.getElementById("add-point-title").value.trim();
      const description = document
        .getElementById("add-point-description")
        .value.trim();

      await fetch("/api/points", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: pendingCoords.lat,
          lon: pendingCoords.lon,
          title,
          description,
        }),
      });

      hideModal("add-point-modal");
      destroyViewers();
      pendingCoords = null;
      loadPoints();
    });
  }
})();