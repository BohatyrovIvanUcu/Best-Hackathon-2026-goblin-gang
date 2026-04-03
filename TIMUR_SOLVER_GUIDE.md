# Timur Solver Guide

Цей файл пояснює, що саме вже є в проєкті, які таблиці реально беруть участь в обрахунку маршрутів, які формули потрібні для solver-а і в якому порядку все рахувати.

Документ зібраний на основі:
- `CONCEPT.md`
- `DATABASE.md`
- `TASKS.md`
- `demo_data/*.csv`
- `optimal-delivery/*` як референсу по алгоритмах

## 1. Що вже є в проєкті

### Концепція продукту

У `CONCEPT.md` зафіксовано головну логіку:

- доставка має 2 окремі плечі:
  - `Leg 1`: `FACTORY -> WAREHOUSE`
  - `Leg 2`: `WAREHOUSE -> STORE`
- вантажівка `semi` не їде напряму в магазин
- пріоритети попиту:
  - `NORMAL = 1.0`
  - `ELEVATED = 2.0`
  - `CRITICAL = 4.0`
- алгоритмічне ядро MVP:
  - Dijkstra
  - greedy assignment
  - nearest-neighbor TSP
  - 2-opt
  - en-route insertions для `truck` і `van`

### Що реально є в demo_data

Поточний демо-набір уже добре підходить для solver-а:

- `nodes.csv`: 28 вузлів
  - 3 `factory`
  - 5 `warehouse`
  - 20 `store`
- `edges.csv`: 71 ребро
- `trucks.csv`: 8 машин
  - 2 `semi`
  - 4 `truck`
  - 2 `van`
- `demand.csv`: 33 записи попиту
  - 5 `CRITICAL`
  - 12 `ELEVATED`
  - 16 `NORMAL`
- `warehouse_stock.csv`: 15 записів запасів
- `routes.csv` і `route_cargo.csv`: поки що порожні, це цільовий output solver-а

### Важливе спостереження по графу

У `DATABASE.md` граф описаний як ненапрямлений, але в `demo_data/edges.csv` кожне ребро записане лише один раз.

Приклад:

```csv
FACTORY_1,WAREHOUSE_1,8.0
```

Зворотного рядка

```csv
WAREHOUSE_1,FACTORY_1,8.0
```

немає.

Отже в loader-і треба обов'язково робити симетризацію:

```python
graph[a][b] = km
graph[b][a] = km
```

Інакше shortest path буде порахований як для напрямленого графа, що тут неправильно.

## 2. Які таблиці реально потрібні Тимуру

### Вхідні таблиці solver-а

#### `nodes`

Потрібні поля:
- `id`
- `type`
- `capacity_kg`
- `lat`, `lon` тільки якщо потім треба карта

Використання:
- визначити, що є заводом, складом, магазином
- перевіряти допустимі типи вузлів для кожного плеча

#### `edges`

Потрібні поля:
- `from_id`
- `to_id`
- `distance_km`

Використання:
- побудова графа
- Dijkstra
- матриця коротких відстаней

#### `trucks`

Потрібні поля:
- `id`
- `type`
- `capacity_kg`
- `fuel_per_100km`
- `depot_node_id`
- `driver_hourly`
- `avg_speed_kmh`
- `amortization_per_km`
- `maintenance_per_km`

Використання:
- фільтр по плечу:
  - `semi` -> `Leg 1`
  - `truck`, `van` -> `Leg 2`
- перевірка місткості
- розрахунок собівартості маршруту

#### `demand`

Потрібні поля:
- `node_id`
- `product_id`
- `current_stock`
- `min_stock`
- `requested_qty`
- `priority`
- `is_urgent`
- `updated_at`

Використання:
- побудова списку замовлень
- перерахунок пріоритету
- сортування замовлень по критичності

#### `warehouse_stock`

Потрібні поля:
- `warehouse_id`
- `product_id`
- `quantity_kg`
- `reserved_kg`

Використання:
- перевірка, чи склад може закрити попит
- резервування після побудови маршруту

#### `products`

