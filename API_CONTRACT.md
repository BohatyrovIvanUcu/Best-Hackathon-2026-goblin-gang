# LogiFlow — API Contract

> **Для команди:** це фіксований контракт між Backend і Frontend.  
> Backend будує саме ці відповіді. Frontend очікує саме ці поля.  
> Якщо щось змінюється — оновити цей файл і повідомити всіх.

**Base URL (dev):** `http://localhost:8000`  
**Base URL (prod):** `https://logiflow-api.railway.app`  
**Content-Type:** `application/json` (крім upload — `multipart/form-data`)

---

## Загальні HTTP коди

| Код | Коли |
|---|---|
| `200` | Успіх |
| `400` | Невалідні дані (бізнес-логіка) |
| `404` | Ресурс не знайдено |
| `422` | Помилка валідації Pydantic (автоматично від FastAPI) |
| `500` | Внутрішня помилка сервера |

**Формат помилки (завжди):**
```json
{
  "detail": "Текст помилки"
}
```

---

## 1. POST /api/upload

Завантажити ZIP-архів з CSV файлами мережі.

### Request
`Content-Type: multipart/form-data`

| Поле | Тип | Обов'язково | Опис |
|---|---|---|---|
| `file` | File | ✅ | ZIP архів з CSV файлами |

ZIP має містити: `nodes.csv`, `edges.csv`, `trucks.csv`, `products.csv`, `warehouse_stock.csv`, `demand.csv`

### Response `200`
```json
{
  "status": "ok",
  "imported": {
    "nodes": 28,
    "edges": 74,
    "trucks": 8,
    "products": 3,
    "warehouse_stock_rows": 15,
    "demand_rows": 20
  },
  "warnings": []
}
```

### Response `400`
```json
{
  "detail": "Відсутній файл nodes.csv в архіві"
}
```

**Backend перевіряє:**
- ZIP не пошкоджений
- Всі 6 файлів присутні
- Обов'язкові колонки є в кожному CSV
- `from_id` і `to_id` в edges.csv є в nodes.csv
- `depot_node_id` у trucks.csv є в nodes.csv

---

## 2. POST /api/generate

Згенерувати випадкову мережу для тестування.

### Request
```json
{
  "n_factories": 3,
  "n_warehouses": 5,
  "n_stores": 20,
  "n_trucks": 8,
  "seed": 42
}
```

| Поле | Тип | Дефолт | Опис |
|---|---|---|---|
| `n_factories` | int | — | Кількість заводів (1–10) |
| `n_warehouses` | int | — | Кількість складів (1–20) |
| `n_stores` | int | — | Кількість магазинів (1–100) |
| `n_trucks` | int | — | Кількість вантажівок (1–50) |
| `seed` | int | `null` | Seed для відтворюваності (null = рандом) |

### Response `200`
```json
{
  "status": "ok",
  "generated": {
    "nodes": 28,
    "edges": 74,
    "trucks": 8,
    "products": 3,
    "warehouse_stock_rows": 15,
    "demand_rows": 20
  },
  "seed_used": 42
}
```

**Примітка:** після generate БД перезаписується новими даними.

---

## 3. GET /api/network

Повернути весь граф для відображення на карті.

### Request
Немає body. Немає параметрів.

### Response `200`
```json
{
  "nodes": [
    {
      "id": "FACTORY_1",
      "name": "Завод Київ",
      "type": "factory",
      "capacity_kg": 50000,
      "lat": 50.45,
      "lon": 30.52,
      "priority": null,
      "current_stock": null,
      "min_stock": null
    },
    {
      "id": "WAREHOUSE_1",
      "name": "Склад Центр",
      "type": "warehouse",
      "capacity_kg": 10000,
      "lat": 50.46,
      "lon": 30.53,
      "priority": null,
      "current_stock": null,
      "min_stock": null
    },
    {
      "id": "STORE_7",
      "name": "Аврора Позняки",
      "type": "store",
      "capacity_kg": 300,
      "lat": 50.39,
      "lon": 30.61,
      "priority": "CRITICAL",
      "current_stock": 15.0,
      "min_stock": 100.0
    }
  ],
  "edges": [
    {
      "from_id": "FACTORY_1",
      "to_id": "WAREHOUSE_1",
      "distance_km": 45.2
    },
    {
      "from_id": "WAREHOUSE_1",
      "to_id": "STORE_7",
      "distance_km": 12.1
    }
  ]
}
```

