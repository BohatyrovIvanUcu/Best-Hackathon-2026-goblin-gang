const state = {
  activeView: "dispatcher",
  health: null,
  network: { nodes: [], edges: [] },
  demand: [],
  routes: [],
  stock: [],
  warehouses: [],
  selectedWarehouseId: readStoredWarehouseId(),
  warehouseDashboard: null,
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
  generateSmallButton: document.getElementById("generate-small-button"),
  generateMediumButton: document.getElementById("generate-medium-button"),
  generateLargeButton: document.getElementById("generate-large-button"),
  solveButton: document.getElementById("solve-button"),
  refreshButton: document.getElementById("refresh-button"),
  summaryCards: document.getElementById("summary-cards"),
  networkMap: document.getElementById("network-map"),
  demandList: document.getElementById("demand-list"),
  routesList: document.getElementById("routes-list"),
  warehouseSelect: document.getElementById("warehouse-select"),
  warehouseSummary: document.getElementById("warehouse-summary"),
  warehouseAlerts: document.getElementById("warehouse-alerts"),
  warehouseInbound: document.getElementById("warehouse-inbound"),
  warehouseOutbound: document.getElementById("warehouse-outbound"),
  warehouseStock: document.getElementById("warehouse-stock"),
};

const ROUTE_COLORS = ["#2563eb", "#f97316", "#8b5cf6", "#0f9f6e", "#dc2626", "#0891b2"];
const WAREHOUSE_STORAGE_KEY = "logiflow.selectedWarehouseId";
const MAP_ZOOM = {
  min: 1,
  max: 6,
  step: 1.18,
  labelsScaleThreshold: 1.35,
  clusterDistance: 22,
};
const MAP_SIZE = {
  width: 1000,
  height: 520,
  padding: 88,
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindEvents();
  await safeAction("Початкове завантаження", async () => {
    await loadHealth();
    await refreshAllData();
    await fetchTruckPositions();
  });
  startAnimationLoop();
}

