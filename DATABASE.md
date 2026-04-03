# LogiFlow — Схема бази даних

## Загальні правила
- БД: SQLite (файл `logiflow.db` на сервері)
- Всі ID — рядки (TEXT), не числа. Наприклад: `FACTORY_1`, `WAREHOUSE_2`, `STORE_7`, `T3`
- Всі ваги в **кілограмах**, відстані в **кілометрах**, гроші в **гривнях**

---

## Таблиця 1: `nodes` — Всі вузли мережі

Зберігає заводи, склади, магазини і точки відпочинку.

```sql
CREATE TABLE nodes (
    id          TEXT PRIMARY KEY,   -- унікальний ID: FACTORY_1, WAREHOUSE_2, STORE_7
    name        TEXT NOT NULL,      -- назва: "Завод Київ", "Склад Центр", "Аврора Позняки"
    type        TEXT NOT NULL,      -- тип: factory | warehouse | store
    capacity_kg REAL DEFAULT 0,     -- місткість вузла в кг (для складів і магазинів)
    lat         REAL,               -- широта (для карти)
    lon         REAL                -- довгота (для карти)
);
```

**Приклад даних:**
```
id           | name              | type      | capacity_kg | lat    | lon
FACTORY_1    | Завод Київ        | factory   | 50000       | 50.45  | 30.52
WAREHOUSE_1  | Склад Центр       | warehouse | 10000       | 50.46  | 30.53
STORE_7      | Аврора Позняки    | store     | 300         | 50.39  | 30.61
```

---

## Таблиця 2: `edges` — Ребра графу (відстані між вузлами)

Зберігає відстані між парами вузлів. Граф **ненапрямлений** — якщо є ребро A→B, то є й B→A.

```sql
CREATE TABLE edges (
    from_id     TEXT NOT NULL,      -- звідки: FACTORY_1
    to_id       TEXT NOT NULL,      -- куди: WAREHOUSE_1
    distance_km REAL NOT NULL,      -- відстань в км: 45.2
    PRIMARY KEY (from_id, to_id)
);
```

**Приклад даних:**
```
from_id      | to_id       | distance_km
FACTORY_1    | WAREHOUSE_1 | 45.2
WAREHOUSE_1  | STORE_7     | 12.1
WAREHOUSE_2  | STORE_7     | 18.5
WAREHOUSE_1  | WAREHOUSE_2 | 34.0
```

**Важливо:** ребро FACTORY → STORE не існує (бізнес-правило: фура не їде напряму в магазин).

---

## Таблиця 3: `products` — Товари

Зберігає характеристики кожного товару для розрахунку об'ємної ваги.

```sql
CREATE TABLE products (
    id              TEXT PRIMARY KEY,  -- product_A, product_B, product_C
    name            TEXT NOT NULL,     -- "Вода питна 0.5л", "Батарейки AA"
    weight_kg       REAL NOT NULL,     -- вага одиниці в кг
    length_cm       REAL DEFAULT NULL, -- довжина упаковки в см (опціонально)
    width_cm        REAL DEFAULT NULL, -- ширина в см (опціонально)
    height_cm       REAL DEFAULT NULL  -- висота в см (опціонально)
);
```

**Приклад даних:**
```
id        | name          | weight_kg | length_cm | width_cm | height_cm
product_A | Вода 0.5л кор | 8.0       | 60        | 40       | 50
product_B | Гігієн. набір | 2.5       | 30        | 20       | 15
product_C | Батарейки AA  | 1.0       | NULL      | NULL     | NULL
```

Якщо `length_cm/width_cm/height_cm` = NULL → система використовує тільки `weight_kg`.
Якщо вказані → розраховує `chargeable_weight = max(weight_kg, L×W×H/4000)`.

---

## Таблиця 4: `trucks` — Вантажівки

Зберігає флот і параметри для розрахунку вартості рейсу.

```sql
CREATE TABLE trucks (
    id                  TEXT PRIMARY KEY, -- T1, T2, T3
    name                TEXT NOT NULL,    -- "Фура Київ-1", "Бус Склад Центр"
    type                TEXT NOT NULL,    -- semi | truck | van
    capacity_kg         REAL NOT NULL,    -- максимальна місткість в кг
    fuel_per_100km      REAL NOT NULL,    -- витрата пального л/100км
    depot_node_id       TEXT NOT NULL,    -- де базується: WAREHOUSE_1, FACTORY_1
    -- розширені параметри (є дефолти в settings)
    driver_hourly       REAL DEFAULT NULL, -- ставка водія грн/год (NULL = дефолт)
    avg_speed_kmh       REAL DEFAULT NULL, -- середня швидкість км/год (NULL = дефолт)
    amortization_per_km REAL DEFAULT NULL, -- амортизація грн/км (NULL = дефолт)
    maintenance_per_km  REAL DEFAULT NULL  -- обслуговування грн/км (NULL = дефолт)
);
```