**Примітки для Frontend:**
- `priority` і `current_stock` — тільки для `type: "store"`
- Для factory/warehouse — `priority: null`, `current_stock: null`
- `priority` — агрегований пріоритет по всіх товарах (найгірший): `"NORMAL" | "ELEVATED" | "CRITICAL"`

---

## 4. GET /api/stock

Залишки товарів на всіх складах.

### Request
Немає body. Немає параметрів.

### Response `200`
```json
{
  "stock": [
    {
      "warehouse_id": "WAREHOUSE_1",
      "warehouse_name": "Склад Центр",
      "product_id": "product_A",
      "product_name": "Вода 0.5л кор",
      "quantity_kg": 2000.0,
      "reserved_kg": 85.0,
      "available_kg": 1915.0
    },
    {
      "warehouse_id": "WAREHOUSE_1",
      "warehouse_name": "Склад Центр",
      "product_id": "product_B",
      "product_name": "Гігієн. набір",
      "quantity_kg": 800.0,
      "reserved_kg": 0.0,
      "available_kg": 800.0
    }
  ]
}
```

**Примітки для Frontend:**
- `available_kg = quantity_kg - reserved_kg` (рахує backend, не frontend)
- Показувати червоним якщо `available_kg < 100`

---

## 5. POST /api/stock/update

Зафіксувати відвантаження товару зі складу (кнопка "Відвантажено" на вкладці Склад).

### Request
```json
{
  "warehouse_id": "WAREHOUSE_1",
  "product_id": "product_A",
  "qty_shipped_kg": 85.0
}
```

### Response `200`
```json
{
  "status": "ok",
  "warehouse_id": "WAREHOUSE_1",
  "product_id": "product_A",
  "quantity_kg_before": 2000.0,
  "quantity_kg_after": 1915.0,
  "reserved_kg_before": 85.0,
  "reserved_kg_after": 0.0
}
```

### Response `400`
```json
{
  "detail": "Недостатньо товару: доступно 50 кг, запрошено 85 кг"
}
```

**Backend перевіряє:**
- `qty_shipped_kg > 0`
- `warehouse_stock.quantity_kg >= qty_shipped_kg`
- Якщо `qty_shipped_kg > reserved_kg` — все одно виконує, але зменшує `reserved_kg` до 0

---

## 6. GET /api/demand

Поточний попит у всіх магазинах.

### Request
Немає body. Опціональний query-параметр: `?priority=CRITICAL` (фільтр).

### Response `200`
```json
{
  "demand": [
    {
      "node_id": "STORE_7",
      "node_name": "Аврора Позняки",
      "product_id": "product_A",
      "product_name": "Вода 0.5л кор",
      "current_stock": 15.0,
      "min_stock": 100.0,
      "requested_qty": 85.0,
      "priority": "CRITICAL",
      "is_urgent": false,
      "updated_at": "2026-04-03 10:15:00"
    },
    {
      "node_id": "STORE_3",
      "node_name": "Аврора Лівобережна",
      "product_id": "product_B",
      "product_name": "Гігієн. набір",
      "current_stock": 60.0,
      "min_stock": 100.0,
      "requested_qty": 40.0,
      "priority": "ELEVATED",
      "is_urgent": false,
      "updated_at": "2026-04-03 09:00:00"
    }
  ]
}
```

---

## 7. POST /api/demand/update

Оновити поточний запас у магазині вручну (диспетчер вводить дані).

