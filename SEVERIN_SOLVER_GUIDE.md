# Severin Solver Guide

This document explains the current backend-to-solver integration in LogiFlow and
describes the main implementation hooks needed to support dynamic truck tracking
and dynamic rerouting.

It is intended as the server-side companion to
[TIMUR_SOLVER_GUIDE.md](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/TIMUR_SOLVER_GUIDE.md).

---

## 1. Purpose

Right now, the backend already supports:

- loading datasets into SQLite
- exposing stable API endpoints for frontend
- reading solver inputs from the database
- running the solver through `POST /api/solve`
- persisting `routes`, `route_cargo`, and warehouse reservations
- handling urgent re-solve and batch sync

What is still missing is the **live operational layer**:

- truck execution state
- route progress state
- onboard cargo state
- partial replanning based on what is already in motion

This guide documents:

- what is already implemented
- where the solver is connected to the backend
- what must be added to support dynamic truck tracking

---

## 2. What Is Already Implemented

### Backend foundation

The backend is a working FastAPI service with SQLite persistence.

Main entrypoints:

- [backend/main.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/main.py)
- [backend/api/router.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/api/router.py)
- [backend/database.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/database.py)

The current DB already stores:

- network topology
- products
- trucks
- warehouse stock
- demand
- route plans
- route cargo
- settings

### Dataset flow

The backend can populate the database from:

- local `demo_data/`
- uploaded ZIP archive through `POST /api/upload`
- generated random dataset through `POST /api/generate`

This means the solver is already working against SQL-backed system state, not
only CSV files.

### Solver integration

The main integration path is:

```text
POST /api/solve
-> load DB state
-> build SolverInputs
-> call solve_network(...)
-> persist routes
-> persist route cargo
-> update reserved stock
-> return response to frontend
```

This is implemented in:

- [backend/api/routes/solve.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/api/routes/solve.py)
- [backend/db/solver_runtime.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/db/solver_runtime.py)

### Current mutation workflows

The backend already supports these operational mutations:

- `POST /api/stock/update`
- `POST /api/demand/update`
- `POST /api/urgent`
- `POST /api/actions/batch`

These are important because dynamic routing will build on top of the same
mutation model instead of bypassing it.

---

## 3. Current Solver Contract

### Current input source

The solver currently receives a full planning snapshot from SQLite.

`load_solver_inputs_from_db()` reads:

- `nodes`
- `edges`
- `trucks`
- `warehouse_stock`
- `demand`
- `products`
- `settings`

and converts them into `SolverInputs`.

Implementation:

- [backend/db/solver_runtime.py](c:/Users/Ivan/Desktop/GOBLIN_GANG/Best-Hackathon-2026-goblin-gang/backend/db/solver_runtime.py)

### Current output persistence

After `solve_network(...)`, the backend persists:

- `routes`
- `route_cargo`
- updated `warehouse_stock.reserved_kg`

Important current behavior:

- old active routes are marked inactive
- new routes are inserted as the new active plan
- this is full-plan persistence, not partial route patching

### Current response shape

`POST /api/solve` currently returns:

- route list
- route summary
- solve time
- empty `enroute_suggestions`

This is good enough for static planning and MVP dispatch, but not enough for
live fleet execution.

---

## 4. What the Backend Knows Today

At the moment, the system knows:

- what trucks exist
- what route was planned for each truck
- what cargo was assigned to each route
- what stock has been reserved

It does **not** yet know:

- whether a truck is idle, loading, moving, unloading, or finished
- where the truck currently is
- which stop has already been completed
- what cargo is still onboard
- whether a planned route is still editable

This is the core gap between:

- **static planning**
and
- **dynamic execution-aware replanning**

---

## 5. Why Dynamic Truck State Matters

Dynamic rerouting requires more than route plans.

If an urgent request arrives while trucks are already operating, the backend
must know:

- which trucks are still available
- which trucks have already departed
- which stops are already completed
- how much capacity each truck still has
- which cargo is still onboard
- whether a truck can still absorb a new stop

