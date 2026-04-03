# Solver Technical Report

## 1. Purpose

This report documents the current delivery solver implementation based on
`TIMUR_SOLVER_GUIDE.md`, `CONCEPT.md`, `DATABASE.md`, and the CSV files in
`demo_data/`.

The solver covers both delivery legs:

- `Leg 1`: `FACTORY -> WAREHOUSE`
- `Leg 2`: `WAREHOUSE -> STORE`

The main production entrypoint is:

```bash
python -m solver --data demo_data --output solver_output
```

An equivalent wrapper is also available:

```bash
python run_solver.py --data demo_data --output solver_output
```

## 2. Module Structure

The implementation follows the modular split requested in the guide:

- `solver/io.py`
  - CSV loading for `nodes`, `edges`, `trucks`, `demand`, `warehouse_stock`,
    `products`, `settings`
  - CSV export for `routes`, `route_cargo`, `warehouse_stock`
- `solver/graph.py`
  - graph construction with mandatory undirected symmetrization
  - Dijkstra with `heapq`
  - shortest-path matrix
  - path reconstruction helpers
- `solver/priority.py`
  - demand priority recomputation
  - priority weights
- `solver/cost.py`
  - truck parameter resolution with `settings` fallbacks
  - `cost_per_km`
- `solver/assignment.py`
  - warehouse candidate selection
  - normalized order preparation
  - `Leg 1` on-demand replenishment planning
  - `Leg 2` greedy assignment
- `solver/enroute.py`
  - `detour_km`
  - `detour_ratio`
  - `savings`
  - insertion candidate evaluation
- `solver/routing.py`
  - nearest-neighbor route build
  - `2-opt`
  - route metrics and timeline
  - en-route insertion integration
  - final `solve_network()` pipeline
- `solver/cli.py`
  - command-line entrypoint
- `solver/__main__.py`
  - package execution via `python -m solver`

## 3. Implemented Logic

### 3.1 Input Normalization

- `edges.csv` is treated as an undirected graph.
- `trucks.csv` resolves `NULL` values via `settings.csv`.
- `demand.priority` is recomputed from `current_stock`, `min_stock`,
  `is_urgent`.
- `requested_qty` is treated as weight in kilograms for capacity checks.

### 3.2 Shortest Paths

- Dijkstra is implemented with `heapq`.
- The solver builds a shortest-path matrix for relevant nodes.
- `reconstruct_path()` returns shortest paths between two nodes.
- `reconstruct_route_path()` rebuilds the full graph path for a route if a map
  polyline is needed later.

### 3.3 Leg 2 Assignment

- Only `truck` and `van` are eligible.
- Orders are sorted by:
  - priority weight descending
  - requested quantity descending
  - stable ID ordering
- Greedy score:

```text
score = marginal_km * cost_per_km / priority_weight
```

- Only vehicles based at the selected warehouse depot may serve the order.
- Capacity is checked through `remaining_capacity_kg`.
- Warehouse stock is tracked through remaining available kilograms.

### 3.4 Leg 1 On-Demand Replenishment

Because the dataset has no `factory_stock`, the solver uses the guide's MVP
assumption:

- factories have infinite stock
- if total warehouse demand for a product exceeds available stock, a
  replenishment need is created
- only `semi` trucks are used
- the nearest reachable factory is selected

Multiple products for the same `factory -> warehouse` corridor can be grouped
into one or more semi trips, respecting truck capacity.

### 3.5 Routing

- Initial route: nearest-neighbor
- Improvement: `2-opt`
- Every route is forced into a round trip:

```text
[depot, ..., depot]
```

- Route metrics:
  - `total_km`
  - `drive_hours`
  - `unload_hours`
  - `total_elapsed_h`
  - `total_cost`
  - `arrival_time`
  - `timeline`

### 3.6 En-Route Insertions

The solver implements the guide's en-route logic:

- eligible types: `truck`, `van`
- minimum priority: controlled by `settings.min_priority_enroute`
- detour threshold: controlled by `settings.max_detour_ratio`
- checks remaining truck capacity
- checks remaining warehouse stock

Insertion candidate evaluation supports two cases:

1. the route already passes through the store on an existing shortest-path
   segment
2. the route can absorb the store with acceptable `detour_ratio`

The helper formulas are exposed in `solver/enroute.py`.

### 3.7 Output

The solver writes:

- `solver_output/routes.csv`
- `solver_output/route_cargo.csv`
- `solver_output/warehouse_stock.csv`

The CSV outputs contain only freshly computed active routes. This matches the
CSV workflow. The database-specific step `old routes.is_active = 0` should be
handled by the backend integration layer when writing to SQL instead of files.

## 4. Route Time and Warning Rules

The solver currently includes:

- unloading time per stop from `settings.unload_min_default`
- mandatory 45-minute break insertion after `4.5h` of continuous driving
- travel time based on `avg_speed_kmh`

Warning rule:

- if `total_elapsed_h > 12`, route status becomes:

```text
TIME_WARNING / WORK_TIME_EXCEEDED
```

## 5. Reserved Stock Semantics

The guide says that `reserved_kg` must increase by cargo that went into active
routes. In practice, `Leg 1` replenishment and `Leg 2` outbound reservation can
refer to the same demand pool.

Current solver rule:

- final `reserved_kg` is updated by the larger of:
  - `Leg 2` reserved outbound cargo
  - `Leg 1` replenishment quantity

This prevents double counting the same kilograms in the final CSV output.

## 6. Current Demo Data Behavior

With the current `demo_data`:

- the solver generates `5` routes
- `Leg 1` is available but not triggered by default data
- `6` orders remain unassigned because there is no `truck/van` based at
  `WAREHOUSE_3`

This is expected behavior under the present fleet topology.

## 7. Validation Summary

The implementation was validated by:

- end-to-end run on `demo_data`
- synthetic `Leg 1` shortage scenario
- synthetic en-route reinsertion scenario
- synthetic long-route timing scenario that triggers
  `TIME_WARNING / WORK_TIME_EXCEEDED`

## 8. Run Instructions

Default run:

```bash
python -m solver
```

Explicit input/output directories:

```bash
python -m solver --data demo_data --output solver_output
```

Using the wrapper script:

```bash
python run_solver.py --data demo_data --output solver_output
```

Optional flags:

- `--departure-time HH:MM`
- `--created-at "YYYY-MM-DD HH:MM:SS"`
- `--json-summary`

## 9. Known Limitations

- no real `factory_stock` table exists, so factory inventory is assumed
  infinite
- no order splitting across multiple warehouses
- no automatic multi-day route splitting, only warning/status marking
- no SQL persistence layer in this repository; outputs are CSV files

## 10. Recommended Next Steps

- add backend integration for route versioning and `is_active = 0`
- support optional demand splitting across warehouses
- support explicit factory inventory once `factory_stock` appears
- add unit tests for `Leg 1`, en-route insertion, and timing edge cases