### Request
```json
{
  "node_id": "STORE_7",
  "product_id": "product_A",
  "current_stock": 12.0
}
```

### Response `200`
```json
{
  "status": "ok",
  "node_id": "STORE_7",
  "product_id": "product_A",
  "current_stock": 12.0,
  "min_stock": 100.0,
  "requested_qty": 88.0,
  "priority": "CRITICAL",
  "priority_changed": true,
  "previous_priority": "ELEVATED"
}
```

**Backend робить:**
1. Оновлює `demand.current_stock`
2. Перераховує `requested_qty = min_stock - current_stock`
3. Перераховує `priority` по формулі `ratio = current_stock / min_stock`
4. Повертає `priority_changed: true` якщо пріоритет змінився

**Frontend після відповіді:**
- Якщо `priority_changed: true` — показати toast "Пріоритет STORE_7 змінився на CRITICAL"
- Оновити колір точки на карті

---

## 8. GET /api/settings

Глобальні налаштування системи.

### Request
Немає body.

### Response `200`
```json
{
  "settings": {
    "fuel_price": 52.0,
    "driver_hourly_default": 180.0,
    "avg_speed_default": 55.0,
    "amortization_default": 5.0,
    "maintenance_default": 3.0,
    "max_detour_ratio": 0.15,
    "min_priority_enroute": "ELEVATED"
  }
}
```

---

## 9. PUT /api/settings

Оновити одне або кілька налаштувань.

### Request
```json
{
  "fuel_price": 55.0,
  "max_detour_ratio": 0.20
}
```
*(Надсилати тільки ті поля які змінюються)*

### Response `200`
```json
{
  "status": "ok",
  "updated": {
    "fuel_price": 55.0,
    "max_detour_ratio": 0.20
  }
}
```

### Response `400`
```json
{
  "detail": "Невідомий ключ: fuel_prce. Допустимі: fuel_price, driver_hourly_default, ..."
}
```

---

## 10. POST /api/solve

Запустити повний VRP-солвер. Будує маршрути Leg1 і Leg2 для всіх вантажівок.

### Request
```json
{
  "departure_time": "08:00"
}
```

| Поле | Тип | Дефолт | Опис |
|---|---|---|---|
| `departure_time` | string | `"08:00"` | Час виїзду для всіх вантажівок (формат HH:MM) |

