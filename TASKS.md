# LogiFlow — План роботи команди

## Склад команди

| Роль | Зона відповідальності |
|---|---|
| Backend 1 | Алгоритм (solver): Dijkstra, assignment, TSP, 2-Opt, En-Route, generator |
| Backend 2 | API (FastAPI): endpoints, CSV парсер, SQLite, offline sync |
| Frontend 1 | Вкладка Диспетчер: карта, solve, urgent, таблиця маршрутів |
| Frontend 2 | Вкладка Склад: список замовлень, offline PWA, індикатор зв'язку |

---

## Стек (зафіксований)

| Шар | Технологія |
|---|---|
| Backend | Python 3.11 + FastAPI + Uvicorn |
| Алгоритм | Власна реалізація (окремий модуль) |
| БД | SQLite (aiosqlite) |
| Frontend | React 18 + TypeScript + Vite + Tailwind |
| Карта | react-leaflet + OpenStreetMap |
| Offline | vite-plugin-pwa + IndexedDB + Background Sync |
| Деплой | Railway (backend) + Vercel (frontend) |

---

## Схема БД (SQLite)

```sql
-- Вузли мережі
nodes (id TEXT PK, name TEXT, type TEXT, capacity_kg REAL, lat REAL, lon REAL, min_stock REAL)
-- type: factory | warehouse | store | rest_stop

-- Ребра графу
edges (from_id TEXT, to_id TEXT, distance_km REAL)

-- Вантажівки
trucks (id TEXT PK, type TEXT, capacity_kg REAL, fuel_per_100km REAL,
        driver_hourly REAL, avg_speed_kmh REAL,
        amortization_per_km REAL, maintenance_per_km REAL,
        depot_node_id TEXT)
-- type: semi | truck | van

-- Запаси на складах
warehouse_stock (warehouse_id TEXT, product_id TEXT, quantity_kg REAL)

-- Попит в магазинах
demand (node_id TEXT, product_id TEXT, current_stock REAL,
        min_stock REAL, requested_qty REAL, priority TEXT, is_urgent BOOL)
-- priority: NORMAL | ELEVATED | CRITICAL

-- Продукти
products (id TEXT PK, weight_kg REAL, length_cm REAL, width_cm REAL, height_cm REAL)

-- Маршрути (результат Solve)
routes (id INTEGER PK, truck_id TEXT, stops TEXT, -- JSON список зупинок
        total_km REAL, total_cost REAL, estimated_hours REAL, created_at TEXT)

-- Глобальні налаштування
settings (key TEXT PK, value TEXT)
-- fuel_price, max_detour_ratio, etc.
```

---

## API Endpoints

### Дані (Backend 2)

| Method | Path | Що робить |
|---|---|---|
| `POST` | `/api/upload` | Завантажити ZIP з CSV файлами |
| `POST` | `/api/generate` | Згенерувати рандомну мережу `{n_factories, n_warehouses, n_stores, n_trucks, seed}` |
| `GET` | `/api/network` | Повернути всі вузли + ребра для карти |
| `GET` | `/api/stock` | Залишки на всіх складах |
| `POST` | `/api/stock/update` | Оновити залишок після відвантаження `{warehouse_id, product_id, qty_shipped}` |
| `GET` | `/api/demand` | Поточний попит і пріоритети всіх магазинів |
| `POST` | `/api/demand/update` | Оновити попит вручну `{node_id, product_id, current_stock}` |
| `GET` | `/api/settings` | Глобальні налаштування (ціна пального і т.д.) |
| `PUT` | `/api/settings` | Оновити налаштування |

### Алгоритм (Backend 1 + Backend 2)

| Method | Path | Що робить |
|---|---|---|
| `POST` | `/api/solve` | Запустити повний VRP-solver → повернути маршрути |
| `POST` | `/api/urgent` | `{node_id, product_id, qty}` → точка стає CRITICAL → re-solve → diff |
| `GET` | `/api/routes` | Поточні маршрути всіх вантажівок |
| `GET` | `/api/routes/{truck_id}` | Маршрут конкретної вантажівки (для offline кешу) |

### Offline sync (Backend 2)

| Method | Path | Що робить |
|---|---|---|
| `POST` | `/api/actions/batch` | Прийняти чергу офлайн дій `[{action, payload, timestamp}]` |

---

## Структура проєкту

```
logiflow/
├── backend/
│   ├── main.py                  # FastAPI app, CORS, підключення роутерів
│   ├── database.py              # SQLite підключення (aiosqlite)
│   ├── models.py                # Pydantic схеми для всіх CSV і відповідей
│   ├── settings.py              # Глобальні дефолти (fuel_price, avg_speed і т.д.)
│   ├── api/
│   │   ├── upload.py            # POST /api/upload, /api/generate
│   │   ├── network.py           # GET /api/network, /api/stock, /api/demand
│   │   ├── solve.py             # POST /api/solve, /api/urgent
│   │   ├── routes.py            # GET /api/routes
│   │   └── sync.py              # POST /api/actions/batch
│   └── solver/
│       ├── graph.py             # Dijkstra з heapq, build_distance_matrix
│       ├── priority.py          # compute_priority → NORMAL/ELEVATED/CRITICAL
│       ├── weight.py            # chargeable_weight (dimensional weight)
│       ├── cost.py              # cost_per_km формула
│       ├── assignment.py        # assign_leg1, assign_leg2 (greedy + priority)
│       ├── routing.py           # greedy_tsp, two_opt
│       ├── enroute.py           # En-Route delivery (detour_ratio, savings)
│       └── generator.py        # generate_random_network
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Хедер з перемикачем Диспетчер/Склад
│   │   ├── api/                 # fetch функції для кожного endpoint
│   │   ├── pages/
│   │   │   ├── Dispatcher.tsx   # Вкладка диспетчера
│   │   │   └── Warehouse.tsx    # Вкладка складу
│   │   ├── components/
│   │   │   ├── NetworkMap.tsx   # react-leaflet карта
│   │   │   ├── RouteTable.tsx   # Таблиця маршрутів
│   │   │   ├── EnRoutePanel.tsx # Панель En-Route пропозицій
│   │   │   ├── UrgentButton.tsx # Кнопка urgent
│   │   │   ├── OrderCard.tsx    # Картка замовлення для складу
│   │   │   └── OfflineStatus.tsx # Індикатор зв'язку
│   │   └── hooks/
│   │       ├── useOfflineQueue.ts # IndexedDB черга дій
│   │       └── useSyncStatus.ts   # Стан зв'язку
│   └── vite.config.ts           # vite-plugin-pwa конфіг
└── demo_data/
    ├── nodes.csv
    ├── edges.csv
    ├── trucks.csv
    ├── demand.csv
    ├── warehouse_stock.csv
    └── products.csv
```