function bindEvents() {
  elements.dispatcherTab.addEventListener("click", () => switchView("dispatcher"));
  elements.warehouseTab.addEventListener("click", () => switchView("warehouse"));
  elements.warehouseSelect.addEventListener("change", () => {
    const nextWarehouseId = elements.warehouseSelect.value;
    state.selectedWarehouseId = nextWarehouseId || null;
    persistSelectedWarehouseId(state.selectedWarehouseId);
    safeAction("Перемикаю робоче місце складу", async () => {
      await refreshWarehouseDashboard();
    });
  });
  elements.loadDemoButton.addEventListener("click", () => {
    safeAction("Завантажую локальний демо-набір", async () => {
      await apiPost("/api/demo/load");
      await refreshAllData();
    });
  });
  elements.generateSmallButton.addEventListener("click", () => generateNetwork("small", "малу"));
  elements.generateMediumButton.addEventListener("click", () => generateNetwork("medium", "середню"));
  elements.generateLargeButton.addEventListener("click", () => generateNetwork("large", "велику"));
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

function generateNetwork(scale, label) {
  safeAction(`Генерую нову ${label} мережу`, async () => {
    await apiPost("/api/generate", { scale });
    await refreshAllData();
  });
}

function switchView(view) {
  state.activeView = view;
  const dispatcherActive = view === "dispatcher";
  elements.dispatcherView.classList.toggle("hidden", !dispatcherActive);
  elements.warehouseView.classList.toggle("hidden", dispatcherActive);
  elements.dispatcherTab.classList.toggle("active", dispatcherActive);
  elements.warehouseTab.classList.toggle("active", !dispatcherActive);
  if (view === "warehouse") {
    safeAction("Готую локальну чергу складу", async () => {
      await loadWarehouses();
      await refreshWarehouseDashboard();
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

  if (state.activeView === "warehouse") {
    await loadWarehouses();
    await refreshWarehouseDashboard();
  }

  renderAll();
  fetchTruckPositions();
}

async function loadWarehouses() {
  const response = await apiGet("/api/warehouses");
  state.warehouses = Array.isArray(response.warehouses) ? response.warehouses : [];
  if (!state.warehouses.length) {
    state.selectedWarehouseId = null;
    state.warehouseDashboard = null;
    return;
  }

  const selectedExists = state.warehouses.some((warehouse) => warehouse.id === state.selectedWarehouseId);
  if (!selectedExists) {
    state.selectedWarehouseId = state.warehouses[0].id;
    persistSelectedWarehouseId(state.selectedWarehouseId);
  }
}

async function refreshWarehouseDashboard() {
  if (!state.warehouses.length) {
    state.warehouseDashboard = null;
    renderWarehouseDashboard();
    return;
  }
  if (!state.selectedWarehouseId) {
    state.selectedWarehouseId = state.warehouses[0]?.id || null;
    persistSelectedWarehouseId(state.selectedWarehouseId);
  }
  if (!state.selectedWarehouseId) {
    state.warehouseDashboard = null;
    renderWarehouseDashboard();
    return;
  }

  state.warehouseDashboard = await apiGet(
    `/api/warehouses/${encodeURIComponent(state.selectedWarehouseId)}/dashboard`
  );
  renderWarehouseDashboard();
}

function renderAll() {
  renderSummary();
  renderMap();
  renderDemand();
  renderRoutes();
  renderWarehouseDashboard();
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
  const svgContent = [];
  const seenPairs = new Set();
  const visibleEdges = [];
  const routeNodeIds = new Set();
  const routeEdgePairs = new Set();
  const visibleRoutes = [];

  state.routes.forEach((route, routeIndex) => {
    const color = ROUTE_COLORS[routeIndex % ROUTE_COLORS.length];
    const routePoints = route.stops
      .map((stopId) => pointById.get(stopId))
      .filter(Boolean);
    if (routePoints.length < 2) {
      return;
    }

    route.stops.forEach((stopId) => routeNodeIds.add(stopId));
    route.stops.forEach((stopId, stopIndex) => {
      const nextStopId = route.stops[stopIndex + 1];
      if (!nextStopId) {
        return;
      }
      routeEdgePairs.add([stopId, nextStopId].sort().join("|"));
    });

    visibleRoutes.push({ stops: route.stops });
    const polyline = routePoints.map((point) => `${point.x},${point.y}`).join(" ");
    svgContent.push(
      `<polyline class="route-line route-line-halo" data-route-index="${visibleRoutes.length - 1}" points="${polyline}" stroke="${color}"></polyline>`,
      `<polyline class="route-line route-line-core" data-route-index="${visibleRoutes.length - 1}" points="${polyline}" stroke="${color}"></polyline>`
    );
  });

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
    const isRouteEdge = routeEdgePairs.has(pairKey);
    svgContent.unshift(
      `<line class="map-edge ${isRouteEdge ? "route-support-edge" : ""}" data-from-id="${escapeHtml(edge.from_id)}" data-to-id="${escapeHtml(edge.to_id)}" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}"></line>`
    );
  });

  const svgPoints = points
    .map((point) => {
      const labelClass = buildMapLabelClass(point);
      const isOnRoute = routeNodeIds.has(point.id);
      const routeStateClass = isOnRoute ? "on-route" : "off-route";
      return `
        <g
          class="map-node ${routeStateClass}"
          data-node-id="${escapeHtml(point.id)}"
          data-base-x="${point.x}"
          data-base-y="${point.y}"
          data-node-shape="${escapeHtml(nodeShapeFor(point))}">
          ${buildNodeShape(point)}
          <text class="${labelClass}" x="${point.x + 16}" y="${point.y - 14}">${escapeHtml(shortLabel(point.name))}</text>
        </g>
      `;
    })
    .join("");

  const criticalStores = nodes.filter((node) => node.type === "store" && node.priority === "CRITICAL").length;
  const elevatedStores = nodes.filter((node) => node.type === "store" && node.priority === "ELEVATED").length;
  elements.networkMap.innerHTML = `
    <div class="map-toolbar">
      <div class="map-controls">
        <button type="button" class="map-control-button" data-map-zoom="out" aria-label="Зменшити карту">-</button>
        <button type="button" class="map-control-button" data-map-zoom="in" aria-label="Збільшити карту">+</button>
        <button type="button" class="map-control-button reset" data-map-reset>Скинути</button>
      </div>
      <div class="map-status">
        <span class="map-metric-chip">Вузли ${nodes.length}</span>
        <span class="map-metric-chip">Ребра ${visibleEdges.length}</span>
        <span class="map-metric-chip">Critical ${criticalStores}</span>
        <span class="map-metric-chip">Elevated ${elevatedStores}</span>
        <span class="map-gesture-hint">Колесо: масштаб, перетягування: панорама</span>
        <span class="map-zoom-readout">Масштаб <strong data-map-zoom-level>100%</strong></span>
      </div>
      <div class="sim-controls">
        <label class="sim-label">
          <input type="checkbox" id="sim-mode-toggle"> Симуляція
        </label>
        <div id="sim-slider-row" style="display:none" class="sim-slider-row">
          <span id="sim-time-display" class="sim-time-display">08:00</span>
          <input type="range" id="sim-time-slider" class="sim-slider" min="0" max="1439" step="1" value="480">
          <select id="sim-speed-select" class="sim-speed-select">
            <option value="0">Пауза</option>
            <option value="1">1x</option>
            <option value="10" selected>10x</option>
            <option value="60">60x</option>
          </select>
        </div>
      </div>
    </div>
    <div class="map-viewport" data-map-viewport data-labels="compact">
      <div class="map-scene" data-map-scene>
        <svg class="map-svg" viewBox="0 0 ${MAP_SIZE.width} ${MAP_SIZE.height}" preserveAspectRatio="xMidYMid meet">
          <defs>
            <filter id="route-glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="4" result="blur"></feGaussianBlur>
              <feMerge>
                <feMergeNode in="blur"></feMergeNode>
                <feMergeNode in="SourceGraphic"></feMergeNode>
              </feMerge>
            </filter>
          </defs>
          <rect class="map-plane" x="0" y="0" width="${MAP_SIZE.width}" height="${MAP_SIZE.height}" rx="28" ry="28"></rect>
          <g class="map-grid">
            ${buildMapGrid()}
          </g>
          ${svgContent.join("")}
          ${svgPoints}
          <g id="truck-layer"></g>
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
  bindSimControls();
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
      const manualPriority = item.manual_priority_override || "AUTO";
      const priorityInputId = `priority-${item.node_id}-${item.product_id}`;
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
          <div class="field-inline priority-inline">
            <label class="field-label" for="${escapeHtml(priorityInputId)}">Пріоритет магазину</label>
            <select
              id="${escapeHtml(priorityInputId)}"
              class="priority-select"
              data-store-priority-select
            >
              <option value="AUTO" ${manualPriority === "AUTO" ? "selected" : ""}>Авто</option>
              <option value="NORMAL" ${manualPriority === "NORMAL" ? "selected" : ""}>NORMAL</option>
              <option value="ELEVATED" ${manualPriority === "ELEVATED" ? "selected" : ""}>ELEVATED</option>
              <option value="CRITICAL" ${manualPriority === "CRITICAL" ? "selected" : ""}>CRITICAL</option>
            </select>
            <button class="mini-button light" data-store-priority="${escapeHtml(item.node_id)}">
              Застосувати пріоритет
            </button>
          </div>
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

  elements.demandList.querySelectorAll("[data-store-priority]").forEach((button) => {
    button.addEventListener("click", async () => {
      const nodeId = button.dataset.storePriority;
      const card = button.closest(".card");
      const select = card?.querySelector("[data-store-priority-select]");
      const selectedPriority = select?.value || "AUTO";
      await safeAction(`Оновлюю пріоритет магазину ${nodeId}`, async () => {
        await apiPost(`/api/stores/${encodeURIComponent(nodeId)}/priority`, {
          priority: selectedPriority === "AUTO" ? null : selectedPriority,
        });
        if (state.routes.length) {
          await apiPost("/api/solve", { departure_time: "08:00" });
        }
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

function renderWarehouseDashboard() {
  renderWarehouseSelector();

  if (!state.warehouses.length) {
    elements.warehouseSummary.innerHTML = "";
    elements.warehouseAlerts.innerHTML = emptyCard("Спочатку завантаж демо або згенеруй мережу зі складами.");
    elements.warehouseInbound.innerHTML = emptyCard("Коли з'являться склади, тут буде локальна вхідна черга.");
    elements.warehouseOutbound.innerHTML = emptyCard("Коли з'являться склади, тут буде локальна вихідна черга.");
    elements.warehouseStock.innerHTML = emptyCard("Локальні залишки з'являться після вибору складу.");
    return;
  }

  if (!state.warehouseDashboard) {
    elements.warehouseSummary.innerHTML = "";
    elements.warehouseAlerts.innerHTML = emptyCard("Підвантажую локальну панель складу.");
    elements.warehouseInbound.innerHTML = emptyCard("Підвантажую вхідні рейси.");
    elements.warehouseOutbound.innerHTML = emptyCard("Підвантажую вихідні рейси.");
    elements.warehouseStock.innerHTML = emptyCard("Підвантажую локальні залишки.");
    return;
  }

  renderWarehouseSummary();
  renderWarehouseAlerts();
  renderWarehouseInbound();
  renderWarehouseOutbound();
  renderWarehouseStock();
}

function renderWarehouseSelector() {
  const selectedId = state.selectedWarehouseId || "";
  elements.warehouseSelect.innerHTML = state.warehouses.length
    ? state.warehouses
        .map(
          (warehouse) => `
            <option value="${escapeHtml(warehouse.id)}" ${warehouse.id === selectedId ? "selected" : ""}>
              ${escapeHtml(warehouse.name)}
            </option>
          `
        )
        .join("")
    : `<option value="">Склади ще не завантажені</option>`;
  elements.warehouseSelect.disabled = state.isBusy || !state.warehouses.length;
}

function renderWarehouseSummary() {
  const dashboard = state.warehouseDashboard;
  const warehouseName = dashboard?.warehouse?.name || "Склад";
  const summary = dashboard?.summary || {};
  elements.warehouseSummary.innerHTML = [
    summaryCard("Склад", warehouseName),
    summaryCard("Вхідні рейси", summary.inbound_count || 0),
    summaryCard("Вихідні рейси", summary.outbound_count || 0),
    summaryCard("Чекає приймання", summary.waiting_receive_count || 0),
    summaryCard("Мало запасу", summary.low_stock_count || 0),
    summaryCard("Блок по комплектуванню", summary.blocked_outbound_count || 0),
  ].join("");
}

function renderWarehouseAlerts() {
  const alerts = Array.isArray(state.warehouseDashboard?.alerts) ? state.warehouseDashboard.alerts : [];
  if (!alerts.length) {
    elements.warehouseAlerts.innerHTML = emptyCard("Локальних алертів поки немає.");
    return;
  }

  elements.warehouseAlerts.innerHTML = alerts
    .map(
      (alert) => `
        <article class="card alert-card ${alert.level || "info"}">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(alertTitle(alert.level))}</h3>
            </div>
          </div>
          <p class="meta">${escapeHtml(alert.text || "")}</p>
        </article>
      `
    )
    .join("");
}

function renderWarehouseInbound() {
  const inbound = Array.isArray(state.warehouseDashboard?.inbound) ? state.warehouseDashboard.inbound : [];
  if (!inbound.length) {
    elements.warehouseInbound.innerHTML = emptyCard("Для цього складу зараз немає активних вхідних поставок.");
    return;
  }

  elements.warehouseInbound.innerHTML = inbound
    .map(
      (route) => `
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(route.truck_name)}</h3>
              <div class="pill neutral">${escapeHtml(route.worker_status)}</div>
            </div>
            <div class="meta">
              Звідки: <strong>${escapeHtml(route.from_node_name || "невідомо")}</strong><br>
              ETA: ${escapeHtml(route.scheduled_time || "немає даних")}
            </div>
          </div>
          <p class="meta">
            Статус рейсу: <strong>${escapeHtml(route.route_status || "planned")}</strong><br>
            Статус машини: <strong>${escapeHtml(route.truck_status || "idle")}</strong>
          </p>
          <ul class="small-list">
            ${route.items
              .map(
                (item) =>
                  `<li>${escapeHtml(item.product_name)}: ${roundValue(item.qty_kg)} кг</li>`
              )
              .join("")}
          </ul>
          <div class="action-grid">
            <button
              class="mini-button light"
              data-warehouse-arrive="${escapeHtml(String(route.route_id))}"
              ${route.can_arrive ? "" : "disabled"}
            >
              Підтвердити прибуття
            </button>
            <button
              class="mini-button success"
              data-warehouse-receive="${escapeHtml(String(route.route_id))}"
              ${route.can_receive ? "" : "disabled"}
            >
              Прийняти поставку
            </button>
          </div>
        </article>
      `
    )
    .join("");

  elements.warehouseInbound.querySelectorAll("[data-warehouse-arrive]").forEach((button) => {
    button.addEventListener("click", async () => {
      const routeId = button.dataset.warehouseArrive;
      await safeAction("Підтверджую прибуття фури на склад", async () => {
        await apiPost(
          `/api/warehouses/${encodeURIComponent(state.selectedWarehouseId)}/inbound/${encodeURIComponent(routeId)}/arrive`,
          {}
        );
        await refreshAllData(true);
      });
    });
  });

  elements.warehouseInbound.querySelectorAll("[data-warehouse-receive]").forEach((button) => {
    button.addEventListener("click", async () => {
      const routeId = button.dataset.warehouseReceive;
      await safeAction("Приймаю поставку на склад", async () => {
        await apiPost(
          `/api/warehouses/${encodeURIComponent(state.selectedWarehouseId)}/inbound/${encodeURIComponent(routeId)}/receive`,
          {}
        );
        await refreshAllData(true);
      });
    });
  });
}

function renderWarehouseOutbound() {
  const outbound = Array.isArray(state.warehouseDashboard?.outbound) ? state.warehouseDashboard.outbound : [];
  if (!outbound.length) {
    elements.warehouseOutbound.innerHTML = emptyCard("Для цього складу зараз немає активних вихідних рейсів.");
    return;
  }

  elements.warehouseOutbound.innerHTML = outbound
    .map(
      (route) => `
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">${escapeHtml(route.truck_name)}</h3>
              <div class="pill neutral">${escapeHtml(route.worker_status)}</div>
            </div>
            <div class="meta">
              Наступна точка: <strong>${escapeHtml(route.next_stop_name || "очікує")}</strong><br>
              ${roundValue(route.total_km)} км · ${roundValue(route.total_cost)} грн
            </div>
          </div>
          <p class="meta">
            До видачі ще: <strong>${roundValue(route.total_reserved_kg)} кг</strong><br>
            Уже видано в машину: <strong>${roundValue(route.total_loaded_kg)} кг</strong>
          </p>
          <div class="mini-actions">
            ${route.items
              .map(
                (item) => `
                  <button
                    class="mini-button ${item.is_issued ? "light issued" : ""}"
                    data-issue-item="${escapeHtml(String(route.route_id))}|${escapeHtml(item.stop_node_id)}|${escapeHtml(item.product_id)}"
                    ${item.is_issued ? "disabled" : ""}
                  >
                    ${item.is_issued ? "Видано" : "Видати"} ${roundValue(item.qty_reserved_kg || item.qty_loaded_kg)} кг
                    ${escapeHtml(item.product_name)} для ${escapeHtml(item.stop_node_name)}
                  </button>
                `
              )
              .join("")}
          </div>
          <div class="action-grid">
            <button
              class="mini-button light"
              data-action="start-loading"
              data-truck="${escapeHtml(route.truck_id)}"
              ${route.can_start_loading ? "" : "disabled"}
            >
              Почати завантаження
            </button>
            <button
              class="mini-button primary"
              data-action="complete-loading"
              data-truck="${escapeHtml(route.truck_id)}"
              ${route.can_complete_loading ? "" : "disabled"}
            >
              Завантаження завершене
            </button>
            <button
              class="mini-button success"
              data-action="depart"
              data-truck="${escapeHtml(route.truck_id)}"
              ${route.can_depart ? "" : "disabled"}
            >
              Відправити фуру
            </button>
          </div>
        </article>
      `
    )
    .join("");

  elements.warehouseOutbound.querySelectorAll("[data-issue-item]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [routeId, stopNodeId, productId] = button.dataset.issueItem.split("|");
      await safeAction("Списую товар у вихідний рейс", async () => {
        await apiPost(
          `/api/warehouses/${encodeURIComponent(state.selectedWarehouseId)}/outbound/${encodeURIComponent(routeId)}/issue-item`,
          {
            stop_node_id: stopNodeId,
            product_id: productId,
          }
        );
        await refreshAllData(true);
      });
    });
  });

  elements.warehouseOutbound.querySelectorAll("[data-action]").forEach((button) => {
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
      }
    });
  });
}

function renderWarehouseStock() {
  const stock = Array.isArray(state.warehouseDashboard?.stock) ? state.warehouseDashboard.stock : [];
  if (!stock.length) {
    elements.warehouseStock.innerHTML = emptyCard("Для цього складу поки немає рядків залишку.");
    return;
  }

  elements.warehouseStock.innerHTML = stock
    .map(
      (item) => `
        <article class="card ${item.is_low ? "low-stock" : ""}">
          <div class="row-between">
            <div>
              <h3 class="card-title">${escapeHtml(item.product_name)}</h3>
              <p class="meta">Лише вибраний склад без повторів назв складів у кожній картці</p>
            </div>
            <div class="meta">
              Доступно: <strong>${roundValue(item.available_kg)} кг</strong><br>
              Всього: ${roundValue(item.quantity_kg)} кг<br>
              Резерв: ${roundValue(item.reserved_kg)} кг
            </div>
          </div>
        </article>
      `
    )
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
    elements.generateSmallButton,
    elements.generateMediumButton,
    elements.generateLargeButton,
    elements.solveButton,
    elements.refreshButton,
    elements.dispatcherTab,
    elements.warehouseTab,
  ].forEach((element) => {
    element.disabled = isBusy;
  });

  if (elements.warehouseSelect) {
    elements.warehouseSelect.disabled = isBusy || !state.warehouses.length;
  }
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
    const detail = data.detail;
    const message = Array.isArray(detail)
      ? detail.map(e => e.msg || JSON.stringify(e)).join("; ")
      : (typeof detail === "string" ? detail : JSON.stringify(detail) || "Невідома помилка");
    throw new Error(message);
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
  const width = MAP_SIZE.width;
  const height = MAP_SIZE.height;
  const padding = MAP_SIZE.padding;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const lonRange = Math.max(lonMax - lonMin, 0.0001);
  const latRange = Math.max(latMax - latMin, 0.0001);
  const scale = Math.min(usableWidth / lonRange, usableHeight / latRange);
  const offsetX = (width - lonRange * scale) / 2;
  const offsetY = (height - latRange * scale) / 2;

  const projected = nodes.map((node) => {
    const lon = Number(node.lon || 0);
    const lat = Number(node.lat || 0);
    const x = offsetX + (lon - lonMin) * scale;
    const y = offsetY + (latMax - lat) * scale;
    return { ...node, x, y };
  });

  return relaxProjectedPoints(projected, width, height, padding);
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

function nodeShapeFor(node) {
  if (node.type === "factory") {
    return "diamond";
  }
  if (node.type === "warehouse") {
    return "square";
  }
  return "circle";
}

function buildNodeShape(point) {
  const fill = colorForNode(point);
  const emphasisRing = point.type === "store" && point.priority === "CRITICAL"
    ? `<circle class="node-ring critical" cx="${point.x}" cy="${point.y}" r="16"></circle>`
    : point.type === "store" && point.priority === "ELEVATED"
      ? `<circle class="node-ring elevated" cx="${point.x}" cy="${point.y}" r="15"></circle>`
      : "";

  if (point.type === "factory") {
    return `
      ${emphasisRing}
      <rect
        class="node-point diamond"
        x="${point.x - 9}"
        y="${point.y - 9}"
        width="18"
        height="18"
        rx="4"
        ry="4"
        fill="${fill}"
        transform="rotate(45 ${point.x} ${point.y})"
      ></rect>
    `;
  }

  if (point.type === "warehouse") {
    return `
      ${emphasisRing}
      <rect
        class="node-point square"
        x="${point.x - 11}"
        y="${point.y - 11}"
        width="22"
        height="22"
        rx="7"
        ry="7"
        fill="${fill}"
      ></rect>
    `;
  }

  return `
    ${emphasisRing}
    <circle
      class="node-point circle"
      cx="${point.x}"
      cy="${point.y}"
      r="10"
      fill="${fill}"
      data-node-id="${escapeHtml(point.id)}"
    ></circle>
  `;
}

function buildMapLabelClass(point) {
  if (point.type !== "store") {
    return "map-label hub-label";
  }
  if (point.priority === "CRITICAL") {
    return "map-label store-label critical-label";
  }
  if (point.priority === "ELEVATED") {
    return "map-label store-label elevated-label";
  }
  return "map-label store-label normal-label";
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

function readStoredWarehouseId() {
  try {
    return window.localStorage.getItem(WAREHOUSE_STORAGE_KEY);
  } catch (error) {
    return null;
  }
}

function persistSelectedWarehouseId(warehouseId) {
  try {
    if (warehouseId) {
      window.localStorage.setItem(WAREHOUSE_STORAGE_KEY, warehouseId);
    } else {
      window.localStorage.removeItem(WAREHOUSE_STORAGE_KEY);
    }
  } catch (error) {
    // Ignore storage failures in demo mode.
  }
}

function alertTitle(level) {
  if (level === "warning") {
    return "Потрібна увага";
  }
  if (level === "success") {
    return "Все під контролем";
  }
  return "Операційний сигнал";
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

  const overflowX = Math.max(width * 0.22, 120);
  const overflowY = Math.max(height * 0.2, 96);
  const minX = width - width * scale - overflowX;
  const minY = height - height * scale - overflowY;
  const maxX = overflowX;
  const maxY = overflowY;

  return {
    scale,
    x: clamp(state.mapViewport.x, minX, maxX),
    y: clamp(state.mapViewport.y, minY, maxY),
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

    const shape = nodeElement.querySelector(".node-point");
    const ring = nodeElement.querySelector(".node-ring");
    const label = nodeElement.querySelector(".map-label");
    const visuals = calculateNodeVisuals(point, state.mapViewport.scale);

    if (shape) {
      updateNodeShapeGeometry(shape, point, visuals);
    }

    if (ring) {
      ring.setAttribute("r", visuals.rawRingRadius);
      ring.setAttribute("stroke-width", visuals.rawRingWidth);
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

  refreshTruckLayer();
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
    const spreadRadius = 10 + zoomProgress * 12 + Math.max(0, cluster.length - 3) * 4;

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
    ? 14.8 + zoomRatio * 1.5
    : point.type === "warehouse"
      ? 17.6 + zoomRatio * 2
      : 16.8 + zoomRatio * 1.8;
  const screenStrokeWidth = 2.6 + zoomRatio * 0.35;
  const screenRingRadius = screenRadius + (point.priority === "CRITICAL" ? 7 : 5.5);
  const screenRingWidth = point.priority === "CRITICAL" ? 3.4 : 2.6;
  const screenFontSize = point.type === "store"
    ? 14.5 + zoomRatio * 1.4
    : 16 + zoomRatio * 1.4;
  const screenLabelOffsetX = 20 + zoomRatio * 2;
  const screenLabelOffsetY = 16 + zoomRatio * 1.5;

  return {
    rawRadius: screenRadius / scale,
    rawStrokeWidth: screenStrokeWidth / scale,
    rawRingRadius: screenRingRadius / scale,
    rawRingWidth: screenRingWidth / scale,
    rawFontSize: screenFontSize / scale,
    rawLabelOffsetX: screenLabelOffsetX / scale,
    rawLabelOffsetY: screenLabelOffsetY / scale,
  };
}

function updateNodeShapeGeometry(shape, point, visuals) {
  const nodeShape = shape.classList.contains("diamond")
    ? "diamond"
    : shape.classList.contains("square")
      ? "square"
      : "circle";

  if (nodeShape === "circle") {
    shape.setAttribute("r", visuals.rawRadius);
    shape.setAttribute("stroke-width", visuals.rawStrokeWidth);
    return;
  }

  const side = visuals.rawRadius * 2;
  shape.setAttribute("x", point.x - side / 2);
  shape.setAttribute("y", point.y - side / 2);
  shape.setAttribute("width", side);
  shape.setAttribute("height", side);
  shape.setAttribute("stroke-width", visuals.rawStrokeWidth);
  shape.setAttribute("rx", nodeShape === "square" ? visuals.rawRadius * 0.62 : visuals.rawRadius * 0.38);
  shape.setAttribute("ry", nodeShape === "square" ? visuals.rawRadius * 0.62 : visuals.rawRadius * 0.38);
  if (nodeShape === "diamond") {
    shape.setAttribute("transform", `rotate(45 ${point.x} ${point.y})`);
  }
}

function relaxProjectedPoints(points, width, height, padding) {
  const relaxed = points.map((point) => ({ ...point, anchorX: point.x, anchorY: point.y }));
  const minDistance = 38;
  const spring = 0.04;

  for (let iteration = 0; iteration < 44; iteration += 1) {
    for (let index = 0; index < relaxed.length; index += 1) {
      for (let candidateIndex = index + 1; candidateIndex < relaxed.length; candidateIndex += 1) {
        const first = relaxed[index];
        const second = relaxed[candidateIndex];
        const dx = second.x - first.x;
        const dy = second.y - first.y;
        const distance = Math.hypot(dx, dy) || 0.001;
        if (distance >= minDistance) {
          continue;
        }

        const push = (minDistance - distance) / 2;
        const offsetX = (dx / distance) * push;
        const offsetY = (dy / distance) * push;
        first.x -= offsetX;
        first.y -= offsetY;
        second.x += offsetX;
        second.y += offsetY;
      }
    }

    relaxed.forEach((point) => {
      point.x += (point.anchorX - point.x) * spring;
      point.y += (point.anchorY - point.y) * spring;
      point.x = clamp(point.x, padding, width - padding);
      point.y = clamp(point.y, padding, height - padding);
    });
  }

  return relaxed.map(({ anchorX, anchorY, ...point }) => point);
}

function buildMapGrid() {
  const lines = [];
  for (let x = 80; x < MAP_SIZE.width; x += 80) {
    lines.push(`<line x1="${x}" y1="0" x2="${x}" y2="${MAP_SIZE.height}"></line>`);
  }
  for (let y = 80; y < MAP_SIZE.height; y += 80) {
    lines.push(`<line x1="0" y1="${y}" x2="${MAP_SIZE.width}" y2="${y}"></line>`);
  }
  return lines.join("");
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

// ─── Truck Animation ──────────────────────────────────────────────────────────

const simState = {
  isSimMode: false,
  simMinutes: 480,
  simSpeed: 10,
  animFrameId: null,
  lastFrameTs: 0,
  lastPollTs: 0,
  truckData: [],
};

function parseTimeToMinutes(hhMm) {
  const [h, m] = String(hhMm || "0:0").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function formatMinutesToHHMM(totalMinutes) {
  const wrapped = ((totalMinutes % 1440) + 1440) % 1440;
  const h = Math.floor(wrapped / 60);
  const m = Math.floor(wrapped % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function getSimulationMinutes() {
  if (simState.isSimMode) {
    return simState.simMinutes;
  }
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
}

function computeTruckPosition(route, simMinutes, expandedPointById, routeIndex) {
  const color = ROUTE_COLORS[routeIndex % ROUTE_COLORS.length];
  const base = { color, truckId: route.truck_id, truckName: route.truck_name };

  const waypoints = [];
  let epochMinutes = null;

  for (const event of (route.timeline || [])) {
    if (!event.node_id || !event.time) continue;
    if (!["departure", "arrival", "return"].includes(event.event)) continue;

    const mins = parseTimeToMinutes(event.time);
    if (epochMinutes === null) epochMinutes = mins;
    const adjusted = mins < epochMinutes - 30 ? mins + 1440 : mins;

    const last = waypoints[waypoints.length - 1];
    if (last && last.node_id === event.node_id && last.minutes === adjusted) continue;

    waypoints.push({ node_id: event.node_id, minutes: adjusted });
  }

  if (waypoints.length === 0) {
    const p = expandedPointById.get(route.stops[0]);
    if (!p) return null;
    return { ...base, x: p.x, y: p.y, phase: "waiting" };
  }

  const firstMins = waypoints[0].minutes;
  const lastMins = waypoints[waypoints.length - 1].minutes;

  if (simMinutes < firstMins) {
    const p = expandedPointById.get(waypoints[0].node_id);
    if (!p) return null;
    return { ...base, x: p.x, y: p.y, phase: "waiting" };
  }

  if (simMinutes >= lastMins) {
    const p = expandedPointById.get(waypoints[waypoints.length - 1].node_id);
    if (!p) return null;
    return { ...base, x: p.x, y: p.y, phase: "completed" };
  }

  for (let i = 0; i < waypoints.length - 1; i++) {
    const from = waypoints[i];
    const to = waypoints[i + 1];

    if (simMinutes < from.minutes || simMinutes >= to.minutes) continue;

    const fromPoint = expandedPointById.get(from.node_id);
    const toPoint = expandedPointById.get(to.node_id);
    if (!fromPoint || !toPoint) return null;

    if (from.node_id === to.node_id) {
      return { ...base, x: fromPoint.x, y: fromPoint.y, phase: "at_stop" };
    }

    const t = (simMinutes - from.minutes) / (to.minutes - from.minutes);
    return {
      ...base,
      x: fromPoint.x + (toPoint.x - fromPoint.x) * t,
      y: fromPoint.y + (toPoint.y - fromPoint.y) * t,
      phase: "en_route",
    };
  }

  return null;
}

function refreshTruckLayer() {
  if (!simState.truckData.length || !state.mapLayout.points.length) return;

  const svg = elements.networkMap.querySelector(".map-svg");
  if (!svg) return;

  let layer = svg.querySelector("#truck-layer");
  if (!layer) {
    layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    layer.setAttribute("id", "truck-layer");
    svg.appendChild(layer);
  }

  const expandedPoints = calculateExpandedPoints(state.mapLayout.points, state.mapViewport.scale);
  const pointById = new Map(expandedPoints.map((p) => [p.id, p]));
  const simMins = getSimulationMinutes();

  const positions = simState.truckData
    .map((route, i) => computeTruckPosition(route, simMins, pointById, i))
    .filter(Boolean);

  const existing = new Map(
    [...layer.querySelectorAll(".truck-icon")].map((el) => [el.dataset.truckId, el])
  );

  positions.forEach((pos) => {
    let g = existing.get(pos.truckId);
    if (!g) {
      g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", "truck-icon");
      g.dataset.truckId = pos.truckId;

      const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      halo.setAttribute("class", "truck-halo");
      halo.setAttribute("r", "18");

      const body = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      body.setAttribute("class", "truck-body");
      body.setAttribute("r", "11");
      body.setAttribute("stroke", "white");
      body.setAttribute("stroke-width", "2.5");

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "truck-label");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("dominant-baseline", "central");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "white");
      label.setAttribute("font-weight", "700");
      label.setAttribute("pointer-events", "none");
      label.textContent = "T";

      g.appendChild(halo);
      g.appendChild(body);
      g.appendChild(label);

      g.addEventListener("click", () => {
        const phaseLabel = {
          en_route: "В дорозі",
          at_stop: "На зупинці",
          waiting: "Очікує",
          completed: "Завершено",
        }[pos.phase] || pos.phase;
        showMessage(`${pos.truckName} · ${phaseLabel}`, "info");
      });

      layer.appendChild(g);
    }

    g.setAttribute("transform", `translate(${pos.x.toFixed(1)}, ${pos.y.toFixed(1)})`);

    const halo = g.querySelector(".truck-halo");
    const body = g.querySelector(".truck-body");

    if (pos.phase === "en_route") {
      body.setAttribute("fill", pos.color);
      body.setAttribute("opacity", "0.95");
      halo.setAttribute("fill", pos.color);
      halo.setAttribute("opacity", "0.2");
      halo.removeAttribute("display");
    } else if (pos.phase === "at_stop") {
      body.setAttribute("fill", pos.color);
      body.setAttribute("opacity", "0.75");
      halo.setAttribute("display", "none");
    } else {
      body.setAttribute("fill", "#94a3b8");
      body.setAttribute("opacity", "0.5");
      halo.setAttribute("display", "none");
    }

    existing.delete(pos.truckId);
  });

  existing.forEach((el) => el.remove());
}

async function fetchTruckPositions() {
  try {
    const data = await apiGet("/api/trucks/positions");
    if (Array.isArray(data.trucks)) {
      simState.truckData = data.trucks;
    }
  } catch (_) {
    // silently ignore — animation continues with cached data
  }
}

function startAnimationLoop() {
  if (simState.animFrameId !== null) return;

  function tick(ts) {
    simState.animFrameId = requestAnimationFrame(tick);

    if (simState.isSimMode && simState.simSpeed > 0 && simState.lastFrameTs > 0) {
      const realElapsedSec = (ts - simState.lastFrameTs) / 1000;
      simState.simMinutes = (simState.simMinutes + realElapsedSec * simState.simSpeed) % 1440;

      const slider = document.getElementById("sim-time-slider");
      if (slider) slider.value = Math.round(simState.simMinutes);
      const display = document.getElementById("sim-time-display");
      if (display) display.textContent = formatMinutesToHHMM(simState.simMinutes);
    }
    simState.lastFrameTs = ts;

    if (!simState.isSimMode && ts - simState.lastPollTs > 3000) {
      simState.lastPollTs = ts;
      fetchTruckPositions();
    }

    refreshTruckLayer();
  }

  simState.animFrameId = requestAnimationFrame(tick);
}

function stopAnimationLoop() {
  if (simState.animFrameId !== null) {
    cancelAnimationFrame(simState.animFrameId);
    simState.animFrameId = null;
  }
}

function bindSimControls() {
  const toggle = document.getElementById("sim-mode-toggle");
  const sliderRow = document.getElementById("sim-slider-row");
  const slider = document.getElementById("sim-time-slider");
  const display = document.getElementById("sim-time-display");
  const speedSelect = document.getElementById("sim-speed-select");

  if (!toggle || !sliderRow || !slider || !display || !speedSelect) return;

  toggle.checked = simState.isSimMode;
  sliderRow.style.display = simState.isSimMode ? "" : "none";
  slider.value = Math.round(simState.simMinutes);
  display.textContent = formatMinutesToHHMM(simState.simMinutes);
  speedSelect.value = String(simState.simSpeed);

  toggle.addEventListener("change", () => {
    simState.isSimMode = toggle.checked;
    sliderRow.style.display = simState.isSimMode ? "" : "none";
  });

  slider.addEventListener("input", () => {
    simState.simMinutes = Number(slider.value);
    display.textContent = formatMinutesToHHMM(simState.simMinutes);
  });

  speedSelect.addEventListener("change", () => {
    simState.simSpeed = Number(speedSelect.value);
  });
}