### Response `200`
```json
{
  "status": "ok",
  "solve_time_ms": 234,
  "routes": [
    {
      "id": 1,
      "truck_id": "T1",
      "truck_name": "Фура Харків",
      "truck_type": "semi",
      "leg": 1,
      "stops": ["FACTORY_1", "WAREHOUSE_1"],
      "stops_names": ["Завод Київ", "Склад Центр"],
      "total_km": 45.2,
      "total_cost": 9432.0,
      "departure_time": "08:00",
      "arrival_time": "08:49",
      "drive_hours": 0.8,
      "total_elapsed_h": 0.8,
      "days": 1,
      "time_status": "ok",
      "time_warning": null,
      "timeline": [
        {"time": "08:00", "event": "departure", "node_id": "FACTORY_1", "note": null},
        {"time": "08:49", "event": "arrival",   "node_id": "WAREHOUSE_1", "note": null}
      ],
      "cargo": [
        {
          "product_id": "product_A",
          "product_name": "Вода 0.5л кор",
          "qty_kg": 500.0
        }
      ]
    },
    {
      "id": 2,
      "truck_id": "T2",
      "truck_name": "Вантажівка-1",
      "truck_type": "truck",
      "leg": 2,
      "stops": ["WAREHOUSE_1", "STORE_7", "STORE_3", "WAREHOUSE_1"],
      "stops_names": ["Склад Центр", "Аврора Позняки", "Аврора Лівобережна", "Склад Центр"],
      "total_km": 87.4,
      "total_cost": 2248.5,
      "departure_time": "08:00",
      "arrival_time": "09:45",
      "drive_hours": 1.6,
      "total_elapsed_h": 1.75,
      "days": 1,
      "time_status": "ok",
      "time_warning": null,
      "timeline": [
        {"time": "08:00", "event": "departure", "node_id": "WAREHOUSE_1", "note": null},
        {"time": "08:22", "event": "arrival",   "node_id": "STORE_7",     "note": "CRITICAL  85 кг"},
        {"time": "08:37", "event": "departure", "node_id": "STORE_7",     "note": "15 хв розвантаження"},
        {"time": "09:00", "event": "arrival",   "node_id": "STORE_3",     "note": "ELEVATED  40 кг"},
        {"time": "09:15", "event": "departure", "node_id": "STORE_3",     "note": "15 хв розвантаження"},
        {"time": "09:45", "event": "return",    "node_id": "WAREHOUSE_1", "note": null}
      ],
      "cargo": [
        {
          "product_id": "product_A",
          "product_name": "Вода 0.5л кор",
          "qty_kg": 85.0,
          "delivery_node": "STORE_7"
        },
        {
          "product_id": "product_B",
          "product_name": "Гігієн. набір",
          "qty_kg": 40.0,
          "delivery_node": "STORE_3"
        }
      ]
    }
  ],
  "summary": {
    "total_routes": 5,
    "total_km": 312.8,
    "total_cost": 18420.5,
    "stores_covered": 18,
    "stores_total": 20,
    "stores_uncovered": ["STORE_15", "STORE_19"],
    "critical_covered": 4,
    "critical_total": 4
  },
  "enroute_suggestions": [
    {
      "truck_id": "T3",
      "store_id": "STORE_12",
      "store_name": "Аврора Троєщина",
      "priority": "CRITICAL",
      "detour_km": 3.2,
      "detour_ratio": 0.08,
      "savings_vs_separate": 1240.0
    }
  ]
}
```

**Примітки для Frontend:**
- `stores_uncovered` — магазини яким не вистачило вантажівок → показати попередження
- `enroute_suggestions` — пропозиції En-Route → показати в `EnRoutePanel`
- `estimated_hours = total_km / avg_speed_default` (з settings)

---

## 11. POST /api/urgent

Позначити точку як URGENT → перебудувати маршрути для affected вантажівок.

### Request
```json
{
  "node_id": "STORE_7",
  "product_id": "product_A",
  "requested_qty": 85.0
}
```

| Поле | Тип | Обов'язково | Опис |
|---|---|---|---|
| `node_id` | string | ✅ | ID магазину |
| `product_id` | string | ✅ | ID товару |
| `requested_qty` | float | ✅ | Скільки треба привезти (кг) |

### Response `200`
```json
{
  "status": "ok",
  "node_id": "STORE_7",
  "new_priority": "CRITICAL",
  "affected_trucks": ["T2", "T3"],
  "routes_before": [
    {
      "truck_id": "T2",
      "stops": ["WAREHOUSE_1", "STORE_3", "WAREHOUSE_1"],
      "total_km": 54.8,
      "total_cost": 1410.0
    }
  ],
  "routes_after": [
    {
      "truck_id": "T2",
      "stops": ["WAREHOUSE_1", "STORE_7", "STORE_3", "WAREHOUSE_1"],
      "total_km": 87.4,
      "total_cost": 2248.5
    }
  ],
  "diff": {
    "T2": {
      "added_stops": ["STORE_7"],
      "removed_stops": [],
      "km_delta": 32.6,
      "cost_delta": 838.5
    }
  }
}
```

**Frontend після відповіді:**
- Показати diff маршрутів (було → стало)
- Перефарбувати STORE_7 в червоний на карті
- Якщо `affected_trucks` порожній → показати "Вільних вантажівок немає"

---

## 12. GET /api/routes

Всі поточні активні маршрути.

### Request
Немає body. Опціональний query-параметр: `?leg=1` або `?leg=2` (фільтр).