---

## Розподіл задач

### Backend 1 — Алгоритм (solver/)

**Пріоритет 1 (ядро):**
- [ ] `graph.py` — Dijkstra з heapq, string node IDs, build_distance_matrix
- [ ] `priority.py` — compute_priority по current_stock/min_stock
- [ ] `weight.py` — chargeable_weight = max(actual, volumetric)
- [ ] `cost.py` — cost_per_km з дефолтами
- [ ] `assignment.py` — assign_leg2: greedy по `cost/priority`, враховує warehouse_stock
- [ ] `routing.py` — greedy_tsp + 2-opt

**Пріоритет 2:**
- [ ] `assignment.py` — assign_leg1: factory → warehouse
- [ ] `enroute.py` — detour_ratio, savings, пропозиція для truck/van
- [ ] `generator.py` — generate_random_network(n_factories, n_warehouses, n_stores, n_trucks, seed)

### Backend 2 — API (api/ + database.py)

**Пріоритет 1:**
- [ ] `database.py` — SQLite schema, init, CRUD функції
- [ ] `models.py` — Pydantic для nodes, edges, trucks, demand, warehouse_stock, products
- [ ] `upload.py` — парсинг CSV → валідація → запис в БД
- [ ] `solve.py` — викликає solver, зберігає routes в БД, повертає JSON
- [ ] `network.py` — GET /api/network, /api/routes

**Пріоритет 2:**
- [ ] `upload.py` — POST /api/generate (викликає generator)
- [ ] `network.py` — /api/stock, /api/demand, /api/demand/update, /api/stock/update
- [ ] `solve.py` — POST /api/urgent (re-solve + diff)
- [ ] `sync.py` — POST /api/actions/batch

### Frontend 1 — Диспетчер (pages/Dispatcher.tsx)

**Пріоритет 1:**
- [ ] Базовий layout: хедер з перемикачем, дві вкладки
- [ ] Upload CSV форма або кнопка Generate з параметрами
- [ ] Кнопка Solve → показати маршрути в таблиці (RouteTable)

**Пріоритет 2:**
- [ ] NetworkMap — react-leaflet: вузли кольорами за пріоритетом, лінії маршрутів
- [ ] EnRoutePanel — картки пропозицій після Solve
- [ ] UrgentButton на кожній точці → запит → оновити карту

### Frontend 2 — Склад + Offline (pages/Warehouse.tsx + hooks/)

**Пріоритет 1:**
- [ ] OrderCard список замовлень: товар, кг, куди, вантажівка
- [ ] Кнопка "Відвантажено" → POST /api/stock/update
- [ ] Темна тема, великі кнопки (64px+)

**Пріоритет 2:**
- [ ] `useOfflineQueue` — зберігати дії в IndexedDB якщо offline
- [ ] `useSyncStatus` — детектувати online/offline, показувати індикатор
- [ ] vite-plugin-pwa конфіг — кешувати app shell + /api/routes/{truck_id}
- [ ] При reconnect — flush черги → оновити дані

---

## Порядок роботи (рекомендований)

```
Год 1-2:   Всі четверо — налаштування проєкту, структура папок, підключення БД
Год 2-6:   B1: Dijkstra + priority + assignment Leg2 + routing
           B2: upload CSV + /api/solve endpoint (заглушка) + /api/network
           F1: layout + upload форма + RouteTable (з моковими даними)
           F2: OrderCard + темна тема + layout складу
Год 6-10:  B1: En-Route + Leg1 + generator
           B2: /api/urgent + /api/stock/update + /api/generate
           F1: NetworkMap (карта) + EnRoutePanel
           F2: useOfflineQueue + useSyncStatus + PWA конфіг
Год 10-12: Інтеграція F↔B, тестування, демо датасет
Год 12+:   Деплой Railway + Vercel, README, скрінкаст
```

---

## Демо датасет (demo_data/)

Мережа "Аврора":
- 3 заводи (Київ, Харків, Львів)
- 5 складів (по регіонах)
- 20 магазинів (різні пріоритети: 4 CRITICAL, 8 ELEVATED, 8 NORMAL)
- 8 вантажівок (mix: 2 semi, 4 truck, 2 van)
- 3 товари (product_A, product_B, product_C)

Сценарій для скрінкасту:
1. Upload демо датасету
2. Натиснути Solve → маршрути на карті
3. En-Route пропозиція для van T3 (CRITICAL магазин по дорозі)
4. Urgent Request для STORE_7 → re-solve → diff в маршруті
5. Перейти на вкладку Склад → відвантажити замовлення
6. Відключити інтернет → натиснути Urgent → індикатор "в черзі"
7. Увімкнути інтернет → автоматична відправка
