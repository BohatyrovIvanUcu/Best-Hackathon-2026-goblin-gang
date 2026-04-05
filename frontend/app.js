const state = {
  activeView: "dispatcher",
  health: null,
  network: { nodes: [], edges: [] },
  demand: [],
  routes: [],
  stock: [],
  routeDetails: new Map(),
  isBusy: false,
  mapViewport: {
    scale: 1,
    x: 0,
    y: 0,
    isDragging: false,
    pointerId: null,
    dragStartX: 0,
    dragStartY: 0,
    dragOriginX: 0,
    dragOriginY: 0,
  },
  mapLayout: {
    points: [],
    edges: [],
    routes: [],
  },
};

const elements = {
  healthStatus: document.getElementById("health-status"),
  lastAction: document.getElementById("last-action"),
  messageBanner: document.getElementById("message-banner"),
  dispatcherView: document.getElementById("dispatcher-view"),
  warehouseView: document.getElementById("warehouse-view"),
  dispatcherTab: document.getElementById("dispatcher-tab"),
  warehouseTab: document.getElementById("warehouse-tab"),
  loadDemoButton: document.getElementById("load-demo-button"),
  generateButton: document.getElementById("generate-button"),
  solveButton: document.getElementById("solve-button"),
  refreshButton: document.getElementById("refresh-button"),
  summaryCards: document.getElementById("summary-cards"),
  networkMap: document.getElementById("network-map"),
  demandList: document.getElementById("demand-list"),
  routesList: document.getElementById("routes-list"),
  warehouseRoutes: document.getElementById("warehouse-routes"),
  stockList: document.getElementById("stock-list"),
};

const ROUTE_COLORS = ["#2563eb", "#f97316", "#8b5cf6", "#0f9f6e", "#dc2626", "#0891b2"];
const MAP_ZOOM = {
  min: 1,
  max: 6,
  step: 1.18,
  labelsScaleThreshold: 1.35,
  clusterDistance: 22,
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindEvents();
  await safeAction("Початкове завантаження", async () => {
    await loadHealth();
    await refreshAllData();
  });
}

function bindEvents() {
  elements.dispatcherTab.addEventListener("click", () => switchView("dispatcher"));
  elements.warehouseTab.addEventListener("click", () => switchView("warehouse"));
  elements.loadDemoButton.addEventListener("click", () => {
    safeAction("Завантажую локальний демо-набір", async () => {
      await apiPost("/api/demo/load");
      await refreshAllData();
    });
  });
  elements.generateButton.addEventListener("click", () => {
    safeAction("Генерую нову невелику мережу", async () => {
      await apiPost("/api/generate", {
        n_factories: 2,
        n_warehouses: 3,
        n_stores: 10,
        n_trucks: 5,
        seed: 42,
      });
      await refreshAllData();
    });
  });
  elements.solveButton.addEventListener("click", () => {
    safeAction("Будую маршрути для всіх машин", async () => {
      await apiPost("/api/solve", { departure_time: "08:00" });
      await refreshAllData(true);
    });
  });
  elements.refreshButton.addEventListener("click", () => {
    safeAction("Оновлюю всі дані на екрані", async () => {
      await refreshAllData(true);
    });
  });
}

function switchView(view) {
  state.activeView = view;
  const dispatcherActive = view === "dispatcher";
  elements.dispatcherView.classList.toggle("hidden", !dispatcherActive);
  elements.warehouseView.classList.toggle("hidden", dispatcherActive);
  elements.dispatcherTab.classList.toggle("active", dispatcherActive);
  elements.warehouseTab.classList.toggle("active", !dispatcherActive);
  if (view === "warehouse" && state.routes.length && state.routeDetails.size === 0) {
    safeAction("Підвантажую деталі для складу", async () => {
      await loadRouteDetails();
      renderWarehouseRoutes();
    });
  }
}

async function loadHealth() {
  state.health = await apiGet("/api/health");
  elements.healthStatus.textContent = state.health.status === "ok" ? "Працює" : "Проблема";
}

async function refreshAllData(loadDetails = false) {
  const [network, demandResponse, routesResponse, stockResponse] = await Promise.all([
    apiGet("/api/network"),
    apiGet("/api/demand"),
    apiGet("/api/routes"),
    apiGet("/api/stock"),
  ]);

  state.network = normalizeNetwork(network);
  state.demand = Array.isArray(demandResponse.demand) ? demandResponse.demand : [];
  state.routes = Array.isArray(routesResponse.routes) ? routesResponse.routes : [];
  state.stock = Array.isArray(stockResponse.stock) ? stockResponse.stock : [];

  if ((loadDetails || state.activeView === "warehouse") && state.routes.length > 0) {
    await loadRouteDetails();
  } else if (!state.routes.length) {
    state.routeDetails = new Map();
  }

  renderAll();
}