### Response `200`
```json
{
  "routes": [
    {
      "id": 1,
      "truck_id": "T1",
      "truck_name": "Фура Харків",
      "truck_type": "semi",
      "leg": 1,
      "stops": ["FACTORY_1", "WAREHOUSE_1"],
      "stops_names": ["Завод Київ", "Склад Центр"],
      "total_km": 45.2,
      "total_cost": 9432.0,
      "estimated_hours": 0.82,
      "created_at": "2026-04-03 10:00:00",
      "is_active": true
    },
    {
      "id": 2,
      "truck_id": "T2",
      "truck_name": "Вантажівка-1",
      "truck_type": "truck",
      "leg": 2,
      "stops": ["WAREHOUSE_1", "STORE_7", "STORE_3", "WAREHOUSE_1"],
      "stops_names": ["Склад Центр", "Аврора Позняки", "Аврора Лівобережна", "Склад Центр"],
      "total_km": 87.4,
      "total_cost": 2248.5,
      "estimated_hours": 1.59,
      "created_at": "2026-04-03 10:00:00",
      "is_active": true
    }
  ]
}
```

**Примітка:** повертає тільки `is_active = true` маршрути.

---

## 13. GET /api/routes/{truck_id}

Маршрут конкретної вантажівки (використовується для offline кешу на вкладці Склад).

### Request
Немає body. `truck_id` — в URL.

**Приклад:** `GET /api/routes/T2`

### Response `200`
```json
{
  "truck_id": "T2",
  "truck_name": "Вантажівка-1",
  "truck_type": "truck",
  "depot_node_id": "WAREHOUSE_1",
  "route": {
    "id": 2,
    "leg": 2,
    "stops": ["WAREHOUSE_1", "STORE_7", "STORE_3", "WAREHOUSE_1"],
    "stops_details": [
      {
        "node_id": "WAREHOUSE_1",
        "node_name": "Склад Центр",
        "type": "warehouse",
        "lat": 50.46,
        "lon": 30.53,
        "action": "departure",
        "scheduled_time": "08:00",
        "cargo_to_load": [
          {"product_id": "product_A", "product_name": "Вода 0.5л кор", "qty_kg": 85.0, "for_store": "STORE_7"},
          {"product_id": "product_B", "product_name": "Гігієн. набір", "qty_kg": 40.0, "for_store": "STORE_3"}
        ]
      },
      {
        "node_id": "STORE_7",
        "node_name": "Аврора Позняки",
        "type": "store",
        "lat": 50.39,
        "lon": 30.61,
        "action": "delivery",
        "scheduled_time": "08:22",
        "priority": "CRITICAL",
        "cargo_to_unload": [
          {"product_id": "product_A", "product_name": "Вода 0.5л кор", "qty_kg": 85.0}
        ]
      },
      {
        "node_id": "STORE_3",
        "node_name": "Аврора Лівобережна",
        "type": "store",
        "lat": 50.47,
        "lon": 30.65,
        "action": "delivery",
        "scheduled_time": "09:00",
        "priority": "ELEVATED",
        "cargo_to_unload": [
          {"product_id": "product_B", "product_name": "Гігієн. набір", "qty_kg": 40.0}
        ]
      },
      {
        "node_id": "WAREHOUSE_1",
        "node_name": "Склад Центр",
        "type": "warehouse",
        "lat": 50.46,
        "lon": 30.53,
        "action": "return",
        "scheduled_time": "09:45",
        "cargo_to_load": []
      }
    ],
    "total_km": 87.4,
    "total_cost": 2248.5,
    "departure_time": "08:00",
    "arrival_time": "09:45",
    "drive_hours": 1.6,
    "total_elapsed_h": 1.75,
    "days": 1,
    "time_status": "ok",
    "time_warning": null,
    "created_at": "2026-04-03 10:00:00"
  }
}
```

### Response `404`
```json
{
  "detail": "Вантажівку T99 не знайдено або маршрут ще не побудовано"
}
```

**Примітка:** цей endpoint кешується PWA для offline-режиму складу.

---

## 14. POST /api/actions/batch