Without this, `POST /api/urgent` can only do a simplified global re-solve.

That is exactly what the current implementation does:

```text
urgent request
-> update demand
-> rerun solve
-> return diff
```

This is acceptable for MVP planning, but not enough for real-time operational
dispatch.

---

## 6. Main Integration Points for Dynamic Tracking

To implement dynamic truck tracking cleanly, there are four main places where
the backend and solver must connect.

### A. Truck execution state

We need a persistent live state for each truck.

Minimum fields:

- `truck_id`
- `status`
- `active_route_id`
- `current_node_id` or `current_lat`, `current_lon`
- `last_completed_stop_index`
- `remaining_capacity_kg`
- `updated_at`

Recommended statuses:

- `idle`
- `loading`
- `loaded`
- `en_route`
- `unloading`
- `completed`
- `blocked`

This can be implemented either as:

- a new `truck_state` table
or
- a `truck_state` + `truck_state_history` pair

Recommended backend ownership:

- backend stores and validates live truck state
- solver reads it as part of dynamic planning input

### B. Route execution state

Current `routes` are plan objects. We also need execution progress.

Minimum fields:

- `route_id`
- `status`
- `started_at`
- `completed_at`
- `current_stop_index`
- `locked_from_stop_index`

Recommended statuses:

- `planned`
- `loading`
- `in_progress`
- `completed`
- `cancelled`

This tells the solver which part of the route is still mutable.

Example:

```text
route stops = [WAREHOUSE_1, STORE_1, STORE_2, STORE_3, WAREHOUSE_1]
current_stop_index = 2
```

Meaning:

- the first two stops are already fixed/history
- only the remaining suffix may be replanned

### C. Cargo execution state

Right now cargo is stored as planned route cargo. For dynamic rerouting, we also
need execution truth.

Minimum state transitions:

- `reserved`
- `loaded`
- `delivered`

This can be stored by extending `route_cargo` or by adding a separate
`route_cargo_state` table.

Why this matters:

- `reserved` means stock is allocated but still at the warehouse
- `loaded` means the truck physically has it
- `delivered` means it is no longer onboard

This distinction is required if we want to know whether the cargo can still be
reassigned.

### D. Dynamic solver input adapter

The current adapter builds a static `SolverInputs` snapshot from tables such as
`demand`, `warehouse_stock`, and `trucks`.

For dynamic rerouting we need an extended adapter that also reads:

- live truck state
- route progress
- onboard cargo
- locked stops / executed stops
- mutable remaining route suffix

In practice this means adding a second backend-to-solver input builder, for
example:

```python
load_dynamic_solver_inputs_from_db(...)
```

This should not replace the current full planning entrypoint. It should exist in
parallel with it.

---

## 7. Recommended Data Model Additions

Below is the minimum backend-side model that would unlock dynamic routing.

### `truck_state`

Suggested fields:

```text
truck_id TEXT PRIMARY KEY
status TEXT
active_route_id INTEGER NULL
current_node_id TEXT NULL
current_lat REAL NULL
current_lon REAL NULL
last_completed_stop_index INTEGER DEFAULT 0
remaining_capacity_kg REAL
updated_at TEXT
```

### `route_execution`

Suggested fields:

```text
route_id INTEGER PRIMARY KEY
status TEXT
current_stop_index INTEGER DEFAULT 0
started_at TEXT NULL
completed_at TEXT NULL
locked_from_stop_index INTEGER DEFAULT 0
updated_at TEXT
```

### `route_cargo_state`

Suggested fields:

```text
route_id INTEGER
stop_node_id TEXT
product_id TEXT
qty_reserved_kg REAL
qty_loaded_kg REAL
qty_delivered_kg REAL
PRIMARY KEY (route_id, stop_node_id, product_id)
```

### Optional `truck_state_history`

This is useful for debugging, analytics, and replay:

```text
id INTEGER PK
truck_id TEXT
status TEXT
current_node_id TEXT NULL
current_lat REAL NULL
current_lon REAL NULL
event_time TEXT
source TEXT
```

---

