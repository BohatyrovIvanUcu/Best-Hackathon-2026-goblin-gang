from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from solver.routing import RoutePlan, compute_route_metrics, solve_network

from backend.db.execution import TRUCK_MUTABLE_STATUSES
from backend.db.helpers import connect
from backend.db.read_api import fetch_routes_data
from backend.db.solver_runtime import load_dynamic_solver_inputs_from_db, run_solver_and_persist


def mark_demand_as_urgent(
    database_path: Path,
    *,
    node_id: str,
    product_id: str,
    qty: float,
) -> dict[str, object]:
    if qty <= 0:
        raise ValueError("qty must be greater than 0")

    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT node_id, product_id, current_stock, min_stock, requested_qty, is_urgent
            FROM demand
            WHERE node_id = ? AND product_id = ?
            """,
            (node_id, product_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Demand row not found for node_id={node_id} and product_id={product_id}"
            )

        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        requested_qty_before = float(row["requested_qty"])
        requested_qty_after = round(requested_qty_before + qty, 2)

        connection.execute(
            """
            UPDATE demand
            SET requested_qty = ?, priority = 'CRITICAL', is_urgent = 1, updated_at = ?
            WHERE node_id = ? AND product_id = ?
            """,
            (
                requested_qty_after,
                updated_at,
                node_id,
                product_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "node_id": node_id,
        "product_id": product_id,
        "qty": qty,
        "requested_qty_before": requested_qty_before,
        "requested_qty_after": requested_qty_after,
        "priority": "CRITICAL",
        "is_urgent": True,
    }


def apply_urgent_request(
    database_path: Path,
    *,
    node_id: str,
    product_id: str,
    qty: float,
    departure_time: str | None = None,
) -> dict[str, object]:
    before_routes = fetch_routes_data(database_path)["routes"]
    urgent_update = mark_demand_as_urgent(
        database_path,
        node_id=node_id,
        product_id=product_id,
        qty=qty,
    )
    solve_result = run_solver_and_persist(
        database_path=database_path,
        departure_time=departure_time,
    )
    after_routes = solve_result["routes"]

    return {
        "status": "ok",
        "urgent_update": urgent_update,
        "diff": _build_route_diff(before_routes, after_routes),
        "solve": solve_result,
    }


def apply_reroute_request(
    database_path: Path,
    *,
    node_id: str,
    product_id: str,
    qty: float,
    departure_time: str | None = None,
    reroute_reason: str | None = None,
    allow_in_progress: bool = True,
) -> dict[str, object]:
    before_routes = fetch_routes_data(database_path)["routes"]
    urgent_update = mark_demand_as_urgent(
        database_path,
        node_id=node_id,
        product_id=product_id,
        qty=qty,
    )
    dynamic_inputs = load_dynamic_solver_inputs_from_db(database_path)
    solve_result = solve_network(
        dynamic_inputs.static_inputs,
        departure_time_override=departure_time,
    )
    route_changes = _build_reroute_changes(
        dynamic_inputs=dynamic_inputs,
        solve_result=solve_result,
        departure_time=departure_time,
        allow_in_progress=allow_in_progress,
    )
    persistence = _persist_reroute_changes(
        database_path=database_path,
        dynamic_inputs=dynamic_inputs,
        route_changes=route_changes,
    )
    after_routes = fetch_routes_data(database_path)["routes"]

    changed_truck_ids = sorted(
        truck_id
        for truck_id, change in route_changes.items()
        if change["mode"] in {"replace", "cancel"}
    )
    unchanged_truck_ids = sorted(
        truck_id
        for truck_id in dynamic_inputs.truck_states
        if truck_id not in changed_truck_ids
    )
    locked_prefix_by_route_id = {
        int(change["old_route_id"]): list(change["locked_prefix"])
        for change in route_changes.values()
        if change["mode"] == "replace" and change["locked_prefix"]
    }

    return {
        "status": "ok",
        "urgent_update": urgent_update,
        "reroute_reason": reroute_reason or "urgent_demand",
        "allow_in_progress": allow_in_progress,
        "changed_truck_ids": changed_truck_ids,
        "unchanged_truck_ids": unchanged_truck_ids,
        "route_id_mapping": persistence["route_id_mapping"],
        "locked_prefix_by_route_id": locked_prefix_by_route_id,
        "updated_cargo_distribution": persistence["updated_cargo_distribution"],
        "diff": _build_route_diff(before_routes, after_routes),
        "routes": after_routes,
    }


def _build_route_diff(
    before_routes: list[dict[str, object]],
    after_routes: list[dict[str, object]],
) -> dict[str, object]:
    before_map = {
        str(route["truck_id"]): (
            int(route["leg"]),
            tuple(route["stops"]),
        )
        for route in before_routes
    }
    after_map = {
        str(route["truck_id"]): (
            int(route["leg"]),
            tuple(route["stops"]),
        )
        for route in after_routes
    }

    before_ids = set(before_map)
    after_ids = set(after_map)
    changed_trucks = sorted(
        truck_id
        for truck_id in before_ids & after_ids
        if before_map[truck_id] != after_map[truck_id]
    )

    return {
        "before_total_routes": len(before_routes),
        "after_total_routes": len(after_routes),
        "added_trucks": sorted(after_ids - before_ids),
        "removed_trucks": sorted(before_ids - after_ids),
        "changed_trucks": changed_trucks,
    }


def _build_reroute_changes(
    *,
    dynamic_inputs,
    solve_result,
    departure_time: str | None,
    allow_in_progress: bool,
) -> dict[str, dict[str, object]]:
    active_route_by_truck = {
        truck_id: dynamic_inputs.active_routes[truck_state.active_route_id]
        for truck_id, truck_state in dynamic_inputs.truck_states.items()
        if truck_state.active_route_id is not None and truck_state.active_route_id in dynamic_inputs.active_routes
    }
    new_plans_by_truck: dict[str, list[RoutePlan]] = defaultdict(list)
    for route_plan in solve_result.route_plans.values():
        new_plans_by_truck[route_plan.truck_id].append(route_plan)
    for truck_id in new_plans_by_truck:
        new_plans_by_truck[truck_id].sort(key=lambda plan: (plan.leg, list(plan.stops)))

    route_changes: dict[str, dict[str, object]] = {}
    for truck_id, truck_state in dynamic_inputs.truck_states.items():
        active_route = active_route_by_truck.get(truck_id)
        if active_route is None:
            continue

        route_execution = dynamic_inputs.route_execution.get(active_route.route_id)
        if route_execution is None:
            continue

        new_plan = _pick_new_plan_for_truck(
            active_route=active_route,
            candidate_plans=new_plans_by_truck.get(truck_id, []),
        )
        locked_prefix = active_route.stops[: route_execution.last_completed_stop_index + 1]

        if truck_state.status == "en_route" and allow_in_progress and new_plan is not None:
            merged_plan = _build_locked_prefix_route_plan(
                dynamic_inputs=dynamic_inputs,
                active_route=active_route,
                route_execution=route_execution,
                new_plan=new_plan,
                departure_time=departure_time,
                distances_by_source=solve_result.distances,
            )
            if merged_plan is not None and _route_plan_changed(active_route, merged_plan):
                route_changes[truck_id] = {
                    "mode": "replace",
                    "old_route_id": active_route.route_id,
                    "new_route_plan": merged_plan,
                    "locked_prefix": locked_prefix,
                    "truck_status_before": truck_state.status,
                    "route_status_before": route_execution.status,
                }
            continue

        if truck_state.status not in TRUCK_MUTABLE_STATUSES:
            continue

        if new_plan is None:
            route_changes[truck_id] = {
                "mode": "cancel",
                "old_route_id": active_route.route_id,
                "new_route_plan": None,
                "locked_prefix": locked_prefix,
                "truck_status_before": truck_state.status,
                "route_status_before": route_execution.status,
            }
            continue

        if _route_plan_changed(active_route, new_plan):
            route_changes[truck_id] = {
                "mode": "replace",
                "old_route_id": active_route.route_id,
                "new_route_plan": new_plan,
                "locked_prefix": locked_prefix,
                "truck_status_before": truck_state.status,
                "route_status_before": route_execution.status,
            }

    return route_changes


def _persist_reroute_changes(
    *,
    database_path: Path,
    dynamic_inputs,
    route_changes: dict[str, dict[str, object]],
) -> dict[str, object]:
    if not route_changes:
        return {
            "route_id_mapping": {},
            "updated_cargo_distribution": {},
        }

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        route_id_mapping: dict[int, int] = {}
        updated_cargo_distribution: dict[str, list[dict[str, object]]] = {}

        for truck_id, change in sorted(route_changes.items()):
            old_route_id = int(change["old_route_id"])
            connection.execute(
                "UPDATE routes SET is_active = 0 WHERE id = ?",
                (old_route_id,),
            )
            connection.execute(
                """
                UPDATE route_execution
                SET status = 'cancelled', updated_at = ?
                WHERE route_id = ?
                """,
                (timestamp, old_route_id),
            )

            if change["mode"] == "cancel":
                _reset_truck_state_after_cancellation(
                    connection,
                    truck_id=truck_id,
                    updated_at=timestamp,
                )
                updated_cargo_distribution[truck_id] = []
                _apply_reserved_stock_delta(
                    connection,
                    dynamic_inputs=dynamic_inputs,
                    route_id=old_route_id,
                    new_route_plan=None,
                )
                continue

            new_route_plan = change["new_route_plan"]
            new_route_id = _insert_route_plan(
                connection,
                route_plan=new_route_plan,
                supersedes_route_id=old_route_id,
            )
            route_id_mapping[old_route_id] = new_route_id
            _insert_route_cargo_rows(connection, route_id=new_route_id, route_plan=new_route_plan)
            _seed_rerouted_execution_state(
                connection,
                dynamic_inputs=dynamic_inputs,
                truck_id=truck_id,
                old_route_id=old_route_id,
                new_route_id=new_route_id,
                change=change,
                updated_at=timestamp,
            )
            updated_cargo_distribution[truck_id] = [
                {
                    "stop_node_id": stop_node_id,
                    "product_id": product_id,
                    "qty_kg": round(qty_kg, 2),
                }
                for (stop_node_id, product_id), qty_kg in sorted(
                    new_route_plan.cargo_by_stop_product.items(),
                    key=lambda item: (item[0][0], item[0][1]),
                )
            ]
            _apply_reserved_stock_delta(
                connection,
                dynamic_inputs=dynamic_inputs,
                route_id=old_route_id,
                new_route_plan=new_route_plan,
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "route_id_mapping": route_id_mapping,
        "updated_cargo_distribution": updated_cargo_distribution,
    }


def _pick_new_plan_for_truck(*, active_route, candidate_plans: list[RoutePlan]) -> RoutePlan | None:
    if not candidate_plans:
        return None

    same_leg = [plan for plan in candidate_plans if int(plan.leg) == int(active_route.leg)]
    if same_leg:
        return same_leg[0]
    return candidate_plans[0]


def _build_locked_prefix_route_plan(
    *,
    dynamic_inputs,
    active_route,
    route_execution,
    new_plan: RoutePlan,
    departure_time: str | None,
    distances_by_source,
) -> RoutePlan | None:
    truck = dynamic_inputs.static_inputs.trucks.get(active_route.truck_id)
    if truck is None:
        return None

    locked_prefix = list(active_route.stops[: route_execution.last_completed_stop_index + 1])
    remaining_current_stops = list(active_route.stops[route_execution.next_stop_index : -1])
    if not remaining_current_stops:
        return None

    remaining_set = set(remaining_current_stops)
    reordered_stops = [
        stop_node_id
        for stop_node_id in new_plan.stops[1:-1]
        if stop_node_id in remaining_set
    ]
    for stop_node_id in remaining_current_stops:
        if stop_node_id not in reordered_stops:
            reordered_stops.append(stop_node_id)

    new_stops = tuple(locked_prefix + reordered_stops + [active_route.stops[-1]])
    if new_stops == active_route.stops:
        return None

    stop_notes = _build_stop_notes(active_route.cargo_by_stop_product)
    (
        total_km,
        drive_hours,
        unload_hours,
        total_elapsed_h,
        total_cost,
        departure_time_value,
        arrival_time,
        days,
        time_status,
        time_warning,
        timeline,
    ) = compute_route_metrics(
        new_stops,
        truck,
        dynamic_inputs.static_inputs.settings,
        distances_by_source,
        stop_notes=stop_notes,
        departure_time_override=departure_time or new_plan.departure_time,
    )

    return RoutePlan(
        truck_id=active_route.truck_id,
        leg=active_route.leg,
        stops=new_stops,
        total_km=total_km,
        drive_hours=drive_hours,
        unload_hours=unload_hours,
        total_elapsed_h=total_elapsed_h,
        total_cost=total_cost,
        days=days,
        departure_time=departure_time_value,
        arrival_time=arrival_time,
        time_status=time_status,
        time_warning=time_warning,
        timeline=timeline,
        cargo_by_stop_product=dict(active_route.cargo_by_stop_product),
        stop_priority_by_node={},
    )


def _route_plan_changed(active_route, new_plan: RoutePlan) -> bool:
    return tuple(active_route.stops) != tuple(new_plan.stops) or dict(active_route.cargo_by_stop_product) != dict(
        new_plan.cargo_by_stop_product
    )


def _insert_route_plan(connection, *, route_plan: RoutePlan, supersedes_route_id: int) -> int:
    cursor = connection.execute(
        """
        INSERT INTO routes(
            truck_id,
            supersedes_route_id,
            leg,
            stops,
            total_km,
            total_cost,
            drive_hours,
            total_elapsed_h,
            days,
            departure_time,
            arrival_time,
            time_status,
            time_warning,
            timeline,
            created_at,
            is_active
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            route_plan.truck_id,
            supersedes_route_id,
            route_plan.leg,
            json.dumps(list(route_plan.stops)),
            route_plan.total_km,
            route_plan.total_cost,
            route_plan.drive_hours,
            route_plan.total_elapsed_h,
            route_plan.days,
            route_plan.departure_time,
            route_plan.arrival_time,
            route_plan.time_status,
            route_plan.time_warning,
            json.dumps(
                [
                    {
                        "time": event.time,
                        "event": event.event,
                        "node_id": event.node_id,
                        "note": event.note,
                    }
                    for event in route_plan.timeline
                ],
                ensure_ascii=False,
            ),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return int(cursor.lastrowid)


def _insert_route_cargo_rows(connection, *, route_id: int, route_plan: RoutePlan) -> None:
    connection.executemany(
        """
        INSERT INTO route_cargo(route_id, stop_node_id, product_id, qty_kg)
        VALUES(?, ?, ?, ?)
        """,
        [
            (
                route_id,
                stop_node_id,
                product_id,
                round(qty_kg, 2),
            )
            for (stop_node_id, product_id), qty_kg in sorted(
                route_plan.cargo_by_stop_product.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
        ],
    )


def _seed_rerouted_execution_state(
    connection,
    *,
    dynamic_inputs,
    truck_id: str,
    old_route_id: int,
    new_route_id: int,
    change: dict[str, object],
    updated_at: str,
) -> None:
    new_route_plan: RoutePlan = change["new_route_plan"]
    truck_state = dynamic_inputs.truck_states[truck_id]
    route_execution = dynamic_inputs.route_execution.get(old_route_id)
    truck = dynamic_inputs.static_inputs.trucks[truck_id]

    if truck_state.status == "en_route" and route_execution is not None:
        connection.execute(
            """
            INSERT INTO route_execution(
                route_id,
                status,
                last_completed_stop_index,
                next_stop_index,
                started_at,
                completed_at,
                updated_at
            )
            VALUES(?, 'in_progress', ?, ?, ?, NULL, ?)
            """,
            (
                new_route_id,
                route_execution.last_completed_stop_index,
                route_execution.next_stop_index,
                route_execution.started_at.isoformat(sep=" ") if route_execution.started_at else None,
                updated_at,
            ),
        )
        old_cargo_state = {
            (state.stop_node_id, state.product_id): state
            for key, state in dynamic_inputs.route_cargo_state.items()
            if key[0] == old_route_id
        }
        connection.executemany(
            """
            INSERT INTO route_cargo_state(
                route_id,
                stop_node_id,
                product_id,
                qty_reserved_kg,
                qty_loaded_kg,
                qty_delivered_kg
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_route_id,
                    stop_node_id,
                    product_id,
                    state.qty_reserved_kg,
                    state.qty_loaded_kg,
                    state.qty_delivered_kg,
                )
                for (stop_node_id, product_id), state in sorted(old_cargo_state.items())
            ],
        )
        connection.execute(
            """
            UPDATE truck_state
            SET
                active_route_id = ?,
                last_completed_stop_index = ?,
                updated_at = ?
            WHERE truck_id = ?
            """,
            (
                new_route_id,
                route_execution.last_completed_stop_index,
                updated_at,
                truck_id,
            ),
        )
        return

    route_status = "loading" if truck_state.status in {"loading", "loaded"} else "planned"
    next_stop_index = 1 if len(new_route_plan.stops) > 1 else None
    connection.execute(
        """
        INSERT INTO route_execution(
            route_id,
            status,
            last_completed_stop_index,
            next_stop_index,
            started_at,
            completed_at,
            updated_at
        )
        VALUES(?, ?, 0, ?, NULL, NULL, ?)
        """,
        (
            new_route_id,
            route_status,
            next_stop_index,
            updated_at,
        ),
    )

    old_cargo_signature = dict(dynamic_inputs.active_routes[old_route_id].cargo_by_stop_product)
    new_cargo_signature = dict(new_route_plan.cargo_by_stop_product)
    preserve_loaded_state = truck_state.status == "loaded" and old_cargo_signature == new_cargo_signature

    cargo_state_rows = []
    total_loaded_kg = 0.0
    for (stop_node_id, product_id), qty_kg in sorted(
        new_route_plan.cargo_by_stop_product.items(),
        key=lambda item: (item[0][0], item[0][1]),
    ):
        qty_reserved_kg = 0.0 if preserve_loaded_state else round(qty_kg, 2)
        qty_loaded_kg = round(qty_kg, 2) if preserve_loaded_state else 0.0
        total_loaded_kg += qty_loaded_kg
        cargo_state_rows.append(
            (
                new_route_id,
                stop_node_id,
                product_id,
                qty_reserved_kg,
                qty_loaded_kg,
                0.0,
            )
        )
    if cargo_state_rows:
        connection.executemany(
            """
            INSERT INTO route_cargo_state(
                route_id,
                stop_node_id,
                product_id,
                qty_reserved_kg,
                qty_loaded_kg,
                qty_delivered_kg
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            cargo_state_rows,
        )

    next_truck_status = (
        "loaded"
        if preserve_loaded_state
        else ("loading" if truck_state.status in {"loading", "loaded"} else "idle")
    )
    remaining_capacity_kg = (
        round(max(0.0, truck.capacity_kg - total_loaded_kg), 2)
        if preserve_loaded_state
        else truck.capacity_kg
    )
    connection.execute(
        """
        UPDATE truck_state
        SET
            status = ?,
            active_route_id = ?,
            current_node_id = ?,
            last_completed_stop_index = 0,
            remaining_capacity_kg = ?,
            updated_at = ?
        WHERE truck_id = ?
        """,
        (
            next_truck_status,
            new_route_id,
            truck.depot_node_id,
            remaining_capacity_kg,
            updated_at,
            truck_id,
        ),
    )


def _reset_truck_state_after_cancellation(connection, *, truck_id: str, updated_at: str) -> None:
    truck_row = connection.execute(
        """
        SELECT depot_node_id, capacity_kg
        FROM trucks
        WHERE id = ?
        """,
        (truck_id,),
    ).fetchone()
    if truck_row is None:
        raise LookupError(f"Truck not found for truck_id={truck_id}")

    connection.execute(
        """
        UPDATE truck_state
        SET
            status = 'idle',
            active_route_id = NULL,
            current_node_id = ?,
            last_completed_stop_index = 0,
            remaining_capacity_kg = ?,
            updated_at = ?
        WHERE truck_id = ?
        """,
        (
            truck_row["depot_node_id"],
            float(truck_row["capacity_kg"]),
            updated_at,
            truck_id,
        ),
    )


def _apply_reserved_stock_delta(connection, *, dynamic_inputs, route_id: int, new_route_plan: RoutePlan | None) -> None:
    active_route = dynamic_inputs.active_routes.get(route_id)
    if active_route is None:
        return

    truck = dynamic_inputs.static_inputs.trucks.get(active_route.truck_id)
    if truck is None:
        return
    depot_node = dynamic_inputs.static_inputs.nodes.get(truck.depot_node_id)
    if depot_node is None or depot_node.type != "warehouse":
        return

    old_totals: dict[str, float] = defaultdict(float)
    for (_stop_node_id, product_id), qty_kg in active_route.cargo_by_stop_product.items():
        old_totals[product_id] += float(qty_kg)

    new_totals: dict[str, float] = defaultdict(float)
    if new_route_plan is not None:
        for (_stop_node_id, product_id), qty_kg in new_route_plan.cargo_by_stop_product.items():
            new_totals[product_id] += float(qty_kg)

    all_product_ids = sorted(set(old_totals) | set(new_totals))
    for product_id in all_product_ids:
        delta = round(new_totals.get(product_id, 0.0) - old_totals.get(product_id, 0.0), 2)
        if abs(delta) < 1e-9:
            continue
        connection.execute(
            """
            UPDATE warehouse_stock
            SET reserved_kg = ROUND(MAX(0, reserved_kg + ?), 2)
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (delta, truck.depot_node_id, product_id),
        )


def _build_stop_notes(cargo_by_stop_product: dict[tuple[str, str], float]) -> dict[str, str]:
    qty_by_stop: dict[str, float] = defaultdict(float)
    for (stop_node_id, _product_id), qty_kg in cargo_by_stop_product.items():
        qty_by_stop[stop_node_id] += float(qty_kg)
    return {
        stop_node_id: f"{round(total_qty_kg, 2)} kg planned"
        for stop_node_id, total_qty_kg in qty_by_stop.items()
    }