**Приклад даних:**
```
id | name          | type  | capacity_kg | fuel/100km | depot_node_id | driver_hourly
T1 | Фура Харків   | semi  | 20000       | 35.0       | FACTORY_1     | NULL
T2 | Вантажівка-1  | truck | 5000        | 28.0       | WAREHOUSE_1   | NULL
T3 | Бус Центр     | van   | 1500        | 12.0       | WAREHOUSE_1   | 200.0
```

`type` визначає чи може вантажівка робити En-Route зупинки:
- `semi` → тільки Leg1 (завод→склад), без заїздів у магазини
- `truck`, `van` → Leg2 (склад→магазини), можливі En-Route

---

## Таблиця 5: `warehouse_stock` — Запаси на складах

Зберігає скільки якого товару є на кожному складі прямо зараз.

```sql
CREATE TABLE warehouse_stock (
    warehouse_id  TEXT NOT NULL,     -- WAREHOUSE_1
    product_id    TEXT NOT NULL,     -- product_A
    quantity_kg   REAL NOT NULL,     -- 2000.0 (скільки є зараз)
    reserved_kg   REAL DEFAULT 0,   -- зарезервовано для активних маршрутів
    PRIMARY KEY (warehouse_id, product_id)
);
```

**Приклад даних:**
```
warehouse_id | product_id | quantity_kg | reserved_kg
WAREHOUSE_1  | product_A  | 2000        | 85    ← 85 кг зарезервовано для STORE_7
WAREHOUSE_1  | product_B  | 800         | 0
WAREHOUSE_2  | product_A  | 50          | 0
WAREHOUSE_2  | product_C  | 1200        | 0
```

**Доступно для нових замовлень:** `quantity_kg - reserved_kg`

---

## Таблиця 6: `demand` — Попит магазинів

Зберігає поточний стан попиту в кожному магазині по кожному товару.

```sql
CREATE TABLE demand (
    node_id       TEXT NOT NULL,    -- STORE_7
    product_id    TEXT NOT NULL,    -- product_A
    current_stock REAL NOT NULL,    -- 15.0 (є зараз)
    min_stock     REAL NOT NULL,    -- 100.0 (мінімально допустимий рівень)
    requested_qty REAL NOT NULL,    -- 85.0 (скільки треба привезти)
    priority      TEXT NOT NULL,    -- NORMAL | ELEVATED | CRITICAL
    is_urgent     INTEGER DEFAULT 0, -- 0 або 1 (SQLite не має boolean; Backend конвертує в true/false для API)
    updated_at    TEXT NOT NULL,    -- "2026-04-03 10:15:00"
    PRIMARY KEY (node_id, product_id)
);
```

**Приклад даних:**
```
node_id  | product_id | current | min   | requested | priority | is_urgent
STORE_7  | product_A  | 15.0    | 100.0 | 85.0      | CRITICAL | 0
STORE_3  | product_B  | 60.0    | 100.0 | 40.0      | ELEVATED | 0
STORE_1  | product_A  | 80.0    | 100.0 | 20.0      | NORMAL   | 0
STORE_5  | product_C  | 5.0     | 50.0  | 45.0      | CRITICAL | 1  ← urgent
```

**Пріоритет рахується автоматично при зміні `current_stock`:**
```
ratio = current_stock / min_stock
ratio < 0.2  → CRITICAL
ratio < 0.5  → ELEVATED
ratio >= 0.5 → NORMAL
is_urgent=1  → завжди CRITICAL
```

---

## Таблиця 7: `routes` — Маршрути (результат Solve)

Зберігає побудовані маршрути після натискання Solve.

```sql
CREATE TABLE routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    truck_id        TEXT NOT NULL,     -- T2
    leg             INTEGER NOT NULL,  -- 1 або 2
    stops           TEXT NOT NULL,     -- JSON: ["WAREHOUSE_1","STORE_7","STORE_3","WAREHOUSE_1"]
    total_km        REAL NOT NULL,     -- 87.4
    total_cost      REAL NOT NULL,     -- 2248.5 (грн)
    drive_hours     REAL NOT NULL,     -- 1.6 (чистий час за кермом)
    total_elapsed_h REAL NOT NULL,     -- 1.75 (з перервами і розвантаженням)
    days            INTEGER DEFAULT 1, -- 1, 2 або 3 (кількість днів рейсу)
    departure_time  TEXT NOT NULL,     -- "08:00" (час виїзду)
    arrival_time    TEXT NOT NULL,     -- "09:45" (час повернення)
    time_status     TEXT NOT NULL,     -- "ok" | "warning" | "multiday"
    time_warning    TEXT DEFAULT NULL, -- null або текст попередження для диспетчера
    timeline        TEXT NOT NULL,     -- JSON: масив подій з часами [{time, event, node_id, note}]
    created_at      TEXT NOT NULL,     -- "2026-04-03 10:00:00"
    is_active       INTEGER DEFAULT 1  -- 1 = поточний, 0 = замінений після urgent
);
```

