# Frontend Guideline For The Current Backend

This document explains what the backend already does today, how the data flows
through the system, and how to build a frontend around the existing API.

It is written for two main frontend surfaces:

- a dispatcher page with a map showing warehouses, stores, trucks, and active routes
- a warehouse worker page where the worker can see current loading/shipping state and trigger actions

The goal is to describe the backend **as it exists now**, not an ideal future
version.

---

## 1. What The Backend Already Does

The backend is a FastAPI service with SQLite persistence.

At startup it:

- initializes the database schema
- ensures default settings exist
- exposes all API routes under `/api`

The backend currently supports these process groups:

### Data bootstrap

- upload a ZIP dataset through `POST /api/upload`
- generate a random dataset through `POST /api/generate`
- store network, trucks, products, stock, demand, settings, routes, and execution state in SQLite

### Read model for UI

- read the full logistics graph through `GET /api/network`
- read stock through `GET /api/stock`
- read demand through `GET /api/demand`
- read settings through `GET /api/settings`
- read active routes through `GET /api/routes`
- read one truck route in detail through `GET /api/routes/{truck_id}`
- read execution details for a route through `GET /api/routes/{route_id}/execution`

### Planning and replanning

- full plan generation through `POST /api/solve`
- urgent re-solve through `POST /api/urgent`
- execution-aware reroute through `POST /api/reroute`

### Live execution state

- start truck loading
- complete truck loading
- depart truck
- update truck position
- mark a route stop as complete

### Offline/batch sync

- submit a queue of actions through `POST /api/actions/batch`

---

## 2. Current Backend State Model

The backend now has two layers of truth:

### A. Planning layer

This is the solver result:

- `routes`
- `route_cargo`
- `warehouse_stock.reserved_kg`

This answers:

- which truck should go where
- what cargo is assigned to the route
- which warehouse stock is reserved for that plan

### B. Execution layer

This is the live operational state:

- `truck_state`
- `route_execution`
- `route_cargo_state`

This answers:

- whether the truck is idle, loading, loaded, or already en route
- which route is currently active for that truck
- which stop has been completed already
- which cargo is still reserved, already loaded, or delivered
- where the truck currently is, if position updates are being sent

This is the main backend change the frontend must now reflect.

---

## 3. Main Backend Processes In The System

These are the real process flows happening in the backend today.

### Process 1. Dataset load or generation

Flow:

```text
upload/generate dataset
-> import into SQLite
-> replace previous operational data
-> UI can read network, stock, demand, trucks, routes
```

Frontend impact:

- this is usually an admin/debug flow, not a warehouse worker flow
- after upload/generate, refresh all frontend state

### Process 2. Full planning

Flow:

```text
POST /api/solve
-> load nodes, edges, trucks, warehouse_stock, demand, products, settings
-> run solver
-> mark old active routes inactive
-> persist new routes
-> persist route cargo
-> update reserved warehouse stock
-> seed execution state for the active plan
```

Important frontend meaning:

- after `solve`, the UI should treat the returned routes as the new source of truth
- execution state is reset and reseeded from the new active plan

### Process 3. Urgent request

Flow:

```text
POST /api/urgent
-> increase demand for a store/product
-> force priority to CRITICAL
-> run full solve
-> return diff between old and new routes
```

Frontend impact:

- use this when the operator wants a simple urgent full re-plan
- it is planning-oriented, not execution-preserving

### Process 4. Execution-aware reroute

Flow:

```text
POST /api/reroute
-> increase urgent demand
-> read active routes + truck_state + route_execution + route_cargo_state
-> run fresh planning snapshot
-> compare current active routes with new plan
-> replace eligible routes
-> preserve supersession lineage with supersedes_route_id
-> keep unchanged routes as-is
```

Current reroute rules:

- trucks in `idle`, `loading`, or `loaded` may be replaced safely
- trucks in `en_route` can be handled with locked-prefix logic if `allow_in_progress=true`
- changed routes create a new route version and link to the previous one

Frontend impact:

- this is the better operator action when execution state matters
- show changed trucks, unchanged trucks, route ID mapping, and locked prefixes in the UI

### Process 5. Warehouse execution

Flow:

```text
worker sees assigned route
-> loading/start
-> loading/complete
-> depart
-> stop-complete
-> route completes
-> truck becomes idle again
```

Important backend state transitions:

- truck: `idle -> loading -> loaded -> en_route -> idle`
- route: `planned -> loading -> in_progress -> completed`
- cargo: `reserved -> loaded -> delivered`

Frontend impact:

- the warehouse page should be built around these actions
- buttons should only be enabled when the current state allows them

### Process 6. Stock correction / shipment confirmation

Flow:

```text
worker confirms stock change
-> POST /api/stock/update
-> quantity_kg decreases
-> reserved_kg also decreases
```

