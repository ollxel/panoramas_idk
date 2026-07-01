(function () {
  "use strict";

  const appEl = document.getElementById("app");
  const isAdmin = appEl.dataset.isAdmin === "true";

  // ------------------------------------------------------------------
  // Карта (Leaflet + OSM)
  // ------------------------------------------------------------------

  const map = L.map("map").setView([55.751244, 37.618423], 11); // Москва по умолчанию

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const markers = new Map(); // id -> L.Marker

  function markerPopupHtml(point) {
    return `<b>${escapeHtml(point.title)}</b>`;
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

  async function loadPoints() {
    const res = await fetch("/api/points");
    const points = await res.json();
    markers.forEach((m) => map.removeLayer(m));
    markers.clear();
    points.forEach(addMarker);
  }

  loadPoints();

  // ------------------------------------------------------------------
  // Модалки: общие помощники
  // ------------------------------------------------------------------

  function showModal(id) {
    document.getElementById(id).classList.remove("hidden");
  }
  function hideModal(id) {
    document.getElementById(id).classList.add("hidden");
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
