const state = {
  activeView: "dispatcher",
  health: null,
  network: { nodes: [], edges: [] },
  demand: [],
  routes: [],
  stock: [],
  routeDetails: new Map(),
  isBusy: false,
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
    svgLines.push(
      `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="#b8c8d8" stroke-width="2" opacity="0.65"></line>`
    );
  });

  state.routes.forEach((route, routeIndex) => {
    const color = ROUTE_COLORS[routeIndex % ROUTE_COLORS.length];
    const routePoints = route.stops
      .map((stopId) => pointById.get(stopId))
      .filter(Boolean);
    if (routePoints.length < 2) {
      return;
    }
    const polyline = routePoints.map((point) => `${point.x},${point.y}`).join(" ");
    svgLines.push(
      `<polyline class="route-line" points="${polyline}" stroke="${color}" stroke-width="5"></polyline>`
    );
  });

  const svgPoints = points
    .map((point) => {
      const fill = colorForNode(point);
      const radius = point.type === "warehouse" ? 12 : point.type === "factory" ? 11 : 10;
      return `
        <g class="map-node" data-node-id="${escapeHtml(point.id)}">
          <circle
            class="node-point"
            cx="${point.x}"
            cy="${point.y}"
            r="${radius}"
            fill="${fill}"
            data-node-id="${escapeHtml(point.id)}"
          ></circle>
          <text class="map-label" x="${point.x + 14}" y="${point.y - 12}">${escapeHtml(shortLabel(point.name))}</text>
        </g>
      `;
    })
    .join("");

  elements.networkMap.innerHTML = `
    <svg class="map-svg" viewBox="0 0 1000 520" preserveAspectRatio="none">
      ${svgLines.join("")}
      ${svgPoints}
    </svg>
  `;

  elements.networkMap.querySelectorAll("[data-node-id]").forEach((nodeElement) => {
    nodeElement.addEventListener("click", (event) => {
      const nodeId = event.target.getAttribute("data-node-id");
      const node = nodes.find((item) => item.id === nodeId);
      if (!node) {
        return;
      }
      const detailText = buildNodeTooltip(node);
      showMessage(detailText, "info");
    });
  });
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