Потрібні поля:
- `id`
- `weight_kg`
- `length_cm`
- `width_cm`
- `height_cm`

Використання:
- базово: можна використати тільки як довідник товарів
- розширення: dimensional / chargeable weight

#### `settings`

Потрібні поля:
- `fuel_price`
- `driver_hourly_default`
- `avg_speed_default`
- `amortization_default`
- `maintenance_default`
- `max_detour_ratio`
- `min_priority_enroute`
- `unload_min_default`
- `departure_time_default`

Використання:
- всі глобальні формули часу, вартості і en-route фільтрів

### Вихідні таблиці solver-а

#### `routes`

Треба записати:
- `truck_id`
- `leg`
- `stops`
- `total_km`
- `total_cost`
- `drive_hours`
- `total_elapsed_h`
- `days`
- `departure_time`
- `arrival_time`
- `time_status`
- `time_warning`
- `timeline`
- `created_at`
- `is_active`

#### `route_cargo`

Для кожної точки доставки:
- `route_id`
- `stop_node_id`
- `product_id`
- `qty_kg`

#### `warehouse_stock`

Після solve:

```text
reserved_kg += qty_kg, яке пішло в активні маршрути
```

## 3. Поточна топологія demo_data

У поточному демо датасеті логіка мережі така:

- кожен завод має зв'язки зі складами
- склади мають зв'язки між собою
- кожен склад має свою групу магазинів
- всередині кожної групи є store-to-store ребра

Фактично видно 5 регіональних кластерів:

- `WAREHOUSE_1` обслуговує `STORE_1..STORE_4`
- `WAREHOUSE_2` обслуговує `STORE_5..STORE_8`
- `WAREHOUSE_3` обслуговує `STORE_9..STORE_12`
- `WAREHOUSE_4` обслуговує `STORE_13..STORE_16`
- `WAREHOUSE_5` обслуговує `STORE_17..STORE_20`

Це означає:

- `Leg 2` можна добре вирішувати по складах/депо
- Dijkstra все одно потрібен, бо між магазинами теж є ребра і shortest path не завжди дорівнює прямому ребру зі складу

## 4. Формули, які треба реалізувати

### 4.1. Пріоритет попиту

Навіть якщо `priority` уже є в CSV, краще його перераховувати в solver-і, щоб не залежати від застарілих значень.

```python
if is_urgent == 1:
    priority = "CRITICAL"
else:
    ratio = current_stock / min_stock
    if ratio < 0.2:
        priority = "CRITICAL"
    elif ratio < 0.5:
        priority = "ELEVATED"
    else:
        priority = "NORMAL"
```

Вага пріоритету:

```python
priority_weight = {
    "NORMAL": 1.0,
    "ELEVATED": 2.0,
    "CRITICAL": 4.0,
}
```

### 4.2. Доступний запас на складі

```python
available_kg = quantity_kg - reserved_kg
```

Склад є валідним кандидатом для замовлення, якщо:

```python
available_kg >= requested_qty
```

Якщо такого складу немає:

1. або дозволяємо split між кількома складами
2. або формуємо дефіцит для `Leg 1`
3. або повертаємо alert диспетчеру

Для MVP можна стартувати з простого правила:

```text
немає складу з достатнім запасом -> замовлення в unassigned / shortage
```

### 4.3. Вага вантажу для перевірки місткості

У поточних CSV і `warehouse_stock.quantity_kg`, і `demand.requested_qty` уже задані в кілограмах. Тому для MVP можна брати:

```python
load_kg = requested_qty
```

Розширення на майбутнє:

```python
chargeable_unit_weight = max(weight_kg, length_cm * width_cm * height_cm / 4000)
```

Але це має сенс тільки якщо ви точно домовитесь, як перевести `requested_qty` у кількість упаковок. У поточній схемі для першої версії безпечніше тримати capacity-check саме в кілограмах.

### 4.4. Собівартість 1 км для конкретної машини

Брати з `trucks`, а якщо там `NULL`, тоді fallback на `settings`.