async function loadRouteDetails() {
  const detailsEntries = await Promise.all(
    state.routes.map(async (route) => {
      try {
        const detail = await apiGet(`/api/routes/${encodeURIComponent(route.truck_id)}`);
        return [route.truck_id, detail];
      } catch (error) {
        return [route.truck_id, { error: error.message }];
      }
    })
  );

  state.routeDetails = new Map(detailsEntries);
}

function renderAll() {
  renderSummary();
  renderMap();
  renderDemand();
  renderRoutes();
  renderWarehouseRoutes();
  renderStock();
}

function renderSummary() {
  const totals = {
    stores: state.network.nodes.filter((node) => node.type === "store").length,
    critical: state.demand.filter((item) => item.priority === "CRITICAL").length,
    routes: state.routes.length,
    stockRows: state.stock.length,
  };

  const routeKm = sumBy(state.routes, (route) => Number(route.total_km || 0));
  const routeCost = sumBy(state.routes, (route) => Number(route.total_cost || 0));

  elements.summaryCards.innerHTML = [
    summaryCard("Магазини", totals.stores),
    summaryCard("Критичні точки", totals.critical),
    summaryCard("Маршрути", totals.routes),
    summaryCard("Кілометри", roundValue(routeKm)),
    summaryCard("Вартість", `${roundValue(routeCost)} грн`),
    summaryCard("Рядки складу", totals.stockRows),
  ].join("");
}

function renderMap() {
  const { nodes, edges } = state.network;
  if (!nodes.length) {
    elements.networkMap.innerHTML = `
      <div class="map-empty">
        <div>
          <strong>Поки немає даних.</strong>
          <p>Натисни “Завантажити демо” або “Згенерувати нову мережу”.</p>
        </div>
      </div>
    `;
    return;
  }

  const points = projectNodes(nodes);
  const pointById = new Map(points.map((point) => [point.id, point]));
  const svgLines = [];
  const seenPairs = new Set();
  const visibleEdges = [];

  edges.forEach((edge) => {
    const from = pointById.get(edge.from_id);
    const to = pointById.get(edge.to_id);
    if (!from || !to) {
      return;
    }
    const pairKey = [edge.from_id, edge.to_id].sort().join("|");
    if (seenPairs.has(pairKey)) {
      return;
    }
    seenPairs.add(pairKey);
    visibleEdges.push({ fromId: edge.from_id, toId: edge.to_id });
    svgLines.push(
      `<line class="map-edge" data-from-id="${escapeHtml(edge.from_id)}" data-to-id="${escapeHtml(edge.to_id)}" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="#b8c8d8" stroke-width="2" opacity="0.65"></line>`
    );
  });

  const visibleRoutes = [];
  state.routes.forEach((route, routeIndex) => {
    const color = ROUTE_COLORS[routeIndex % ROUTE_COLORS.length];
    const routePoints = route.stops
      .map((stopId) => pointById.get(stopId))
      .filter(Boolean);
    if (routePoints.length < 2) {
      return;
    }
    visibleRoutes.push({ stops: route.stops });
    const polyline = routePoints.map((point) => `${point.x},${point.y}`).join(" ");
    svgLines.push(
      `<polyline class="route-line" data-route-index="${visibleRoutes.length - 1}" points="${polyline}" stroke="${color}" stroke-width="5"></polyline>`
    );
  });

  const svgPoints = points
    .map((point) => {
      const fill = colorForNode(point);
      const radius = point.type === "warehouse" ? 12 : point.type === "factory" ? 11 : 10;
      const labelClass = point.type === "store" ? "map-label store-label" : "map-label hub-label";
      return `
        <g class="map-node" data-node-id="${escapeHtml(point.id)}" data-base-x="${point.x}" data-base-y="${point.y}">
          <circle
            class="node-point"
            cx="${point.x}"
            cy="${point.y}"
            r="${radius}"
            fill="${fill}"
            data-node-id="${escapeHtml(point.id)}"
          ></circle>
          <text class="${labelClass}" x="${point.x + 14}" y="${point.y - 12}">${escapeHtml(shortLabel(point.name))}</text>
        </g>
      `;
    })
    .join("");

  elements.networkMap.innerHTML = `
    <div class="map-toolbar">
      <div class="map-controls">
        <button type="button" class="map-control-button" data-map-zoom="out" aria-label="Зменшити карту">-</button>
        <button type="button" class="map-control-button" data-map-zoom="in" aria-label="Збільшити карту">+</button>
        <button type="button" class="map-control-button reset" data-map-reset>Скинути</button>
      </div>
      <div class="map-status">
        <span class="map-gesture-hint">Колесо: масштаб, перетягування: панорама</span>
        <span class="map-zoom-readout">Масштаб <strong data-map-zoom-level>100%</strong></span>
      </div>
    </div>
    <div class="map-viewport" data-map-viewport data-labels="compact">
      <div class="map-scene" data-map-scene>
        <svg class="map-svg" viewBox="0 0 1000 520" preserveAspectRatio="none">
          ${svgLines.join("")}
          ${svgPoints}
        </svg>
      </div>
    </div>
  `;

  state.mapLayout = {
    points,
    edges: visibleEdges,
    routes: visibleRoutes,
  };

  elements.networkMap.querySelectorAll(".map-node").forEach((nodeElement) => {
    nodeElement.addEventListener("click", (event) => {
      const nodeId = event.currentTarget.getAttribute("data-node-id");
      const node = nodes.find((item) => item.id === nodeId);
      if (!node) {
        return;
      }
      const detailText = buildNodeTooltip(node);
      showMessage(detailText, "info");
    });
  });

  setupMapInteractions();
}