Прийняти чергу дій зібраних в offline-режимі (IndexedDB → Background Sync).

### Request
```json
{
  "actions": [
    {
      "action": "stock_update",
      "payload": {
        "warehouse_id": "WAREHOUSE_1",
        "product_id": "product_A",
        "qty_shipped_kg": 85.0
      },
      "timestamp": "2026-04-03 11:30:00",
      "client_id": "warehouse-tablet-01"
    },
    {
      "action": "urgent",
      "payload": {
        "node_id": "STORE_7",
        "product_id": "product_A",
        "requested_qty": 85.0
      },
      "timestamp": "2026-04-03 11:31:00",
      "client_id": "warehouse-tablet-01"
    }
  ]
}
```

| `action` | Відповідний endpoint |
|---|---|
| `stock_update` | POST /api/stock/update |
| `urgent` | POST /api/urgent |
| `demand_update` | POST /api/demand/update |

### Response `200`
```json
{
  "status": "ok",
  "processed": 2,
  "failed": 0,
  "results": [
    {
      "action": "stock_update",
      "timestamp": "2026-04-03 11:30:00",
      "status": "ok"
    },
    {
      "action": "urgent",
      "timestamp": "2026-04-03 11:31:00",
      "status": "ok"
    }
  ]
}
```

### Response (часткова помилка)
```json
{
  "status": "partial",
  "processed": 1,
  "failed": 1,
  "results": [
    {
      "action": "stock_update",
      "timestamp": "2026-04-03 11:30:00",
      "status": "ok"
    },
    {
      "action": "urgent",
      "timestamp": "2026-04-03 11:31:00",
      "status": "error",
      "error": "Вузол STORE_99 не знайдено"
    }
  ]
}
```

**Примітки:**
- Backend обробляє дії **по черзі** в порядку `timestamp`
- Якщо одна дія провалилась — продовжує наступні (не rollback всього batch)
- HTTP код завжди `200` (навіть якщо `status: "partial"`) — щоб PWA не ретраяло успішні дії
- `client_id` — для дедуплікації (якщо batch надіслали двічі)

---

## Типи TypeScript (для Frontend)

```typescript
// Базові типи
type NodeType = "factory" | "warehouse" | "store"
type TruckType = "semi" | "truck" | "van"
type Priority = "NORMAL" | "ELEVATED" | "CRITICAL"

// GET /api/network
interface NetworkNode {
  id: string
  name: string
  type: NodeType
  capacity_kg: number
  lat: number
  lon: number
  priority: Priority | null
  current_stock: number | null
  min_stock: number | null
}

interface NetworkEdge {
  from_id: string
  to_id: string
  distance_km: number
}

interface NetworkResponse {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
}

// POST /api/solve
interface RouteStop {
  node_id: string
  node_name: string
  type: NodeType
  lat: number
  lon: number
  action: "departure" | "delivery" | "return"
  priority?: Priority
  cargo_to_load?: CargoItem[]
  cargo_to_unload?: CargoItem[]
}

interface CargoItem {
  product_id: string
  product_name: string
  qty_kg: number
  for_store?: string
  delivery_node?: string
}

interface Route {
  id: number
  truck_id: string
  truck_name: string
  truck_type: TruckType
  leg: 1 | 2
  stops: string[]
  stops_names: string[]
  total_km: number
  total_cost: number
  estimated_hours: number
  created_at: string
  is_active: boolean
}

interface EnRouteSuggestion {
  truck_id: string
  store_id: string
  store_name: string
  priority: Priority
  detour_km: number
  detour_ratio: number
  savings_vs_separate: number
}

interface SolveResponse {
  status: "ok"
  solve_time_ms: number
  routes: Route[]
  summary: {
    total_routes: number
    total_km: number
    total_cost: number
    stores_covered: number
    stores_total: number
    stores_uncovered: string[]
    critical_covered: number
    critical_total: number
  }
  enroute_suggestions: EnRouteSuggestion[]
}
```