```python
driver_hourly = truck.driver_hourly or settings.driver_hourly_default
avg_speed_kmh = truck.avg_speed_kmh or settings.avg_speed_default
amortization_per_km = truck.amortization_per_km or settings.amortization_default
maintenance_per_km = truck.maintenance_per_km or settings.maintenance_default
fuel_price = settings.fuel_price
```

Формули:

```python
fuel_cost_per_km = truck.fuel_per_100km * fuel_price / 100
driver_cost_per_km = driver_hourly / avg_speed_kmh
cost_per_km = (
    fuel_cost_per_km
    + driver_cost_per_km
    + amortization_per_km
    + maintenance_per_km
)
```

Приклади з поточних CSV:

- для `T3` (`truck`, 22 л/100 км):

```text
fuel = 22 * 52 / 100 = 11.44
driver = 180 / 55 = 3.27
amortization = 5.00
maintenance = 3.00
cost_per_km = 22.71 грн/км
```

- для `T7` (`van`, 12 л/100 км, driver_hourly=200):

```text
fuel = 12 * 52 / 100 = 6.24
driver = 200 / 55 = 3.64
amortization = 5.00
maintenance = 3.00
cost_per_km = 17.88 грн/км
```

### 4.5. Score для greedy assignment

Основна ідея з `CONCEPT.md`:

```python
score = marginal_km * cost_per_km / priority_weight
```

Де:

- `marginal_km` = додатковий кілометраж, який отримає машина, якщо взяти це замовлення
- `cost_per_km` = індивідуальна собівартість машини
- `priority_weight` = 1 / 2 / 4

Чим менший `score`, тим вигідніше призначати замовлення цій машині.

Для першої версії можна рахувати `marginal_km` так:

- якщо в машини ще немає жодної точки:

```python
marginal_km = dist[depot][store]
```

- якщо точки вже є:

```python
marginal_km = dist[current_last_stop][store]
```

Краще наближення для другої версії:

```python
marginal_km = new_route_km - old_route_km
```

тобто через повний перерахунок тимчасового маршруту.

### 4.6. Довжина маршруту

Для послідовності стопів:

```python
route = [depot, stop_1, stop_2, ..., stop_n, depot]
```

формула:

```python
total_km = sum(dist[route[i]][route[i + 1]] for i in range(len(route) - 1))
```

Тут `dist` має бути не сире ребро з CSV, а найкоротка відстань з матриці Dijkstra.

### 4.7. Час маршруту

```python
drive_hours = total_km / avg_speed_kmh
```

```python
unload_hours = stops_count * unload_min_default / 60
```

```python
total_elapsed_h = drive_hours + unload_hours
```

Для MVP цього достатньо. Правила EC 561/2006 можна додати пізніше окремим шаром.

### 4.8. Вартість маршруту

```python
total_cost = total_km * cost_per_km
```

### 4.9. En-Route insertion

Формули з концепту:

```python
detour_km = dist[A][candidate] + dist[candidate][C] - dist[A][C]
detour_ratio = detour_km / base_route_km
```

Кандидат валідний, якщо:

- `truck.type in {"truck", "van"}`
- `priority in {"CRITICAL", "ELEVATED"}` або вище за `settings.min_priority_enroute`
- `detour_ratio <= settings.max_detour_ratio`
- є достатня місткість

Фінансова логіка:

```python
cost_with_stop = (base_route_km + detour_km) * cost_per_km
cost_without_stop = base_route_km * cost_per_km + separate_trip_km * cost_per_km
savings = cost_without_stop - cost_with_stop
```

Для `separate_trip_km` у першій версії можна брати окремий рейс від depot цієї машини:

```python
separate_trip_km = dist[depot][candidate] + dist[candidate][depot]
```

## 5. Алгоритм обрахунку шляхів і маршрутів

### Етап 1. Прочитати і нормалізувати дані

1. Завантажити:
   - `nodes`
   - `edges`
   - `trucks`
   - `demand`
   - `warehouse_stock`
   - `products`
   - `settings`
2. Зібрати словники по ID.
3. Для `edges` побудувати симетричний adjacency-list.
4. Для `trucks` підставити дефолти із `settings`, якщо в колонках `NULL`.

