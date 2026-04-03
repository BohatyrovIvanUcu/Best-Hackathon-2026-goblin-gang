from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from .assignment import (
    AssignedOrder,
    Leg1PlanResult,
    Leg2AssignmentResult,
    TruckAssignment,
    UnassignedOrder,
    assign_leg1_simple,
    assign_leg2,
    build_normalized_orders,
    normalized_order_from_unassigned,
)
from .cost import compute_cost_per_km, resolve_truck_cost_params
from .enroute import try_insert_candidate
from .graph import DistanceMatrix, PredecessorMatrix, build_distance_matrix
from .io import DemandKey, SettingsValue, SolverInputs, Truck, WarehouseStockRecord
from .priority import priority_weight

BREAK_AFTER_H = 4.5
BREAK_MIN = 45
DAILY_LIMIT_H = 9.0
WORK_TIME_LIMIT_H = 12.0


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    time: str
    event: str
    node_id: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class RoutePlan:
    truck_id: str
    leg: int
    stops: tuple[str, ...]
    total_km: float
    drive_hours: float
    unload_hours: float
    total_elapsed_h: float
    total_cost: float
    days: int
    departure_time: str
    arrival_time: str
    time_status: str
    time_warning: str | None
    timeline: tuple[TimelineEvent, ...]
    cargo_by_stop_product: dict[tuple[str, str], float]
    stop_priority_by_node: dict[str, str]


@dataclass(frozen=True, slots=True)
class EnRouteInsertion:
    unassigned_index: int
    order: UnassignedOrder
    insert_after_index: int | None
    detour_km: float
    detour_ratio: float
    savings: float


@dataclass(frozen=True, slots=True)
class SolveResult:
    relevant_nodes: tuple[str, ...]
    distances: DistanceMatrix
    predecessors: PredecessorMatrix
    leg1_plan: Leg1PlanResult
    assignment: Leg2AssignmentResult
    route_plans: dict[str, RoutePlan]
    routes_table: list[dict[str, object]]
    route_cargo_table: list[dict[str, object]]
    warehouse_stock_table: list[dict[str, object]]


Leg2SolveResult = SolveResult


