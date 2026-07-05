(function () {
  "use strict";

  const appEl = document.getElementById("app");
  const isAdmin = appEl.dataset.isAdmin === "true";

  // ------------------------------------------------------------------
  // Карта (Leaflet + OSM)
  // ------------------------------------------------------------------

  // Тюмень по умолчанию
  const map = L.map("map").setView([57.1509, 65.5273], 11);

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
      currentLocationBtn.textContent = "Определяем…";

      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          const lat = pos.coords.latitude;
          const lon = pos.coords.longitude;

          // Центрируем карту на текущее местоположение
          map.setView([lat, lon], 16);

          try {
            // Обновляем боковую сводку по текущим координатам
            await loadPoiSummaryForLatLon(lat, lon);
          } catch (_) {}

          // Подхватим ближайшие air-panorama маркеры вокруг новой области
          try {
            await loadAirPanoMarkers();
          } catch (_) {}

          currentLocationBtn.disabled = false;
          currentLocationBtn.textContent = "Текущее местоположение";
        },
        (err) => {
          console.warn("Geolocation error:", err);
          alert("Не удалось получить геолокацию. Разрешите доступ к местоположению.");
          currentLocationBtn.disabled = false;
          currentLocationBtn.textContent = "Текущее местоположение";
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

  function renderPoiSummary(data) {
    const poiEl = document.getElementById("poi-summary");
    if (!poiEl) return;

    if (!data || data.status !== "ok") {
      poiEl.innerHTML = `<p class="hint">Сводка недоступна</p>`;
      return;
    }

    const categories = data.categories || [];
    if (!categories.length) {
      poiEl.innerHTML = `<p class="hint">В радиусе ${data.radius_m}м объектов не найдено</p>`;
      return;
    }

    poiEl.innerHTML = "";
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
        empty.textContent = "не найдено";
        list.appendChild(empty);
      } else {
        items.forEach((it) => {
          const p = document.createElement("div");
          p.className = "poi-item";

          const title = it.title ? it.title : "Объект";
          const dist = it.dist_m != null ? it.dist_m : "";

          if (it.org_url) {
            p.innerHTML = `<a href="${escapeHtml(it.org_url)}" target="_blank" rel="noreferrer">${escapeHtml(
              title
            )}</a> <span class="dist">— ${dist}м</span>`;
          } else {
            p.innerHTML = `${escapeHtml(title)} <span class="dist">— ${dist}м</span>`;
          }
          list.appendChild(p);
        });
      }

      wrapper.appendChild(head);
      wrapper.appendChild(list);
      poiEl.appendChild(wrapper);
    });
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
    renderPoiSummary(data);
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
  }

  /**
   * Запрашивает панораму у бэкенда и монтирует её в Pannellum-контейнер.
   * containerId — id div-контейнера, statusElId — id элемента для статуса.
   * Возвращает объект { viewer } либо null, если панорамы нет/ошибка.
   */
  async function loadPanoramaInto(containerId, statusElId, lat, lon) {
    const statusEl = document.getElementById(statusElId);
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    statusEl.textContent = "Ищем панораму рядом с точкой…";
    statusEl.classList.remove("hidden");

    let data;
    try {
      const res = await fetch(`/api/panorama?lat=${lat}&lon=${lon}`);
      data = await res.json();
    } catch (err) {
      statusEl.textContent = "Не удалось связаться с сервером панорам.";
      return null;
    }

    if (data.status === "not_found") {
      statusEl.textContent = "Рядом с этой точкой панорама Яндекса не найдена.";
      return null;
    }
    if (data.status === "error") {
      statusEl.textContent = "Ошибка получения панорамы: " + (data.message || "неизвестно");
      return null;
    }

    statusEl.classList.add("hidden");

    const viewer = pannellum.viewer(containerId, {
      type: "equirectangular",
      panorama: data.url,
      autoLoad: true,
      showControls: true,
      compass: false,
    });

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
    showModal("view-point-modal");

    const result = await loadPanoramaInto(
      "view-panorama",
      "view-panorama-status",
      point.lat,
      point.lon
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
    document.getElementById("view-point-title").textContent = panoPoint.title || "Воздушная панорама";
    document.getElementById("view-point-description").textContent = panoPoint.description || "";
    showModal("view-point-modal");

    const result = await loadPanoramaInto(
      "view-panorama",
      "view-panorama-status",
      panoPoint.lat,
      panoPoint.lon
    );
    if (result) viewViewer = result.viewer;

    if (panoPoint && panoPoint.lat != null && panoPoint.lon != null) {
      loadPoiSummaryForLatLon(panoPoint.lat, panoPoint.lon);
    }
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