### Етап 2. Побудувати shortest path matrix

Для кожної релевантної вершини запустити Dijkstra з `heapq`.

Мінімальний набір релевантних вузлів для `Leg 2`:

- всі `depot_node_id` для машин типу `truck` і `van`
- всі магазини, де `requested_qty > 0`

Потрібні два результати:

1. `dist[start][end]`
2. `prev[start][end]` або `predecessors[start]` для відновлення реального шляху

Рекомендація:

```python
distances_by_source[source] = dijkstra(graph, source)
predecessors_by_source[source] = predecessors
```

### Етап 3. Підготувати список замовлень

Для кожного рядка `demand`:

1. перерахувати `priority`
2. якщо `requested_qty <= 0`, пропустити
3. знайти склад(и), де є потрібний товар
4. порахувати score постачання зі складу
5. створити normalized order object

Приклад внутрішньої структури:

```python
{
    "store_id": "STORE_5",
    "product_id": "product_C",
    "qty_kg": 138.0,
    "priority": "CRITICAL",
    "priority_weight": 4.0,
    "warehouse_id": "WAREHOUSE_2",
}
```

### Етап 4. Greedy assignment по машинах

Для `Leg 2` працюємо тільки з `truck` і `van`.

Логіка:

1. відсортувати замовлення:
   - спочатку `CRITICAL`
   - потім `ELEVATED`
   - потім `NORMAL`
   - всередині можна за `qty_kg` або за ближчістю
2. для кожного замовлення перебрати валідні машини:
   - машина базується на відповідному складі
   - вистачає `remaining_capacity`
3. порахувати:

```python
score = marginal_km * truck.cost_per_km / order.priority_weight
```

4. вибрати машину з мінімальним `score`
5. оновити:
   - `remaining_capacity`
   - список призначених stop-ів
   - cargo по продукту

### Етап 5. Побудувати порядок stop-ів

Для кожної машини:

1. взяти унікальний список магазинів, призначених машині
2. побудувати початковий маршрут nearest-neighbor:

```python
[depot, ..., depot]
```

3. покращити маршрут через `2-opt`

Nearest-neighbor:

```python
current = depot
while unvisited:
    next_stop = argmin(dist[current][candidate])
```

2-opt:

```python
if swapped_route_km < current_route_km:
    accept_swap()
```

### Етап 6. За бажанням вставити En-Route точки

Для кожного маршруту перевірити магазини з високим пріоритетом, які ще не потрапили в route.

Перевірка на кожній парі сусідніх stop-ів:

```python
A -> C
```

чи вигідно вставити:

```python
A -> B -> C
```

### Етап 7. Порахувати фінальні метрики

Для кожного маршруту:

- `total_km`
- `drive_hours`
- `total_elapsed_h`
- `total_cost`
- `timeline`
- `arrival_time`
- `time_status`

### Етап 8. Записати результат у таблиці

1. старі `routes.is_active = 0`
2. вставити нові `routes`
3. вставити `route_cargo`
4. оновити `warehouse_stock.reserved_kg`

## 6. Що важливо для відновлення реального шляху

`routes.stops` у схемі БД зберігає тільки контрольні точки:

```json
["WAREHOUSE_1", "STORE_7", "STORE_3", "WAREHOUSE_1"]
```

Це не повний список усіх проміжних вузлів графа.

Тому:

- для БД достатньо зберігати саме список stop-ів
- якщо для карти треба повна polyline, її краще відновлювати через `predecessors` Dijkstra

Псевдокод:

```python
full_path = []
for a, b in zip(route[:-1], route[1:]):
    segment = reconstruct_path(predecessors_by_source[a], a, b)
    full_path.extend(segment if not full_path else segment[1:])
```

## 7. Важливі нюанси і підводні камені

### 1. `edges.csv` треба симетризувати

Це обов'язково.

### 2. `requested_qty` уже в кг

Для першого MVP не ускладнюй solver dimensional weight-ом. Бери `requested_qty` як фактичне навантаження машини.

### 3. `routes.csv` і `route_cargo.csv` зараз порожні

Це нормально. Вони мають наповнюватись тільки після `Solve`.