function renderDemand() {
  if (!state.demand.length) {
    elements.demandList.innerHTML = emptyCard("Поки нема попиту. Спочатку завантаж дані.");
    return;
  }

  elements.demandList.innerHTML = state.demand
    .slice(0, 12)
    .map((item) => {
      const priorityClass = priorityClassName(item.priority);
      return `
        <article class="card ${priorityClass}">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(item.node_name)}</h3>
              <div class="pill ${priorityClass}">${escapeHtml(item.priority)}</div>
            </div>
            <div class="meta">
              Потрібно: <strong>${roundValue(item.requested_qty)} кг</strong><br>
              Товар: ${escapeHtml(item.product_name)}
            </div>
          </div>
          <p class="meta">
            Зараз у магазині: ${roundValue(item.current_stock)} кг із мінімуму
            ${roundValue(item.min_stock)} кг.
          </p>
          <div class="field-inline">
            <input type="number" min="1" step="1" value="${Math.max(1, Math.round(Number(item.requested_qty || 1)))}"
              data-urgent-qty="${escapeHtml(item.node_id)}|${escapeHtml(item.product_id)}">
            <button class="mini-button danger" data-urgent="${escapeHtml(item.node_id)}|${escapeHtml(item.product_id)}">
              Терміново
            </button>
          </div>
        </article>
      `;
    })
    .join("");

  elements.demandList.querySelectorAll("[data-urgent]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [nodeId, productId] = button.dataset.urgent.split("|");
      const input = elements.demandList.querySelector(
        `[data-urgent-qty="${CSS.escape(`${nodeId}|${productId}`)}"]`
      );
      const qty = Number(input?.value || 1);
      await safeAction(`Роблю терміновий запит для ${nodeId}`, async () => {
        await apiPost("/api/urgent", {
          node_id: nodeId,
          product_id: productId,
          qty,
          departure_time: "08:00",
        });
        await refreshAllData(true);
      });
    });
  });
}

function renderRoutes() {
  if (!state.routes.length) {
    elements.routesList.innerHTML = emptyCard("Маршрути ще не побудовані. Натисни “Побудувати маршрути”.");
    return;
  }

  elements.routesList.innerHTML = state.routes
    .map((route) => {
      const execution = route.execution || {};
      const truckState = execution.truck_state || {};
      return `
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(route.truck_name)}</h3>
              <div class="pill neutral">${escapeHtml(route.truck_type)} · Leg ${escapeHtml(String(route.leg))}</div>
            </div>
            <div class="meta">
              ${roundValue(route.total_km)} км<br>
              ${roundValue(route.total_cost)} грн
            </div>
          </div>
          <p class="meta">
            Статус машини: <strong>${escapeHtml(truckState.status || "невідомо")}</strong><br>
            Наступна точка: <strong>${escapeHtml(execution.current_stop_name || "очікує")}</strong>
          </p>
          <ol class="small-list">${route.stops_names.map((name) => `<li>${escapeHtml(name)}</li>`).join("")}</ol>
        </article>
      `;
    })
    .join("");
}