## 8. Recommended New API Endpoints

To make dynamic tracking usable from frontend or mobile flows, the backend
should expose truck execution mutations explicitly.

Recommended endpoints:

### Truck execution

- `POST /api/trucks/{truck_id}/loading/start`
- `POST /api/trucks/{truck_id}/loading/complete`
- `POST /api/trucks/{truck_id}/depart`
- `POST /api/trucks/{truck_id}/arrive`
- `POST /api/trucks/{truck_id}/complete`

### Route execution

- `POST /api/routes/{route_id}/progress`
- `POST /api/routes/{route_id}/stop-complete`
- `GET /api/routes/{route_id}/execution`

### Position updates

- `POST /api/trucks/{truck_id}/position`

Payload example:

```json
{
  "current_lat": 50.4501,
  "current_lon": 30.5234,
  "updated_at": "2026-04-04 12:15:00"
}
```

### Dynamic rerouting

- `POST /api/reroute`

This endpoint should differ from `POST /api/solve`.

`/api/solve`:

- full planning from current state

`/api/reroute`:

- execution-aware replanning of only eligible trucks and only editable route
  suffixes

---

## 9. Recommended Solver-Side Contract for Dynamic Planning

The simplest clean architecture is to keep two solver modes.

### Mode 1. Full planning

Current mode:

```text
full network snapshot
-> full route generation
-> full persistence of active plan
```

Current backend function:

- `run_solver_and_persist(...)`

### Mode 2. Dynamic rerouting

New mode:

```text
current active routes
+ truck execution state
+ current location
+ completed stops
+ onboard cargo
+ new urgent demand
-> partial replanning
-> updated route suffixes or replacement active routes
```

The dynamic solver input should include at least:

- trucks that are still eligible for replanning
- current truck position
- current onboard cargo
- current remaining capacity
- remaining unserved demand
- route suffix that may still be changed

The dynamic solver output should include:

- truck-level route changes
- unchanged locked prefix
- new editable suffix
- updated cargo distribution
- update reason / reroute reason

---

## 10. Backend Implementation Strategy

Recommended order:

### Step 1. Add persistent live truck state

Do this first because all dynamic logic depends on it.

### Step 2. Add route progress tracking

Without route progress, rerouting cannot know which stops are already fixed.

### Step 3. Add cargo execution state

This separates planned stock from physically loaded stock.

### Step 4. Add execution update endpoints

This gives frontend/mobile a way to keep state fresh.

### Step 5. Add dynamic solver adapter

Only after steps 1-4, because otherwise the solver still lacks real-world
context.

### Step 6. Add `/api/reroute`

At this point the solver can make execution-aware decisions safely.

---

## 11. What Can Be Reused from the Current System

The good news is that most of the current architecture can stay in place.

Already reusable:

- SQLite schema approach
- import flow
- current route persistence
- current stock reservation logic
- current `POST /api/urgent`
- current `POST /api/actions/batch`
- current solver entrypoint `solve_network(...)`

What needs extension, not replacement:

- solver input adapter
- DB schema
- route persistence model
- mutation APIs

This means dynamic tracking can be added incrementally without rebuilding the
whole backend.

---

## 12. Current Limitation Summary

At the moment, the backend supports:

- planning
- reservation
- shipment confirmation
- urgent full re-solve

It does not yet support:

- live truck status tracking
- route execution tracking
- loaded vs delivered cargo tracking
- true partial rerouting based on execution state

So the current system is already a strong planning MVP, but it is not yet a
fully execution-aware dispatch system.

---

## 13. Final Summary

The backend-solver integration is already in a good state for static and
semi-dynamic planning:

- the solver is connected to the real database
- route results are persisted
- stock reservation is applied
- urgent requests already trigger replanning

To reach real dynamic truck-aware dispatching, the next key addition is not a
new optimization formula first, but **live execution state**.

The most important server-side principle is:

```text
do not reroute abstract planned trucks
reroute only trucks whose real execution state is known
```

That is the main bridge between the current MVP and the future dynamic routing
logic.