### 4. У схемі немає `factory_stock`

Це означає, що `Leg 1` зараз неповністю формалізований.

Тобто треба прийняти одне з припущень:

- або завод має нескінченний запас кожного товару
- або `Leg 1` поки не рахуємо повноцінно, а тільки фіксуємо потребу складу в поповненні

Для хакатонного MVP я б радив:

- повністю довести до робочого стану `Leg 2`
- для `Leg 1` зробити спрощений варіант: найближчий завод закриває дефіцит складу

### 5. Пріоритет краще перераховувати, а не довіряти CSV

Це захист від неузгодженості між `current_stock`, `min_stock` і колонкою `priority`.

## 8. Рекомендована структура модулів для Тимура

Якщо йти по `TASKS.md`, то хороший поділ такий:

- `solver/graph.py`
  - `build_graph_from_edges`
  - `dijkstra`
  - `build_distance_matrix`
  - `reconstruct_path`
- `solver/priority.py`
  - `compute_priority`
  - `priority_weight`
- `solver/cost.py`
  - `resolve_truck_cost_params`
  - `compute_cost_per_km`
- `solver/assignment.py`
  - `select_candidate_warehouses`
  - `assign_leg2`
  - `assign_leg1_simple`
- `solver/routing.py`
  - `greedy_tsp`
  - `two_opt`
  - `compute_route_metrics`
- `solver/enroute.py`
  - `detour_km`
  - `detour_ratio`
  - `savings`
  - `try_insert_candidate`

## 9. Що саме варто зробити першим

Оптимальний порядок для старту:

1. `graph.py`
   - побудувати граф із симетризацією
   - зробити Dijkstra через `heapq`
   - зробити distance matrix
2. `priority.py`
   - формула `NORMAL / ELEVATED / CRITICAL`
3. `cost.py`
   - формула `cost_per_km`
4. `assignment.py`
   - простий greedy для `Leg 2`
5. `routing.py`
   - nearest-neighbor
   - 2-opt
   - підрахунок `total_km`, `total_cost`, `total_elapsed_h`
6. `enroute.py`
   - як покращення після базового solver-а

## 10. Швидкі sanity-check приклади з поточного demo_data

Це корисно, щоб руками перевіряти, чи solver поводиться логічно.

### Критичні замовлення в поточному CSV

Зараз у `demand.csv` є 5 `CRITICAL` записів:

- `STORE_1`, `product_A`, `92.0`
- `STORE_5`, `product_A`, `95.0`
- `STORE_5`, `product_C`, `138.0`
- `STORE_9`, `product_B`, `90.0`
- `STORE_13`, `product_C`, `142.0`

### Яку поведінку варто очікувати

- `STORE_1` майже напевно має піти через `WAREHOUSE_1`
- `STORE_5` майже напевно має піти через `WAREHOUSE_2`
- `STORE_9` майже напевно має піти через `WAREHOUSE_3`
- `STORE_13` майже напевно має піти через `WAREHOUSE_4`

Чому:

- це їхні локальні склади
- у відповідних складах є потрібний товар
- у графі є прямі ребра `warehouse -> store` для цих кластерів

### Що можна перевірити після першої реалізації

1. `CRITICAL` замовлення мають розбиратися раніше за `NORMAL`
2. жоден `van` або `truck` не має перевищувати `capacity_kg`
3. `routes.csv` після solve вже не порожній
4. `route_cargo.csv` узгоджується з `routes`
5. `warehouse_stock.reserved_kg` збільшився рівно на обсяг призначеного cargo

## 11. Короткий висновок

Якщо коротко: для першої робочої версії треба закрити повний цикл `Leg 2`:

```text
demand + warehouse_stock + trucks + edges
-> shortest paths
-> greedy assignment
-> route ordering
-> route optimization
-> routes / route_cargo / reserved_kg
```

Найважливіше не пропустити три речі:

1. симетризацію `edges.csv`
2. пріоритетну вагу в assignment
3. запис результату не тільки в `routes`, а й у `route_cargo` та `warehouse_stock.reserved_kg`