function renderWarehouseRoutes() {
  if (!state.routes.length) {
    elements.warehouseRoutes.innerHTML = emptyCard("Після побудови маршрутів тут з'являться машини для складу.");
    return;
  }

  elements.warehouseRoutes.innerHTML = state.routes
    .map((route) => {
      const detail = state.routeDetails.get(route.truck_id);
      const detailRoute = detail?.route;
      const execution = detailRoute?.execution || route.execution || {};
      const truckState = execution.truck_state || {};
      const firstStop = detailRoute?.stops_details?.[0];
      const nextStop = detailRoute?.stops_details?.[execution.next_stop_index ?? 0];
      const loadItems = Array.isArray(firstStop?.cargo_to_load) ? firstStop.cargo_to_load : [];
      const allowedActions = getAllowedActions(execution);
      const itemList = loadItems.length
        ? `<ul class="small-list">${loadItems
            .map(
              (item) =>
                `<li>${escapeHtml(item.product_name)}: ${roundValue(item.qty_kg)} кг для ${escapeHtml(item.for_store)}</li>`
            )
            .join("")}</ul>`
        : `<p class="meta">Тут немає вантажу для завантаження або деталі ще не завантажились.</p>`;

      return `
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(route.truck_name)}</h3>
              <div class="pill neutral">${escapeHtml(truckState.status || "idle")}</div>
            </div>
            <div class="meta">
              Маршрут #${escapeHtml(String(route.id))}<br>
              Наступна точка: ${escapeHtml(nextStop?.node_name || "очікує")}
            </div>
          </div>
          <p class="meta">
            1. Завантажити товар<br>
            2. Підтвердити готовність<br>
            3. Відправити машину<br>
            4. Позначити наступну зупинку виконаною
          </p>
          ${itemList}
          <div class="action-grid">
            <button class="mini-button light" data-action="start-loading" data-truck="${escapeHtml(route.truck_id)}" ${allowedActions.startLoading ? "" : "disabled"}>Почати завантаження</button>
            <button class="mini-button primary" data-action="complete-loading" data-truck="${escapeHtml(route.truck_id)}" ${allowedActions.completeLoading ? "" : "disabled"}>Завантажено</button>
            <button class="mini-button success" data-action="depart" data-truck="${escapeHtml(route.truck_id)}" ${allowedActions.depart ? "" : "disabled"}>Машина виїхала</button>
            <button class="mini-button warning" data-action="stop-complete" data-route="${escapeHtml(String(route.id))}" ${allowedActions.completeStop ? "" : "disabled"}>Наступну зупинку виконано</button>
          </div>
          <div class="mini-actions">
            ${loadItems
              .map(
                (item) => `
                  <button
                    class="mini-button"
                    data-action="ship-stock"
                    data-warehouse="${escapeHtml(firstStop?.node_id || "")}"
                    data-product="${escapeHtml(item.product_id)}"
                    data-qty="${escapeHtml(String(item.qty_kg))}"
                    ${allowedActions.shipStock ? "" : "disabled"}
                  >
                    Списати ${roundValue(item.qty_kg)} кг ${escapeHtml(item.product_name)}
                  </button>
                `
              )
              .join("")}
          </div>
        </article>
      `;
    })
    .join("");

  elements.warehouseRoutes.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      if (action === "start-loading") {
        await safeAction("Позначаю початок завантаження", async () => {
          await apiPost(`/api/trucks/${encodeURIComponent(button.dataset.truck)}/loading/start`, {});
          await refreshAllData(true);
        });
      } else if (action === "complete-loading") {
        await safeAction("Позначаю, що завантаження завершене", async () => {
          await apiPost(`/api/trucks/${encodeURIComponent(button.dataset.truck)}/loading/complete`, {});
          await refreshAllData(true);
        });
      } else if (action === "depart") {
        await safeAction("Позначаю виїзд машини", async () => {
          await apiPost(`/api/trucks/${encodeURIComponent(button.dataset.truck)}/depart`, {});
          await refreshAllData(true);
        });
      } else if (action === "stop-complete") {
        await safeAction("Позначаю наступну зупинку як виконану", async () => {
          await apiPost(`/api/routes/${encodeURIComponent(button.dataset.route)}/stop-complete`, {});
          await refreshAllData(true);
        });
      } else if (action === "ship-stock") {
        await safeAction("Оновлюю склад після відвантаження", async () => {
          await apiPost("/api/stock/update", {
            warehouse_id: button.dataset.warehouse,
            product_id: button.dataset.product,
            qty_shipped_kg: Number(button.dataset.qty),
          });
          await refreshAllData(true);
        });
      }
    });
  });
}