Frontend impact:

- use this for physical stock changes
- refresh stock table after success

### Process 7. Demand correction

Flow:

```text
operator updates current stock at a store
-> POST /api/demand/update
-> requested_qty recalculated
-> priority recalculated
```

Frontend impact:

- use this for manual store-side demand correction
- this belongs more to dispatcher/store operations than warehouse loading

---

## 4. API Endpoints The Frontend Should Use

This section maps endpoints to frontend screens and actions.

## 4.1 Read endpoints

### `GET /api/network`

Use for:

- base map nodes
- graph edges
- store risk coloring

Response shape:

- `nodes[]`
  - `id`
  - `name`
  - `type` = `factory | warehouse | store`
  - `capacity_kg`
  - `lat`
  - `lon`
  - `priority`
  - `current_stock`
  - `min_stock`
- `edges[]`
  - `from_id`
  - `to_id`
  - `distance_km`

Frontend notes:

- the map can render all physical points from `nodes`
- stores may already carry their top demand priority in this payload
- trucks are not returned here, so truck markers must come from route/execution data

### `GET /api/routes`

Use for:

- active routes list
- route overlays on map
- truck cards
- current route execution state summary

Query:

- optional `leg`

Response shape:

- `routes[]`
  - `id`
  - `truck_id`
  - `truck_name`
  - `truck_type`
  - `supersedes_route_id`
  - `leg`
  - `stops[]`
  - `stops_names[]`
  - `total_km`
  - `total_cost`
  - `estimated_hours`
  - `created_at`
  - `is_active`
  - `execution`
    - `route_status`
    - `last_completed_stop_index`
    - `next_stop_index`
    - `started_at`
    - `completed_at`
    - `locked_prefix[]`
    - `current_stop_node_id`
    - `current_stop_name`
    - `is_current_route_for_truck`
    - `truck_state`
      - `status`
      - `active_route_id`
      - `current_node_id`
      - `current_node_name`
      - `current_lat`
      - `current_lon`
      - `remaining_capacity_kg`
      - `updated_at`

Frontend notes:

- this is the main source for truck markers and route state on the map
- if `current_lat/current_lon` exist, use them for the truck marker
- otherwise fall back to the current route node or depot node

### `GET /api/routes/{truck_id}`

Use for:

- truck detail drawer/modal
- warehouse worker task detail
- per-stop breakdown

Response shape:

- truck summary
- `route`
  - route metrics
  - `stops[]`
  - `stops_details[]`
    - `node_id`
    - `node_name`
    - `type`
    - `lat`
    - `lon`
    - `action`
    - `scheduled_time`
    - `priority` for store stops
    - `cargo_to_unload[]` for store stops
    - `cargo_to_load[]` for depot stop
  - `execution`

Frontend notes:

- this is the best endpoint for the warehouse worker page
- `cargo_to_load[]` at the first stop is especially useful for loading UI

### `GET /api/routes/{route_id}/execution`

Use for:

- execution detail panel
- cargo execution state
- reroute history and locked-prefix view

Response shape:

- `route_id`
- `truck_id`
- `supersedes_route_id`
- `is_active`
- `route_status`
- `last_completed_stop_index`
- `next_stop_index`
- `started_at`
- `completed_at`
- `updated_at`
- `locked_prefix[]`
- `remaining_suffix[]`
- `current_stop_node_id`
- `truck_state`
- `cargo_state[]`
  - `stop_node_id`
  - `product_id`
  - `qty_reserved_kg`
  - `qty_loaded_kg`
  - `qty_delivered_kg`
  - `qty_onboard_kg`

Frontend notes:

- use this when you need exact cargo execution numbers
- this is the best endpoint to power a detailed operational timeline

### `GET /api/stock`

Use for:

- warehouse inventory page
- stock tables
- inventory badges on warehouse cards

Response shape:

- `stock[]`
  - `warehouse_id`
  - `warehouse_name`
  - `product_id`
  - `product_name`
  - `quantity_kg`
  - `reserved_kg`
  - `available_kg`

### `GET /api/demand`

Use for:

- store demand table
- dispatcher warning list
- map filters by priority

Query:

- optional `priority`

Response shape:

- `demand[]`
  - `node_id`
  - `node_name`
  - `product_id`
  - `product_name`
  - `current_stock`
  - `min_stock`
  - `requested_qty`
  - `priority`
  - `is_urgent`
  - `updated_at`

### `GET /api/settings`

Use for:

- admin/settings panel
- displaying current planning parameters

---

## 4.2 Planning endpoints

### `POST /api/solve`

Use for:

- full route planning

Request:

```json
{
  "departure_time": "08:00"
}
```

Response:

- `status`
- `solve_time_ms`
- `enroute_suggestions`
- `routes[]`
- `summary`