def greedy_tsp(
    depot_node_id: str,
    stop_node_ids: Iterable[str],
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> list[str]:
    unvisited = set(stop_node_ids)
    route = [depot_node_id]
    current = depot_node_id

    while unvisited:
        next_stop = min(
            unvisited,
            key=lambda candidate: (
                _distance_between(current, candidate, distances_by_source),
                candidate,
            ),
        )
        route.append(next_stop)
        unvisited.remove(next_stop)
        current = next_stop

    route.append(depot_node_id)
    return route


def two_opt(
    route: list[str],
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> list[str]:
    if len(route) < 5:
        return route[:]

    best_route = route[:]
    best_distance = compute_route_km(best_route, distances_by_source)
    improved = True

    while improved:
        improved = False
        for i in range(1, len(best_route) - 2):
            for k in range(i + 1, len(best_route) - 1):
                candidate_route = _two_opt_swap(best_route, i, k)
                candidate_distance = compute_route_km(candidate_route, distances_by_source)
                if candidate_distance + 1e-9 < best_distance:
                    best_route = candidate_route
                    best_distance = candidate_distance
                    improved = True
                    break
            if improved:
                break

    return best_route


def ensure_round_trip(route: Iterable[str], depot_node_id: str) -> list[str]:
    route_nodes = list(route)
    if not route_nodes:
        return [depot_node_id, depot_node_id]
    if route_nodes[0] != depot_node_id:
        route_nodes.insert(0, depot_node_id)
    if route_nodes[-1] != depot_node_id:
        route_nodes.append(depot_node_id)
    return route_nodes


def compute_route_km(
    route: Iterable[str],
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> float:
    route_nodes = list(route)
    return round(
        sum(
            _distance_between(route_nodes[index], route_nodes[index + 1], distances_by_source)
            for index in range(len(route_nodes) - 1)
        ),
        2,
    )


def compute_route_metrics(
    route: Iterable[str],
    truck: Truck,
    settings: Mapping[str, SettingsValue],
    distances_by_source: Mapping[str, Mapping[str, float]],
    stop_notes: Mapping[str, str] | None = None,
    departure_time_override: time | str | None = None,
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    str,
    str,
    int,
    str,
    str | None,
    tuple[TimelineEvent, ...],
]:
    route_nodes = ensure_round_trip(route, truck.depot_node_id)
    cost_params = resolve_truck_cost_params(truck, settings)
    total_km = compute_route_km(route_nodes, distances_by_source)
    drive_hours = round(total_km / cost_params.avg_speed_kmh, 2)
    unload_min = _resolve_unload_minutes(settings)
    unload_hours = round(max(len(route_nodes) - 2, 0) * unload_min / 60.0, 2)
    timeline, arrival_time, total_elapsed_h = build_timeline(
        route_nodes,
        distances_by_source,
        departure_time_override or settings.get("departure_time_default"),
        cost_params.avg_speed_kmh,
        unload_min,
        stop_notes=stop_notes,
    )
    total_cost = round(total_km * compute_cost_per_km(truck, settings), 2)
    days, time_status, time_warning = classify_route_timing(drive_hours, total_elapsed_h)

    return (
        total_km,
        drive_hours,
        unload_hours,
        total_elapsed_h,
        total_cost,
        _format_time(
            _resolve_departure_time(departure_time_override or settings.get("departure_time_default"))
        ),
        arrival_time,
        days,
        time_status,
        time_warning,
        timeline,
    )


def build_timeline(
    route: Iterable[str],
    distances_by_source: Mapping[str, Mapping[str, float]],
    departure_time_value: time | str | object | None,
    avg_speed_kmh: float,
    unload_min: int,
    stop_notes: Mapping[str, str] | None = None,
) -> tuple[tuple[TimelineEvent, ...], str, float]:
    route_nodes = list(route)
    departure_clock = _resolve_departure_time(departure_time_value)
    departure_dt = datetime.combine(date(2000, 1, 1), departure_clock)
    current_time = departure_dt
    driven_since_break = 0.0
    notes_by_stop = dict(stop_notes or {})
    timeline: list[TimelineEvent] = [
        TimelineEvent(
            time=_format_time(current_time.time()),
            event="departure",
            node_id=route_nodes[0],
            note=None,
        )
    ]

    for index in range(len(route_nodes) - 1):
        source = route_nodes[index]
        target = route_nodes[index + 1]
        segment_km = _distance_between(source, target, distances_by_source)
        segment_hours = segment_km / avg_speed_kmh
        remaining_segment_hours = segment_hours

        while driven_since_break + remaining_segment_hours > BREAK_AFTER_H:
            drivable_before_break = BREAK_AFTER_H - driven_since_break
            if drivable_before_break > 1e-9:
                current_time += timedelta(hours=drivable_before_break)
                remaining_segment_hours -= drivable_before_break

            current_time += timedelta(minutes=BREAK_MIN)
            timeline.append(
                TimelineEvent(
                    time=_format_time(current_time.time()),
                    event="break",
                    node_id=None,
                    note="Mandatory 45 min break (EC 561/2006)",
                )
            )
            driven_since_break = 0.0

        current_time += timedelta(hours=remaining_segment_hours)
        driven_since_break += remaining_segment_hours
        is_last_segment = index == len(route_nodes) - 2
        timeline.append(
            TimelineEvent(
                time=_format_time(current_time.time()),
                event="return" if is_last_segment else "arrival",
                node_id=target,
                note=None if is_last_segment else notes_by_stop.get(target),
            )
        )

        if not is_last_segment:
            current_time += timedelta(minutes=unload_min)
            timeline.append(
                TimelineEvent(
                    time=_format_time(current_time.time()),
                    event="departure",
                    node_id=target,
                    note=f"{unload_min} min unloading",
                )
            )

    total_elapsed_h = round((current_time - departure_dt).total_seconds() / 3600.0, 2)
    return tuple(timeline), _format_time(current_time.time()), total_elapsed_h


def classify_drive_time(drive_hours: float) -> tuple[int, str, str | None]:
    if drive_hours <= BREAK_AFTER_H:
        return 1, "ok", None
    if drive_hours <= DAILY_LIMIT_H:
        return 1, "warning", "Mandatory 45 min break included in timeline"
    if drive_hours <= DAILY_LIMIT_H * 2:
        return 2, "multiday", "Two-day trip requires overnight rest between days"
    return 3, "multiday", "Trip exceeds two driving days and needs dispatcher review"


def classify_route_timing(
    drive_hours: float,
    total_elapsed_h: float,
) -> tuple[int, str, str | None]:
    days, time_status, time_warning = classify_drive_time(drive_hours)
    days = max(days, math.ceil(total_elapsed_h / WORK_TIME_LIMIT_H) if total_elapsed_h > 0 else 1)
    if total_elapsed_h > WORK_TIME_LIMIT_H:
        return days, "TIME_WARNING", "WORK_TIME_EXCEEDED"
    return days, time_status, time_warning


def build_leg2_routes(
    assignment_result: Leg2AssignmentResult,
    distances_by_source: Mapping[str, Mapping[str, float]],
    settings: Mapping[str, SettingsValue],
    departure_time_override: time | str | None = None,
    *,
    predecessors_by_source: PredecessorMatrix | None = None,
) -> dict[str, RoutePlan]:
    route_plans: dict[str, RoutePlan] = {}

    for truck_id, truck_plan in sorted(assignment_result.truck_plans.items()):
        if not truck_plan.assigned_orders:
            continue

        route_nodes = greedy_tsp(
            truck_plan.depot_node_id,
            sorted(truck_plan.visited_stores),
            distances_by_source,
        )
        route_nodes = two_opt(route_nodes, distances_by_source)
        route_nodes = apply_enroute_insertions(
            route_nodes,
            truck_plan,
            assignment_result,
            settings,
            distances_by_source,
            predecessors_by_source=predecessors_by_source,
        )
        route_nodes = two_opt(route_nodes, distances_by_source)
        route_nodes = ensure_round_trip(route_nodes, truck_plan.depot_node_id)
        truck_plan.assigned_stops = list(route_nodes[1:-1])

        stop_priority_by_node = _build_stop_priority_map(truck_plan)
        stop_notes = _build_leg2_stop_notes(truck_plan, stop_priority_by_node)
        (
            total_km,
            drive_hours,
            unload_hours,
            total_elapsed_h,
            total_cost,
            departure_time,
            arrival_time,
            days,
            time_status,
            time_warning,
            timeline,
        ) = compute_route_metrics(
            route_nodes,
            truck_plan.truck,
            settings,
            distances_by_source,
            stop_notes=stop_notes,
            departure_time_override=departure_time_override,
        )

        route_plans[f"LEG2:{truck_id}"] = RoutePlan(
            truck_id=truck_id,
            leg=2,
            stops=tuple(route_nodes),
            total_km=total_km,
            drive_hours=drive_hours,
            unload_hours=unload_hours,
            total_elapsed_h=total_elapsed_h,
            total_cost=total_cost,
            days=days,
            departure_time=departure_time,
            arrival_time=arrival_time,
            time_status=time_status,
            time_warning=time_warning,
            timeline=timeline,
            cargo_by_stop_product=dict(truck_plan.cargo_by_store_product),
            stop_priority_by_node=stop_priority_by_node,
        )

    return route_plans


def build_leg1_routes(
    leg1_plan: Leg1PlanResult,
    trucks: Mapping[str, Truck],
    settings: Mapping[str, SettingsValue],
    distances_by_source: Mapping[str, Mapping[str, float]],
    departure_time_override: time | str | None = None,
) -> dict[str, RoutePlan]:
    route_plans: dict[str, RoutePlan] = {}

    for trip in sorted(leg1_plan.trips, key=lambda item: (item.truck_id, item.trip_no, item.warehouse_id)):
        truck = trucks[trip.truck_id]
        route_nodes = ensure_round_trip([trip.factory_id, trip.warehouse_id], truck.depot_node_id)
        total_qty = round(sum(trip.cargo_by_product.values()), 2)
        stop_notes = {trip.warehouse_id: f"REPLENISH {_format_qty(total_qty)} kg"}
        (
            total_km,
            drive_hours,
            unload_hours,
            total_elapsed_h,
            total_cost,
            departure_time,
            arrival_time,
            days,
            time_status,
            time_warning,
            timeline,
        ) = compute_route_metrics(
            route_nodes,
            truck,
            settings,
            distances_by_source,
            stop_notes=stop_notes,
            departure_time_override=departure_time_override,
        )

        route_key = f"LEG1:{trip.truck_id}:{trip.trip_no}:{trip.warehouse_id}"
        route_plans[route_key] = RoutePlan(
            truck_id=trip.truck_id,
            leg=1,
            stops=tuple(route_nodes),
            total_km=total_km,
            drive_hours=drive_hours,
            unload_hours=unload_hours,
            total_elapsed_h=total_elapsed_h,
            total_cost=total_cost,
            days=days,
            departure_time=departure_time,
            arrival_time=arrival_time,
            time_status=time_status,
            time_warning=time_warning,
            timeline=timeline,
            cargo_by_stop_product={
                (trip.warehouse_id, product_id): round(qty_kg, 2)
                for product_id, qty_kg in trip.cargo_by_product.items()
            },
            stop_priority_by_node={},
        )

    return route_plans


def apply_enroute_insertions(
    route_nodes: list[str],
    truck_plan: TruckAssignment,
    assignment_result: Leg2AssignmentResult,
    settings: Mapping[str, SettingsValue],
    distances_by_source: Mapping[str, Mapping[str, float]],
    *,
    predecessors_by_source: PredecessorMatrix | None = None,
) -> list[str]:
    if truck_plan.truck.type not in {"truck", "van"}:
        return route_nodes

    min_priority = _resolve_min_priority_enroute(settings)
    max_detour_ratio = _resolve_max_detour_ratio(settings)

    while True:
        base_route_km = max(compute_route_km(route_nodes, distances_by_source), 1e-9)
        best_insertion: EnRouteInsertion | None = None

        for order_index, order in enumerate(assignment_result.unassigned_orders):
            insertion = _evaluate_enroute_insertion(
                order_index,
                order,
                route_nodes,
                truck_plan,
                assignment_result,
                min_priority,
                max_detour_ratio,
                base_route_km,
                distances_by_source,
                predecessors_by_source,
            )
            if insertion is None:
                continue

            if best_insertion is None or _compare_enroute_candidates(insertion, best_insertion):
                best_insertion = insertion

        if best_insertion is None:
            break

        route_nodes = _apply_enroute_insertion(
            best_insertion,
            route_nodes,
            truck_plan,
            assignment_result,
        )

    return route_nodes


def build_output_tables(
    route_plans: Mapping[str, RoutePlan] | Iterable[RoutePlan],
    created_at: datetime | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    timestamp = (created_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    routes_table: list[dict[str, object]] = []
    route_cargo_table: list[dict[str, object]] = []
    route_items = route_plans.values() if isinstance(route_plans, Mapping) else route_plans

    for route_id, route_plan in enumerate(route_items, start=1):
        routes_table.append(
            {
                "id": route_id,
                "truck_id": route_plan.truck_id,
                "leg": route_plan.leg,
                "stops": json.dumps(list(route_plan.stops)),
                "total_km": route_plan.total_km,
                "total_cost": route_plan.total_cost,
                "drive_hours": route_plan.drive_hours,
                "total_elapsed_h": route_plan.total_elapsed_h,
                "days": route_plan.days,
                "departure_time": route_plan.departure_time,
                "arrival_time": route_plan.arrival_time,
                "time_status": route_plan.time_status,
                "time_warning": route_plan.time_warning,
                "timeline": json.dumps(
                    [asdict(event) for event in route_plan.timeline],
                    ensure_ascii=False,
                ),
                "created_at": timestamp,
                "is_active": 1,
            }
        )

        stop_order = {node_id: index for index, node_id in enumerate(route_plan.stops)}
        for (stop_node_id, product_id), qty_kg in sorted(
            route_plan.cargo_by_stop_product.items(),
            key=lambda item: (
                stop_order.get(item[0][0], math.inf),
                item[0][0],
                item[0][1],
            ),
        ):
            route_cargo_table.append(
                {
                    "route_id": route_id,
                    "stop_node_id": stop_node_id,
                    "product_id": product_id,
                    "qty_kg": round(qty_kg, 2),
                }
            )

    return routes_table, route_cargo_table


def apply_reserved_stock_updates(
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    reserved_stock_delta_kg: Mapping[tuple[str, str], float],
    replenishment_kg: Mapping[tuple[str, str], float] | None = None,
) -> list[dict[str, object]]:
    updated_rows: list[dict[str, object]] = []
    replenishment_kg = replenishment_kg or {}

    for key, stock_record in sorted(warehouse_stock.items()):
        # Leg 1 replenishment and Leg 2 outbound reservations can map to the
        # same demand pool. Keep the larger active commitment to avoid
        # double-counting the same kilograms in reserved_kg.
        delta = max(
            reserved_stock_delta_kg.get(key, 0.0),
            replenishment_kg.get(key, 0.0),
        )
        updated_rows.append(
            {
                "warehouse_id": stock_record.warehouse_id,
                "product_id": stock_record.product_id,
                "quantity_kg": stock_record.quantity_kg,
                "reserved_kg": round(stock_record.reserved_kg + delta, 2),
            }
        )

    return updated_rows


def solve_network(
    solver_inputs: SolverInputs,
    departure_time_override: time | str | None = None,
    created_at: datetime | None = None,
) -> SolveResult:
    relevant_nodes = _collect_solver_relevant_nodes(solver_inputs)
    distances, predecessors = build_distance_matrix(
        solver_inputs.graph,
        relevant_nodes,
    )
    normalized_orders, shortages = build_normalized_orders(
        solver_inputs.demand,
        solver_inputs.warehouse_stock,
        distances,
        graph=solver_inputs.graph,
    )
    leg1_plan = assign_leg1_simple(
        normalized_orders,
        solver_inputs.warehouse_stock,
        solver_inputs.trucks,
        distances,
        graph=solver_inputs.graph,
    )
    assignment_result = assign_leg2(
        normalized_orders,
        solver_inputs.trucks,
        solver_inputs.warehouse_stock,
        distances,
        solver_inputs.settings,
        graph=solver_inputs.graph,
        preexisting_unassigned=shortages,
        stock_replenishment_kg=leg1_plan.replenishment_by_warehouse_product,
    )
    leg2_route_plans = build_leg2_routes(
        assignment_result,
        distances,
        solver_inputs.settings,
        departure_time_override=departure_time_override,
        predecessors_by_source=predecessors,
    )
    leg1_route_plans = build_leg1_routes(
        leg1_plan,
        solver_inputs.trucks,
        solver_inputs.settings,
        distances,
        departure_time_override=departure_time_override,
    )

    route_plans: dict[str, RoutePlan] = {}
    route_plans.update(leg1_route_plans)
    route_plans.update(leg2_route_plans)

    routes_table, route_cargo_table = build_output_tables(route_plans, created_at=created_at)
    warehouse_stock_table = apply_reserved_stock_updates(
        solver_inputs.warehouse_stock,
        assignment_result.reserved_stock_delta_kg,
        replenishment_kg=leg1_plan.replenishment_by_warehouse_product,
    )

    return SolveResult(
        relevant_nodes=tuple(relevant_nodes),
        distances=distances,
        predecessors=predecessors,
        leg1_plan=leg1_plan,
        assignment=assignment_result,
        route_plans=route_plans,
        routes_table=routes_table,
        route_cargo_table=route_cargo_table,
        warehouse_stock_table=warehouse_stock_table,
    )


def solve_leg2(
    solver_inputs: SolverInputs,
    departure_time_override: time | str | None = None,
    created_at: datetime | None = None,
) -> SolveResult:
    return solve_network(
        solver_inputs,
        departure_time_override=departure_time_override,
        created_at=created_at,
    )


def _evaluate_enroute_insertion(
    order_index: int,
    order: UnassignedOrder,
    route_nodes: list[str],
    truck_plan: TruckAssignment,
    assignment_result: Leg2AssignmentResult,
    min_priority: str,
    max_detour_ratio: float,
    base_route_km: float,
    distances_by_source: Mapping[str, Mapping[str, float]],
    predecessors_by_source: PredecessorMatrix | None,
) -> EnRouteInsertion | None:
    if priority_weight(order.priority) < priority_weight(min_priority):
        return None
    if truck_plan.remaining_capacity_kg < order.qty_kg:
        return None
    if truck_plan.depot_node_id not in order.candidate_warehouse_ids:
        return None
    if assignment_result.remaining_stock_kg.get((truck_plan.depot_node_id, order.product_id), 0.0) < order.qty_kg:
        return None
    candidate = try_insert_candidate(
        route_nodes,
        order.store_id,
        truck_plan.depot_node_id,
        base_route_km,
        truck_plan.cost_per_km,
        max_detour_ratio,
        distances_by_source,
        predecessors_by_source=predecessors_by_source,
    )
    if candidate is None:
        return None

    return EnRouteInsertion(
        unassigned_index=order_index,
        order=order,
        insert_after_index=candidate.insert_after_index,
        detour_km=candidate.detour_km,
        detour_ratio=candidate.detour_ratio,
        savings=candidate.savings,
    )


def _apply_enroute_insertion(
    insertion: EnRouteInsertion,
    route_nodes: list[str],
    truck_plan: TruckAssignment,
    assignment_result: Leg2AssignmentResult,
) -> list[str]:
    order = insertion.order
    if insertion.insert_after_index is not None:
        route_nodes = (
            route_nodes[: insertion.insert_after_index + 1]
            + [order.store_id]
            + route_nodes[insertion.insert_after_index + 1 :]
        )

    normalized_order = normalized_order_from_unassigned(order)
    score = (
        insertion.detour_km * truck_plan.cost_per_km / normalized_order.priority_weight
        if normalized_order.priority_weight > 0
        else math.inf
    )
    assigned_order = AssignedOrder(
        order=normalized_order,
        truck_id=truck_plan.truck_id,
        warehouse_id=truck_plan.depot_node_id,
        marginal_km=insertion.detour_km,
        score=round(score, 2),
        cost_per_km=truck_plan.cost_per_km,
    )
    truck_plan.assigned_orders.append(assigned_order)
    assignment_result.assigned_orders.append(assigned_order)
    truck_plan.remaining_capacity_kg = round(truck_plan.remaining_capacity_kg - order.qty_kg, 2)
    truck_plan.visited_stores.add(order.store_id)
    truck_plan.cargo_by_store_product[(order.store_id, order.product_id)] = round(
        truck_plan.cargo_by_store_product.get((order.store_id, order.product_id), 0.0) + order.qty_kg,
        2,
    )
    truck_plan.cargo_by_product[order.product_id] = round(
        truck_plan.cargo_by_product.get(order.product_id, 0.0) + order.qty_kg,
        2,
    )
    assignment_result.remaining_stock_kg[(truck_plan.depot_node_id, order.product_id)] = round(
        assignment_result.remaining_stock_kg.get((truck_plan.depot_node_id, order.product_id), 0.0)
        - order.qty_kg,
        2,
    )
    assignment_result.reserved_stock_delta_kg[(truck_plan.depot_node_id, order.product_id)] = round(
        assignment_result.reserved_stock_delta_kg.get((truck_plan.depot_node_id, order.product_id), 0.0)
        + order.qty_kg,
        2,
    )
    assignment_result.unassigned_orders.pop(insertion.unassigned_index)
    return route_nodes


def _compare_enroute_candidates(candidate: EnRouteInsertion, current_best: EnRouteInsertion) -> bool:
    return (
        -candidate.savings,
        candidate.detour_ratio,
        candidate.detour_km,
        -priority_weight(candidate.order.priority),
        candidate.order.order_id,
    ) < (
        -current_best.savings,
        current_best.detour_ratio,
        current_best.detour_km,
        -priority_weight(current_best.order.priority),
        current_best.order.order_id,
    )


def _distance_between(
    source: str,
    target: str,
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> float:
    if source == target:
        return 0.0
    return distances_by_source.get(source, {}).get(target, math.inf)


def _two_opt_swap(route: list[str], start_index: int, end_index: int) -> list[str]:
    return route[:start_index] + list(reversed(route[start_index : end_index + 1])) + route[end_index + 1 :]


def _resolve_unload_minutes(settings: Mapping[str, SettingsValue]) -> int:
    unload_value = settings.get("unload_min_default", 15)
    if isinstance(unload_value, bool):
        raise ValueError("unload_min_default must be numeric")
    if isinstance(unload_value, (int, float)):
        return int(unload_value)
    if isinstance(unload_value, str):
        return int(float(unload_value))
    raise ValueError(f"Unsupported unload_min_default value: {unload_value!r}")


def _resolve_departure_time(value: time | str | object | None) -> time:
    if value is None:
        return time.fromisoformat("08:00")
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    raise ValueError(f"Unsupported departure time value: {value!r}")


def _resolve_min_priority_enroute(settings: Mapping[str, SettingsValue]) -> str:
    value = settings.get("min_priority_enroute", "ELEVATED")
    if not isinstance(value, str):
        raise ValueError(f"Unsupported min_priority_enroute value: {value!r}")
    return value.upper()


def _resolve_max_detour_ratio(settings: Mapping[str, SettingsValue]) -> float:
    value = settings.get("max_detour_ratio", 0.15)
    if isinstance(value, bool):
        raise ValueError("max_detour_ratio must be numeric")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise ValueError(f"Unsupported max_detour_ratio value: {value!r}")


def _build_stop_priority_map(truck_plan: TruckAssignment) -> dict[str, str]:
    stop_priority_by_node: dict[str, str] = {}

    for assigned_order in truck_plan.assigned_orders:
        current_priority = stop_priority_by_node.get(assigned_order.order.store_id)
        if current_priority is None:
            stop_priority_by_node[assigned_order.order.store_id] = assigned_order.order.priority
            continue

        if priority_weight(assigned_order.order.priority) > priority_weight(current_priority):
            stop_priority_by_node[assigned_order.order.store_id] = assigned_order.order.priority

    return stop_priority_by_node


def _build_leg2_stop_notes(
    truck_plan: TruckAssignment,
    stop_priority_by_node: Mapping[str, str],
) -> dict[str, str]:
    stop_qty: dict[str, float] = {}

    for assigned_order in truck_plan.assigned_orders:
        stop_qty[assigned_order.order.store_id] = round(
            stop_qty.get(assigned_order.order.store_id, 0.0) + assigned_order.order.qty_kg,
            2,
        )

    return {
        stop_node_id: f"{stop_priority_by_node[stop_node_id]} {_format_qty(total_qty)} kg"
        for stop_node_id, total_qty in stop_qty.items()
    }


def _collect_solver_relevant_nodes(solver_inputs: SolverInputs) -> list[str]:
    relevant_nodes: list[str] = []
    seen: set[str] = set()

    def add(node_id: str) -> None:
        if node_id not in seen:
            seen.add(node_id)
            relevant_nodes.append(node_id)

    for truck in solver_inputs.trucks.values():
        add(truck.depot_node_id)

    for demand_record in solver_inputs.demand.values():
        if demand_record.requested_qty > 0:
            add(demand_record.node_id)

    for warehouse_id, _product_id in solver_inputs.warehouse_stock.keys():
        add(warehouse_id)

    return relevant_nodes


def _format_time(value: time) -> str:
    return value.strftime("%H:%M")


def _format_qty(value: float) -> str:
    rounded_value = round(value, 2)
    if rounded_value.is_integer():
        return str(int(rounded_value))
    return f"{rounded_value:.2f}".rstrip("0").rstrip(".")


__all__ = [
    "BREAK_AFTER_H",
    "BREAK_MIN",
    "DAILY_LIMIT_H",
    "EnRouteInsertion",
    "Leg2SolveResult",
    "RoutePlan",
    "SolveResult",
    "TimelineEvent",
    "WORK_TIME_LIMIT_H",
    "apply_enroute_insertions",
    "apply_reserved_stock_updates",
    "build_leg1_routes",
    "build_leg2_routes",
    "build_output_tables",
    "build_timeline",
    "classify_drive_time",
    "classify_route_timing",
    "compute_route_km",
    "compute_route_metrics",
    "ensure_round_trip",
    "greedy_tsp",
    "solve_leg2",
    "solve_network",
    "two_opt",
]