**Приклад даних:**
```
id | truck_id | leg | stops                                           | km   | cost    | drive_h | elapsed_h | days | depart | arrive | status
1  | T1       | 1   | ["FACTORY_1","WAREHOUSE_1"]                     | 45.2 | 9432.0  | 0.8     | 0.8       | 1    | 08:00  | 08:49  | ok
2  | T2       | 2   | ["WAREHOUSE_1","STORE_7","STORE_3","WAREHOUSE_1"]| 87.4 | 2248.5  | 1.6     | 1.75      | 1    | 08:00  | 09:45  | ok
3  | T3       | 2   | ["WAREHOUSE_1","STORE_1","WAREHOUSE_1"]          | 43.0 | 610.0   | 0.8     | 0.9       | 1    | 08:00  | 08:54  | ok
```

**`timeline` зберігається як JSON рядок:**
```json
[
  {"time": "08:00", "event": "departure", "node_id": "WAREHOUSE_1", "note": null},
  {"time": "08:22", "event": "arrival",   "node_id": "STORE_7",     "note": "CRITICAL  85 кг"},
  {"time": "08:37", "event": "departure", "node_id": "STORE_7",     "note": "15 хв розвантаження"},
  {"time": "09:00", "event": "arrival",   "node_id": "STORE_3",     "note": "ELEVATED  40 кг"},
  {"time": "09:15", "event": "departure", "node_id": "STORE_3",     "note": "15 хв розвантаження"},
  {"time": "09:45", "event": "return",    "node_id": "WAREHOUSE_1", "note": null}
]
```

**`event` може бути:** `departure` | `arrival` | `return` | `break`

---

## Таблиця 8: `settings` — Глобальні налаштування

Зберігає параметри які впливають на всі розрахунки.

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**Дефолтні значення при ініціалізації:**
```
key                    | value    | пояснення
fuel_price             | 52.0     | ціна пального грн/л
driver_hourly_default  | 180.0    | дефолтна ставка водія грн/год
avg_speed_default      | 55.0     | дефолтна швидкість км/год
amortization_default   | 5.0      | дефолтна амортизація грн/км
maintenance_default    | 3.0      | дефолтне обслуговування грн/км
max_detour_ratio       | 0.15     | ліміт відхилення для En-Route (15%)
min_priority_enroute   | ELEVATED | мінімальний пріоритет для En-Route
unload_min_default     | 15       | хвилин на розвантаження на кожній зупинці
departure_time_default | 08:00    | час виїзду за замовчуванням
```

---

## Таблиця 9: `route_cargo` — Вантаж маршруту

Зберігає що саме везе кожна вантажівка і куди. Потрібна для вкладки Склад (список що завантажити) і для перевірки резервування.

```sql
CREATE TABLE route_cargo (
    route_id     INTEGER NOT NULL,  -- → routes.id
    stop_node_id TEXT NOT NULL,     -- STORE_7 (куди доставити)
    product_id   TEXT NOT NULL,     -- product_A
    qty_kg       REAL NOT NULL,     -- 85.0
    PRIMARY KEY (route_id, stop_node_id, product_id)
);
```

**Приклад даних** (для маршруту id=2, T2):
```
route_id | stop_node_id | product_id | qty_kg
2        | STORE_7      | product_A  | 85.0
2        | STORE_3      | product_B  | 40.0
```

**Навіщо окрема таблиця а не JSON в routes:**
- Вкладка Склад запитує "що завантажити на T2" одним SQL запитом
- При re-solve легко видалити старий cargo і записати новий
- Дозволяє перевірити що `reserved_kg` у `warehouse_stock` відповідає сумі cargo

---

## Зв'язки між таблицями

```
nodes ──────────────────── edges (from_id, to_id → nodes.id)
  │
  ├── warehouse_stock (warehouse_id → nodes.id де type=warehouse)
  │       └── products (product_id → products.id)
  │
  ├── demand (node_id → nodes.id де type=store)
  │       └── products (product_id → products.id)
  │
  └── trucks (depot_node_id → nodes.id)
          └── routes (truck_id → trucks.id)
                  └── route_cargo (route_id → routes.id)
                          └── products (product_id → products.id)
```

---

## Що відбувається при Solve

```
1. Читаємо: nodes, edges, trucks, warehouse_stock, demand, settings
2. Алгоритм будує маршрути з повним timeline
3. Старі routes: is_active = 0
4. Записуємо: routes (нові записи з timeline, departure_time, arrival_time і т.д.)
5. Записуємо: route_cargo (що везе кожна вантажівка і куди)
6. Оновлюємо: warehouse_stock.reserved_kg += qty для кожного замовлення
```

## Що відбувається при Urgent

```
1. demand: is_urgent = 1, priority = CRITICAL для вказаного node_id
2. Re-solve тільки для affected вантажівок
3. routes: старі affected = is_active=0, нові записи
4. route_cargo: видалити старий cargo affected маршрутів, записати новий
5. warehouse_stock: перерахувати reserved_kg
```

## Що відбувається при "Відвантажено" (склад)

```
1. warehouse_stock: quantity_kg -= shipped_qty, reserved_kg -= shipped_qty
2. routes: оновити статус зупинки (опціонально)
```