Frontend notes:

- after success, refresh `network`, `routes`, `stock`, and `demand`

### `POST /api/urgent`

Use for:

- quick urgent demand escalation with full re-solve

Request:

```json
{
  "node_id": "STORE_7",
  "product_id": "product_A",
  "qty": 50,
  "departure_time": "08:00"
}
```

Response:

- `urgent_update`
- `diff`
- `solve`

### `POST /api/reroute`

Use for:

- execution-aware urgent replanning

Request:

```json
{
  "node_id": "STORE_7",
  "product_id": "product_A",
  "qty": 50,
  "departure_time": "08:00",
  "reroute_reason": "operator_request",
  "allow_in_progress": true
}
```

Response:

- `urgent_update`
- `reroute_reason`
- `allow_in_progress`
- `changed_truck_ids[]`
- `unchanged_truck_ids[]`
- `route_id_mapping`
- `locked_prefix_by_route_id`
- `updated_cargo_distribution`
- `diff`
- `routes`

Frontend notes:

- this is the endpoint that best fits a dispatcher “reroute” button
- use the returned `route_id_mapping` to update open drawers or selected route state

---

## 4.3 Execution endpoints

### `POST /api/trucks/{truck_id}/loading/start`

Use for:

- worker begins loading a truck

### `POST /api/trucks/{truck_id}/loading/complete`

Use for:

- worker confirms loading is finished

Important effect:

- cargo moves from reserved state to loaded state

### `POST /api/trucks/{truck_id}/depart`

Use for:

- truck leaves the depot

### `POST /api/trucks/{truck_id}/position`

Use for:

- truck GPS/device position update

Request:

```json
{
  "current_lat": 50.4501,
  "current_lon": 30.5234,
  "current_node_id": "STORE_7",
  "updated_at": "2026-04-04 12:15:00"
}
```

Frontend notes:

- `current_node_id` is optional
- use this from a driver/mobile/device integration if available

### `POST /api/routes/{route_id}/stop-complete`

Use for:

- confirming the next route stop is completed

Important effect:

- route progress advances
- delivered cargo is updated
- free truck capacity increases
- if final stop is done, route becomes completed and truck returns to idle

---

## 4.4 Warehouse/data mutation endpoints

### `POST /api/stock/update`

Use for:

- physical stock change after shipment

### `POST /api/demand/update`

Use for:

- correcting a store’s current stock

### `PUT /api/settings`

Use for:

- updating planning parameters

### `POST /api/actions/batch`

Use for:

- offline queue replay
- mobile worker sync after reconnect

Supported actions include:

- stock update
- demand update
- urgent
- reroute
- solve
- truck loading start
- truck loading complete
- truck depart
- truck position
- route stop complete

---

## 5. Recommended Frontend Structure

The backend supports two very clear frontend domains.

## 5.1 Dispatcher page

Purpose:

- see the whole network
- see route plans and truck state
- react to urgent demand and reroute needs

Recommended layout:

### Left side or main panel

- map with:
  - factories
  - warehouses
  - stores
  - truck markers
  - route polylines between route stops

### Right panel or drawers

- current routes list
- demand risk list
- stock overview by warehouse
- selected truck/route details

### Dispatcher actions

- `Solve`
- `Urgent`
- `Reroute`
- refresh execution state

Recommended data loading:

On page load:

1. `GET /api/network`
2. `GET /api/routes`
3. `GET /api/demand`
4. `GET /api/stock`

Recommended polling:

- `GET /api/routes` every 5-15 seconds
- `GET /api/demand` every 15-30 seconds
- refresh `stock` after any worker or shipment action

Map rendering strategy:

- nodes from `/api/network`
- route lines from `/api/routes[].stops`
- truck markers from `/api/routes[].execution.truck_state`
- if `current_lat/current_lon` exist, render live truck position
- otherwise render the truck at `current_node_id` or the route depot

Important current limitation:

- backend gives route stops, not full graph polylines between each pair of stops
- if the map needs exact road-like path segments, the frontend must reconstruct them itself or use straight lines for now

## 5.2 Warehouse worker page

Purpose:

- see which trucks are waiting to be loaded
- see exactly what to load
- confirm loading and departures
- optionally update physical stock and route progress

Recommended layout:

### Truck task list

Use `/api/routes` or `/api/routes/{truck_id}` and filter by:

- `execution.truck_state.status = idle | loading | loaded`
- usually `leg = 2` for warehouse-to-store operations

Each task card should show:

- truck name
- truck type
- route status
- current planned stops
- cargo to load
- current remaining capacity

### Task detail view

Use `GET /api/routes/{truck_id}`.

Render:

- truck summary
- depot
- full stop sequence
- `stops_details[0].cargo_to_load`
- execution status

### Worker actions

Recommended button flow:

1. `Start loading`
2. `Loading complete`
3. `Depart`
4. `Stop complete` for each next stop

Optional stock action:

- `POST /api/stock/update` when a stock correction or shipment confirmation is needed

Recommended button enable rules:

- show `Start loading` only when truck status is `idle`
- show `Loading complete` only when truck status is `loading`
- show `Depart` only when truck status is `loaded`
- show `Stop complete` only when route status is `in_progress`

---

## 6. How To Represent Objects On The Map

The frontend needs to display both static network objects and live operational objects.

### Static objects

From `/api/network`:

- `factory`
- `warehouse`
- `store`

Recommended map styles:

- factory: neutral industrial icon
- warehouse: logistics/depot icon
- store: status-colored icon

Store coloring rule:

- `CRITICAL` -> red
- `ELEVATED` -> orange
- `NORMAL` -> blue or green

### Live truck objects

From `/api/routes[].execution.truck_state`:

- `status`
- `current_lat/current_lon`
- `current_node_id`
- `remaining_capacity_kg`

Recommended truck colors:

- `idle` -> gray
- `loading` -> amber
- `loaded` -> blue
- `en_route` -> green
- `blocked` -> red

### Route objects

From `/api/routes`:

- `stops[]`
- `leg`
- `truck_name`
- execution status

Recommended route rendering:

- one color per truck
- dim or dashed route if it is not the truck’s current route
- visually distinguish completed prefix vs remaining suffix when possible

---

## 7. Backend Limitations The Frontend Must Respect

These are important so the frontend does not assume functionality that the backend does not provide yet.

### No dedicated trucks list endpoint

Today truck state is exposed through route endpoints, not through `GET /api/trucks`.

Frontend implication:

- build the truck layer from `/api/routes`
- if you need a truck registry page later, the backend should add a dedicated trucks endpoint

### No WebSocket/live stream

Updates are request/response only.

Frontend implication:

- use polling
- optionally use optimistic UI for worker actions

### No authentication/roles yet

Frontend implication:

- role-based pages are a UI concern for now
- access control is not enforced by the backend yet

### Route geometry is stop-based, not polyline-based

Frontend implication:

- draw straight route segments between stop coordinates for MVP
- do not assume backend gives map-ready path geometry

### No dedicated “warehouse worker request creation” endpoint

What exists today:

- stock update
- demand update
- urgent
- reroute
- execution mutations

Frontend implication:

- if the worker must “create a request”, decide whether that means:
  - stock correction
  - urgent replenishment
  - reroute request
  - demand correction
- map each button to an existing backend mutation

### Execution is route-centric, not user-centric

Frontend implication:

- the worker UI should operate on truck/route IDs
- do not assume per-user assignments exist in the backend

---

## 8. Best Practical Frontend Plan

If the frontend starts now against the current backend, the safest build order is:

### Step 1. Build a shared API client

Support these first:

- `/api/network`
- `/api/routes`
- `/api/routes/{truck_id}`
- `/api/stock`
- `/api/demand`
- `/api/solve`
- `/api/reroute`
- execution endpoints

### Step 2. Build dispatcher map page

First version:

- show all nodes
- show active routes
- show truck markers
- show demand severity
- allow solve and reroute

### Step 3. Build warehouse worker task page

First version:

- list route assignments by truck
- show cargo to load
- allow loading start / complete / depart
- show next stop and route state

### Step 4. Add execution refresh and polling

- poll `/api/routes`
- refresh details on interaction success

### Step 5. Add advanced execution detail panels

- route execution drilldown
- cargo state drilldown
- reroute history via `supersedes_route_id`

---

## 9. Recommended Frontend Data Ownership

To keep the UI maintainable, treat these backend endpoints as primary sources:

### Source of truth for map topology

- `/api/network`

### Source of truth for active trucks and routes

- `/api/routes`

### Source of truth for one truck’s operational task

- `/api/routes/{truck_id}`

### Source of truth for deep execution state

- `/api/routes/{route_id}/execution`

### Source of truth for warehouse inventory

- `/api/stock`

### Source of truth for demand severity

- `/api/demand`

---

## 10. Final Recommendation

The backend is already strong enough to support a meaningful frontend now.

The frontend should not be designed as “just show the solver result”.
It should be designed around two operational loops:

### Dispatcher loop

```text
see network -> inspect demand -> solve/reroute -> monitor truck execution
```

### Warehouse worker loop

```text
see assigned truck -> load cargo -> confirm loading -> depart -> track stop completion
```

If the frontend follows the API contracts above, it can already deliver:

- a full network map with warehouses, stores, and trucks
- a dispatcher view of active plans and reroutes
- a warehouse worker page with real operational actions
- a basic offline-sync path through `/api/actions/batch`

That is the best fit to the backend in its current state.