function renderStock() {
  if (!state.stock.length) {
    elements.stockList.innerHTML = emptyCard("Складські залишки з'являться після завантаження даних.");
    return;
  }

  elements.stockList.innerHTML = state.stock
    .map((item) => {
      const lowStockClass = Number(item.available_kg) < 100 ? "low-stock" : "";
      return `
        <article class="card ${lowStockClass}">
          <div class="row-between">
            <div>
              <h3 class="card-title">${escapeHtml(item.warehouse_name)}</h3>
              <p class="meta">${escapeHtml(item.product_name)}</p>
            </div>
            <div class="meta">
              Доступно: <strong>${roundValue(item.available_kg)} кг</strong><br>
              Резерв: ${roundValue(item.reserved_kg)} кг
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

async function safeAction(label, action) {
  if (state.isBusy) {
    return;
  }

  setBusy(true);
  showMessage(`${label}...`, "info");
  elements.lastAction.textContent = label;
  try {
    await action();
    showMessage(`${label}. Успіх.`, "success");
  } catch (error) {
    showMessage(`${label}. Помилка: ${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

function setBusy(isBusy) {
  state.isBusy = isBusy;
  [
    elements.loadDemoButton,
    elements.generateButton,
    elements.solveButton,
    elements.refreshButton,
    elements.dispatcherTab,
    elements.warehouseTab,
  ].forEach((element) => {
    element.disabled = isBusy;
  });
}

function showMessage(message, type) {
  elements.messageBanner.textContent = message;
  elements.messageBanner.className = `message-banner ${type}`;
}

async function apiGet(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });
  return parseResponse(response);
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseResponse(response);
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Невідома помилка");
  }
  return data;
}

function normalizeNetwork(network) {
  const nodes = Array.isArray(network.nodes) ? network.nodes : [];
  const edges = Array.isArray(network.edges) ? network.edges : [];
  return { nodes, edges };
}

function projectNodes(nodes) {
  const lonValues = nodes.map((node) => Number(node.lon || 0));
  const latValues = nodes.map((node) => Number(node.lat || 0));
  const lonMin = Math.min(...lonValues);
  const lonMax = Math.max(...lonValues);
  const latMin = Math.min(...latValues);
  const latMax = Math.max(...latValues);
  const width = 1000;
  const height = 520;
  const padding = 70;

  return nodes.map((node) => {
    const lon = Number(node.lon || 0);
    const lat = Number(node.lat || 0);
    const x = scaleValue(lon, lonMin, lonMax, padding, width - padding);
    const y = scaleValue(lat, latMin, latMax, height - padding, padding);
    return { ...node, x, y };
  });
}

function scaleValue(value, min, max, outMin, outMax) {
  if (min === max) {
    return (outMin + outMax) / 2;
  }
  return outMin + ((value - min) / (max - min)) * (outMax - outMin);
}

function buildNodeTooltip(node) {
  const parts = [
    `${node.name}`,
    `Тип: ${node.type}`,
  ];
  if (node.type === "store") {
    parts.push(`Пріоритет: ${node.priority || "NORMAL"}`);
    parts.push(`Зараз: ${roundValue(node.current_stock || 0)} кг`);
    parts.push(`Мінімум: ${roundValue(node.min_stock || 0)} кг`);
  }
  if (node.type !== "store") {
    parts.push(`Місткість: ${roundValue(node.capacity_kg || 0)} кг`);
  }
  return parts.join(" | ");
}

function colorForNode(node) {
  if (node.type === "factory") {
    return "var(--factory)";
  }
  if (node.type === "warehouse") {
    return "var(--warehouse)";
  }
  if (node.priority === "CRITICAL") {
    return "var(--danger)";
  }
  if (node.priority === "ELEVATED") {
    return "var(--warning)";
  }
  return "#16a34a";
}

function priorityClassName(priority) {
  if (priority === "CRITICAL") {
    return "critical";
  }
  if (priority === "ELEVATED") {
    return "elevated";
  }
  return "normal";
}

function getAllowedActions(execution) {
  const truckStatus = execution?.truck_state?.status;
  const routeStatus = execution?.route_status;

  return {
    startLoading: truckStatus === "idle" && routeStatus === "planned",
    completeLoading: truckStatus === "loading" && routeStatus === "loading",
    depart: truckStatus === "loaded" && routeStatus === "loading",
    completeStop: truckStatus === "en_route" && routeStatus === "in_progress",
    shipStock: truckStatus === "loading" || truckStatus === "loaded",
  };
}

function shortLabel(name) {
  return String(name).split(" ").slice(0, 2).join(" ");
}

function emptyCard(text) {
  return `<div class="empty-card">${escapeHtml(text)}</div>`;
}

function summaryCard(label, value) {
  return `
    <div class="summary-card">
      <span class="label">${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `;
}

function roundValue(value) {
  return Number(value || 0).toFixed(1).replace(".0", "");
}

function sumBy(items, getter) {
  return items.reduce((sum, item) => sum + getter(item), 0);
}

function setupMapInteractions() {
  const viewport = elements.networkMap.querySelector("[data-map-viewport]");
  const scene = elements.networkMap.querySelector("[data-map-scene]");
  if (!viewport || !scene) {
    return;
  }

  viewport.addEventListener("wheel", handleMapWheel, { passive: false });
  viewport.addEventListener("pointerdown", handleMapPointerDown);
  viewport.addEventListener("pointermove", handleMapPointerMove);
  viewport.addEventListener("pointerup", handleMapPointerEnd);
  viewport.addEventListener("pointerleave", handleMapPointerEnd);
  viewport.addEventListener("pointercancel", handleMapPointerEnd);
  viewport.addEventListener("dblclick", handleMapDoubleClick);

  elements.networkMap.querySelectorAll("[data-map-zoom]").forEach((button) => {
    button.addEventListener("click", () => {
      const factor = button.dataset.mapZoom === "in" ? MAP_ZOOM.step : 1 / MAP_ZOOM.step;
      zoomMapFromViewportCenter(factor);
    });
  });

  const resetButton = elements.networkMap.querySelector("[data-map-reset]");
  resetButton?.addEventListener("click", resetMapViewport);

  applyMapTransform();
}

function handleMapWheel(event) {
  event.preventDefault();
  const factor = event.deltaY < 0 ? MAP_ZOOM.step : 1 / MAP_ZOOM.step;
  zoomMapAtClientPoint(event.currentTarget, event.clientX, event.clientY, factor);
}

function handleMapDoubleClick(event) {
  event.preventDefault();
  zoomMapAtClientPoint(event.currentTarget, event.clientX, event.clientY, MAP_ZOOM.step);
}

function handleMapPointerDown(event) {
  if (event.button !== 0 || state.mapViewport.scale <= MAP_ZOOM.min) {
    return;
  }
  if (event.target.closest(".map-node, .map-control-button")) {
    return;
  }

  const viewport = event.currentTarget;
  state.mapViewport.isDragging = true;
  state.mapViewport.pointerId = event.pointerId;
  state.mapViewport.dragStartX = event.clientX;
  state.mapViewport.dragStartY = event.clientY;
  state.mapViewport.dragOriginX = state.mapViewport.x;
  state.mapViewport.dragOriginY = state.mapViewport.y;
  viewport.classList.add("is-dragging");
  viewport.setPointerCapture(event.pointerId);
}

function handleMapPointerMove(event) {
  if (!state.mapViewport.isDragging || state.mapViewport.pointerId !== event.pointerId) {
    return;
  }

  state.mapViewport.x = state.mapViewport.dragOriginX + (event.clientX - state.mapViewport.dragStartX);
  state.mapViewport.y = state.mapViewport.dragOriginY + (event.clientY - state.mapViewport.dragStartY);
  applyMapTransform(event.currentTarget);
}

function handleMapPointerEnd(event) {
  if (!state.mapViewport.isDragging || state.mapViewport.pointerId !== event.pointerId) {
    return;
  }

  const pointerId = state.mapViewport.pointerId;
  state.mapViewport.isDragging = false;
  state.mapViewport.pointerId = null;
  event.currentTarget.classList.remove("is-dragging");
  if (pointerId !== null && event.currentTarget.hasPointerCapture(pointerId)) {
    event.currentTarget.releasePointerCapture(pointerId);
  }
}

function zoomMapFromViewportCenter(factor) {
  const viewport = elements.networkMap.querySelector("[data-map-viewport]");
  if (!viewport) {
    return;
  }

  const rect = viewport.getBoundingClientRect();
  zoomMapAtClientPoint(viewport, rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
}

function zoomMapAtClientPoint(viewport, clientX, clientY, factor) {
  const rect = viewport.getBoundingClientRect();
  const pointerX = clientX - rect.left;
  const pointerY = clientY - rect.top;
  const previousScale = state.mapViewport.scale;
  const nextScale = clamp(previousScale * factor, MAP_ZOOM.min, MAP_ZOOM.max);

  if (nextScale === previousScale) {
    return;
  }

  const worldX = (pointerX - state.mapViewport.x) / previousScale;
  const worldY = (pointerY - state.mapViewport.y) / previousScale;

  state.mapViewport.scale = nextScale;
  state.mapViewport.x = pointerX - worldX * nextScale;
  state.mapViewport.y = pointerY - worldY * nextScale;
  applyMapTransform(viewport);
}

function resetMapViewport() {
  state.mapViewport.scale = MAP_ZOOM.min;
  state.mapViewport.x = 0;
  state.mapViewport.y = 0;
  state.mapViewport.isDragging = false;
  state.mapViewport.pointerId = null;
  const viewport = elements.networkMap.querySelector("[data-map-viewport]");
  viewport?.classList.remove("is-dragging");
  applyMapTransform(viewport);
}

function applyMapTransform(viewportOverride) {
  const viewport = viewportOverride || elements.networkMap.querySelector("[data-map-viewport]");
  const scene = elements.networkMap.querySelector("[data-map-scene]");
  const zoomLevel = elements.networkMap.querySelector("[data-map-zoom-level]");
  if (!viewport || !scene) {
    return;
  }

  const clamped = clampMapViewport(viewport);
  state.mapViewport.x = clamped.x;
  state.mapViewport.y = clamped.y;
  state.mapViewport.scale = clamped.scale;

  updateMapGeometry();
  scene.style.transform = `translate(${state.mapViewport.x}px, ${state.mapViewport.y}px) scale(${state.mapViewport.scale})`;
  viewport.dataset.labels =
    state.mapViewport.scale >= MAP_ZOOM.labelsScaleThreshold ? "full" : "compact";
  viewport.dataset.pan = state.mapViewport.scale > MAP_ZOOM.min ? "enabled" : "disabled";

  if (zoomLevel) {
    zoomLevel.textContent = `${Math.round(state.mapViewport.scale * 100)}%`;
  }
}

function clampMapViewport(viewport) {
  const rect = viewport.getBoundingClientRect();
  const width = Math.max(rect.width, 1);
  const height = Math.max(rect.height, 1);
  const scale = clamp(state.mapViewport.scale, MAP_ZOOM.min, MAP_ZOOM.max);

  if (scale <= MAP_ZOOM.min) {
    return { scale: MAP_ZOOM.min, x: 0, y: 0 };
  }

  const minX = width - width * scale;
  const minY = height - height * scale;

  return {
    scale,
    x: clamp(state.mapViewport.x, minX, 0),
    y: clamp(state.mapViewport.y, minY, 0),
  };
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function updateMapGeometry() {
  if (!state.mapLayout.points.length) {
    return;
  }

  const pointMap = new Map(
    calculateExpandedPoints(state.mapLayout.points, state.mapViewport.scale).map((point) => [point.id, point])
  );

  elements.networkMap.querySelectorAll(".map-node").forEach((nodeElement) => {
    const nodeId = nodeElement.getAttribute("data-node-id");
    const point = pointMap.get(nodeId);
    if (!point) {
      return;
    }

    const baseX = Number(nodeElement.getAttribute("data-base-x"));
    const baseY = Number(nodeElement.getAttribute("data-base-y"));
    nodeElement.setAttribute("transform", `translate(${point.x - baseX} ${point.y - baseY})`);

    const circle = nodeElement.querySelector(".node-point");
    const label = nodeElement.querySelector(".map-label");
    const visuals = calculateNodeVisuals(point, state.mapViewport.scale);

    if (circle) {
      circle.setAttribute("r", visuals.rawRadius);
      circle.setAttribute("stroke-width", visuals.rawStrokeWidth);
    }

    if (label) {
      label.setAttribute("x", baseX + visuals.rawLabelOffsetX);
      label.setAttribute("y", baseY - visuals.rawLabelOffsetY);
      label.style.fontSize = `${visuals.rawFontSize}px`;
    }
  });

  elements.networkMap.querySelectorAll(".map-edge").forEach((edgeElement) => {
    const from = pointMap.get(edgeElement.getAttribute("data-from-id"));
    const to = pointMap.get(edgeElement.getAttribute("data-to-id"));
    if (!from || !to) {
      return;
    }

    edgeElement.setAttribute("x1", from.x);
    edgeElement.setAttribute("y1", from.y);
    edgeElement.setAttribute("x2", to.x);
    edgeElement.setAttribute("y2", to.y);
  });

  elements.networkMap.querySelectorAll(".route-line").forEach((routeElement) => {
    const routeIndex = Number(routeElement.getAttribute("data-route-index"));
    const route = state.mapLayout.routes[routeIndex];
    if (!route) {
      return;
    }

    const polyline = route.stops
      .map((stopId) => pointMap.get(stopId))
      .filter(Boolean)
      .map((point) => `${point.x},${point.y}`)
      .join(" ");
    routeElement.setAttribute("points", polyline);
  });
}

function calculateExpandedPoints(basePoints, scale) {
  const pointMap = new Map(basePoints.map((point) => [point.id, { ...point }]));
  const zoomProgress = Math.max(0, scale - MAP_ZOOM.min);
  if (!zoomProgress) {
    return basePoints.map((point) => ({ ...point }));
  }

  const clusters = buildPointClusters(basePoints, MAP_ZOOM.clusterDistance);
  clusters.forEach((cluster) => {
    if (cluster.length < 2) {
      return;
    }

    const centroid = {
      x: sumBy(cluster, (point) => point.x) / cluster.length,
      y: sumBy(cluster, (point) => point.y) / cluster.length,
    };
    const sorted = [...cluster].sort(clusterPointSort);
    const angleStep = (Math.PI * 2) / sorted.length;
    const spreadRadius = 8 + zoomProgress * 10 + Math.max(0, cluster.length - 3) * 3;

    sorted.forEach((point, index) => {
      const angle = -Math.PI / 2 + index * angleStep;
      const radius = spreadRadius + Math.floor(index / 6) * (6 + zoomProgress * 4);
      pointMap.set(point.id, {
        ...point,
        x: centroid.x + Math.cos(angle) * radius,
        y: centroid.y + Math.sin(angle) * radius,
      });
    });
  });

  return basePoints.map((point) => pointMap.get(point.id) ?? { ...point });
}

function calculateNodeVisuals(point, scale) {
  const zoomRatio = getZoomRatio(scale);
  const screenRadius = point.type === "store"
    ? 15.2 + zoomRatio * 1.7
    : point.type === "warehouse"
      ? 17.2 + zoomRatio * 1.8
      : 16.4 + zoomRatio * 1.7;
  const screenStrokeWidth = 2.4 + zoomRatio * 0.25;
  const screenFontSize = point.type === "store"
    ? 16 + zoomRatio * 1.5
    : 17 + zoomRatio * 1.4;
  const screenLabelOffsetX = 18 + zoomRatio * 2;
  const screenLabelOffsetY = 15 + zoomRatio * 1.4;

  return {
    rawRadius: screenRadius / scale,
    rawStrokeWidth: screenStrokeWidth / scale,
    rawFontSize: screenFontSize / scale,
    rawLabelOffsetX: screenLabelOffsetX / scale,
    rawLabelOffsetY: screenLabelOffsetY / scale,
  };
}

function buildPointClusters(points, distanceThreshold) {
  const clusters = [];
  const visited = new Set();

  points.forEach((point) => {
    if (visited.has(point.id)) {
      return;
    }

    const cluster = [];
    const queue = [point];
    visited.add(point.id);

    while (queue.length) {
      const current = queue.shift();
      cluster.push(current);

      points.forEach((candidate) => {
        if (visited.has(candidate.id)) {
          return;
        }
        if (distanceBetweenPoints(current, candidate) > distanceThreshold) {
          return;
        }

        visited.add(candidate.id);
        queue.push(candidate);
      });
    }

    clusters.push(cluster);
  });

  return clusters;
}

function distanceBetweenPoints(first, second) {
  const dx = first.x - second.x;
  const dy = first.y - second.y;
  return Math.hypot(dx, dy);
}

function getZoomRatio(scale) {
  if (MAP_ZOOM.max === MAP_ZOOM.min) {
    return 0;
  }
  return clamp((scale - MAP_ZOOM.min) / (MAP_ZOOM.max - MAP_ZOOM.min), 0, 1);
}

function clusterPointSort(first, second) {
  const typeOrder = {
    warehouse: 0,
    factory: 1,
    store: 2,
  };
  const typeDelta = (typeOrder[first.type] ?? 99) - (typeOrder[second.type] ?? 99);
  if (typeDelta !== 0) {
    return typeDelta;
  }
  return String(first.name).localeCompare(String(second.name), "uk");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
