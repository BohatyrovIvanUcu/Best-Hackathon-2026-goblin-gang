from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from .cost import compute_cost_per_km
from .graph import DistanceMatrix, Graph, dijkstra
from .io import DemandKey, DemandRecord, SettingsValue, Truck, WarehouseStockRecord
from .priority import compute_priority, priority_weight

StockKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CandidateWarehouse:
    warehouse_id: str
    available_kg: float
    distance_km: float

    @property
    def supply_score(self) -> float:
        return self.distance_km


@dataclass(frozen=True, slots=True)
class NormalizedOrder:
    order_id: str
    store_id: str
    product_id: str
    qty_kg: float
    priority: str
    priority_weight: float
    warehouse_id: str
    is_urgent: bool
    updated_at: datetime | None
    current_stock: float
    min_stock: float
    candidate_warehouses: tuple[CandidateWarehouse, ...]

    @property
    def preferred_warehouse_id(self) -> str | None:
        return self.warehouse_id

    @property
    def candidate_warehouse_ids(self) -> tuple[str, ...]:
        return tuple(candidate.warehouse_id for candidate in self.candidate_warehouses)


@dataclass(frozen=True, slots=True)
class AssignedOrder:
    order: NormalizedOrder
    truck_id: str
    warehouse_id: str
    marginal_km: float
    score: float
    cost_per_km: float


@dataclass(frozen=True, slots=True)
class UnassignedOrder:
    order_id: str
    store_id: str
    product_id: str
    qty_kg: float
    priority: str
    priority_weight: float
    reason: str
    warehouse_id: str
    candidate_warehouse_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class TruckAssignment:
    truck: Truck
    cost_per_km: float
    remaining_capacity_kg: float
    assigned_stops: list[str] = field(default_factory=list)
    assigned_orders: list[AssignedOrder] = field(default_factory=list)
    cargo_by_store_product: dict[StockKey, float] = field(default_factory=dict)
    cargo_by_product: dict[str, float] = field(default_factory=dict)
    visited_stores: set[str] = field(default_factory=set)

    @property
    def truck_id(self) -> str:
        return self.truck.id

    @property
    def depot_node_id(self) -> str:
        return self.truck.depot_node_id

    @property
    def last_stop(self) -> str:
        if not self.assigned_stops:
            return self.depot_node_id
        return self.assigned_stops[-1]


@dataclass(slots=True)
class Leg2AssignmentResult:
    truck_plans: dict[str, TruckAssignment]
    assigned_orders: list[AssignedOrder]
    unassigned_orders: list[UnassignedOrder]
    remaining_stock_kg: dict[StockKey, float]
    reserved_stock_delta_kg: dict[StockKey, float]


@dataclass(frozen=True, slots=True)
class ReplenishmentNeed:
    warehouse_id: str
    product_id: str
    qty_kg: float
    factory_id: str | None


@dataclass(frozen=True, slots=True)
class Leg1Trip:
    truck_id: str
    factory_id: str
    warehouse_id: str
    trip_no: int
    total_qty_kg: float
    cargo_by_product: dict[str, float]


@dataclass(frozen=True, slots=True)
class Leg1PlanResult:
    needs: list[ReplenishmentNeed]
    trips: list[Leg1Trip]
    replenishment_by_warehouse_product: dict[StockKey, float]
    unassigned_needs: list[ReplenishmentNeed]


def available_kg(stock_record: WarehouseStockRecord) -> float:
    return stock_record.quantity_kg - stock_record.reserved_kg


def select_candidate_warehouses(
    demand_row: DemandRecord,
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None = None,
) -> list[CandidateWarehouse]:
    fallback_cache: dict[str, dict[str, float]] | None = {} if graph is not None else None
    return _select_candidate_warehouses(
        demand_row,
        warehouse_stock,
        distances_by_source,
        graph,
        fallback_cache,
    )


def build_normalized_orders(
    demand_rows: Mapping[DemandKey, DemandRecord] | Iterable[DemandRecord],
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None = None,
) -> tuple[list[NormalizedOrder], list[UnassignedOrder]]:
    fallback_cache: dict[str, dict[str, float]] | None = {} if graph is not None else None
    normalized_orders: list[NormalizedOrder] = []
    shortages: list[UnassignedOrder] = []

    iterable = demand_rows.values() if isinstance(demand_rows, Mapping) else demand_rows
    for demand_row in iterable:
        if demand_row.requested_qty <= 0:
            continue

        priority = compute_priority(
            current_stock=demand_row.current_stock,
            min_stock=demand_row.min_stock,
            is_urgent=demand_row.is_urgent,
        )
        weight = priority_weight(priority)
        candidate_warehouses = _select_candidate_warehouses(
            demand_row,
            warehouse_stock,
            distances_by_source,
            graph,
            fallback_cache,
        )
        order_id = _make_order_id(demand_row.node_id, demand_row.product_id)

        if not candidate_warehouses:
            shortages.append(
                UnassignedOrder(
                    order_id=order_id,
                    store_id=demand_row.node_id,
                    product_id=demand_row.product_id,
                    qty_kg=demand_row.requested_qty,
                    priority=priority,
                    priority_weight=weight,
                    reason="no_reachable_warehouse_for_product",
                    warehouse_id="",
                )
            )
            continue

        normalized_orders.append(
            NormalizedOrder(
                order_id=order_id,
                store_id=demand_row.node_id,
                product_id=demand_row.product_id,
                qty_kg=demand_row.requested_qty,
                priority=priority,
                priority_weight=weight,
                warehouse_id=candidate_warehouses[0].warehouse_id,
                is_urgent=demand_row.is_urgent,
                updated_at=demand_row.updated_at,
                current_stock=demand_row.current_stock,
                min_stock=demand_row.min_stock,
                candidate_warehouses=tuple(candidate_warehouses),
            )
        )

    return sort_orders_for_assignment(normalized_orders), shortages


def sort_orders_for_assignment(orders: Iterable[NormalizedOrder]) -> list[NormalizedOrder]:
    return sorted(
        orders,
        key=lambda order: (
            -order.priority_weight,
            -order.qty_kg,
            order.store_id,
            order.product_id,
        ),
    )


def compute_leg1_replenishment_needs(
    orders: Iterable[NormalizedOrder],
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    trucks: Mapping[str, Truck],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None = None,
) -> list[ReplenishmentNeed]:
    demand_by_warehouse_product: defaultdict[StockKey, float] = defaultdict(float)
    semi_trucks = [truck for truck in trucks.values() if truck.type == "semi"]
    fallback_cache: dict[str, dict[str, float]] | None = {} if graph is not None else None

    for order in orders:
        demand_by_warehouse_product[(order.warehouse_id, order.product_id)] += order.qty_kg

    needs: list[ReplenishmentNeed] = []
    for (warehouse_id, product_id), total_needed in sorted(demand_by_warehouse_product.items()):
        stock_record = warehouse_stock.get((warehouse_id, product_id))
        available_stock = available_kg(stock_record) if stock_record is not None else 0.0
        deficit_kg = round(max(0.0, total_needed - available_stock), 2)
        if deficit_kg <= 0:
            continue

        factory_id = _select_nearest_factory_for_warehouse(
            warehouse_id,
            semi_trucks,
            distances_by_source,
            graph,
            fallback_cache,
        )
        needs.append(
            ReplenishmentNeed(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_kg=deficit_kg,
                factory_id=factory_id,
            )
        )

    return needs


def assign_leg1_simple(
    orders: Iterable[NormalizedOrder],
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    trucks: Mapping[str, Truck],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None = None,
) -> Leg1PlanResult:
    needs = compute_leg1_replenishment_needs(
        orders,
        warehouse_stock,
        trucks,
        distances_by_source,
        graph=graph,
    )
    semi_trucks_by_factory: defaultdict[str, list[Truck]] = defaultdict(list)
    for truck in trucks.values():
        if truck.type == "semi":
            semi_trucks_by_factory[truck.depot_node_id].append(truck)
    for factory_id in semi_trucks_by_factory:
        semi_trucks_by_factory[factory_id].sort(key=lambda truck: truck.id)

    grouped_needs: defaultdict[tuple[str | None, str], list[ReplenishmentNeed]] = defaultdict(list)
    for need in needs:
        grouped_needs[(need.factory_id, need.warehouse_id)].append(need)

    replenishment_by_warehouse_product: defaultdict[StockKey, float] = defaultdict(float)
    trips: list[Leg1Trip] = []
    unassigned_needs: list[ReplenishmentNeed] = []

    for (factory_id, warehouse_id), warehouse_needs in sorted(
        grouped_needs.items(),
        key=lambda item: ((item[0][0] or ""), item[0][1]),
    ):
        if factory_id is None or not semi_trucks_by_factory.get(factory_id):
            unassigned_needs.extend(warehouse_needs)
            continue

        remaining_products = [
            [need.product_id, need.qty_kg]
            for need in sorted(warehouse_needs, key=lambda item: item.product_id)
            if need.qty_kg > 0
        ]
        semi_trucks = semi_trucks_by_factory[factory_id]
        trip_no_by_truck: defaultdict[str, int] = defaultdict(int)
        truck_cursor = 0

        while any(qty > 0 for _, qty in remaining_products):
            truck = semi_trucks[truck_cursor % len(semi_trucks)]
            truck_cursor += 1
            remaining_capacity = truck.capacity_kg
            cargo_by_product: dict[str, float] = {}

            for product_entry in remaining_products:
                product_id, remaining_qty = product_entry
                if remaining_qty <= 0 or remaining_capacity <= 0:
                    continue

                load_qty = min(remaining_qty, remaining_capacity)
                cargo_by_product[product_id] = round(
                    cargo_by_product.get(product_id, 0.0) + load_qty,
                    2,
                )
                product_entry[1] = round(remaining_qty - load_qty, 2)
                remaining_capacity = round(remaining_capacity - load_qty, 2)

            if not cargo_by_product:
                break

            trip_no_by_truck[truck.id] += 1
            total_qty_kg = round(sum(cargo_by_product.values()), 2)
            trips.append(
                Leg1Trip(
                    truck_id=truck.id,
                    factory_id=factory_id,
                    warehouse_id=warehouse_id,
                    trip_no=trip_no_by_truck[truck.id],
                    total_qty_kg=total_qty_kg,
                    cargo_by_product=dict(sorted(cargo_by_product.items())),
                )
            )

            for product_id, qty_kg in cargo_by_product.items():
                replenishment_by_warehouse_product[(warehouse_id, product_id)] += qty_kg

    return Leg1PlanResult(
        needs=needs,
        trips=trips,
        replenishment_by_warehouse_product=dict(replenishment_by_warehouse_product),
        unassigned_needs=unassigned_needs,
    )


def assign_leg2(
    orders: Iterable[NormalizedOrder],
    trucks: Mapping[str, Truck],
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    distances_by_source: DistanceMatrix,
    settings: Mapping[str, SettingsValue],
    graph: Graph | None = None,
    preexisting_unassigned: Iterable[UnassignedOrder] = (),
    stock_replenishment_kg: Mapping[StockKey, float] | None = None,
) -> Leg2AssignmentResult:
    truck_plans = {
        truck.id: TruckAssignment(
            truck=truck,
            cost_per_km=compute_cost_per_km(truck, settings),
            remaining_capacity_kg=truck.capacity_kg,
        )
        for truck in trucks.values()
        if truck.type in {"truck", "van"}
    }

    stock_replenishment_kg = stock_replenishment_kg or {}
    remaining_stock_kg: dict[StockKey, float] = {
        (stock_record.warehouse_id, stock_record.product_id): round(
            available_kg(stock_record)
            + float(stock_replenishment_kg.get((stock_record.warehouse_id, stock_record.product_id), 0.0)),
            2,
        )
        for stock_record in warehouse_stock.values()
    }
    for stock_key, injected_qty in stock_replenishment_kg.items():
        if stock_key not in remaining_stock_kg:
            remaining_stock_kg[stock_key] = round(float(injected_qty), 2)
    reserved_stock_delta_kg: defaultdict[StockKey, float] = defaultdict(float)
    assigned_orders: list[AssignedOrder] = []
    unassigned_orders = list(preexisting_unassigned)
    fallback_cache: dict[str, dict[str, float]] | None = {} if graph is not None else None

    for order in sort_orders_for_assignment(orders):
        best_choice: tuple[tuple[float, float, float, str], TruckAssignment, float] | None = None
        candidate_plans = [
            plan for plan in truck_plans.values() if plan.depot_node_id == order.warehouse_id
        ]
        plans_to_consider = candidate_plans

        for plan in plans_to_consider:
            if not _can_assign_order(plan, order, remaining_stock_kg):
                continue

            marginal_km = compute_marginal_km(
                plan,
                order,
                distances_by_source,
                graph=graph,
                fallback_cache=fallback_cache,
            )
            if not math.isfinite(marginal_km):
                continue

            score = marginal_km * plan.cost_per_km / order.priority_weight
            rank = (score, marginal_km, -plan.remaining_capacity_kg, plan.truck_id)
            if best_choice is None or rank < best_choice[0]:
                best_choice = (rank, plan, marginal_km)

        if best_choice is None:
            unassigned_orders.append(
                UnassignedOrder(
                    order_id=order.order_id,
                    store_id=order.store_id,
                    product_id=order.product_id,
                    qty_kg=order.qty_kg,
                    priority=order.priority,
                    priority_weight=order.priority_weight,
                    reason=_derive_unassigned_reason(order, truck_plans, remaining_stock_kg),
                    warehouse_id=order.warehouse_id,
                    candidate_warehouse_ids=order.candidate_warehouse_ids,
                )
            )
            continue

        rank, chosen_plan, marginal_km = best_choice
        score = rank[0]
        warehouse_id = chosen_plan.depot_node_id
        chosen_plan.remaining_capacity_kg = round(chosen_plan.remaining_capacity_kg - order.qty_kg, 2)
        if order.store_id not in chosen_plan.visited_stores:
            chosen_plan.visited_stores.add(order.store_id)
            chosen_plan.assigned_stops.append(order.store_id)
        chosen_plan.cargo_by_store_product[(order.store_id, order.product_id)] = round(
            chosen_plan.cargo_by_store_product.get((order.store_id, order.product_id), 0.0)
            + order.qty_kg,
            2,
        )
        chosen_plan.cargo_by_product[order.product_id] = round(
            chosen_plan.cargo_by_product.get(order.product_id, 0.0) + order.qty_kg,
            2,
        )

        assigned_order = AssignedOrder(
            order=order,
            truck_id=chosen_plan.truck_id,
            warehouse_id=warehouse_id,
            marginal_km=marginal_km,
            score=score,
            cost_per_km=chosen_plan.cost_per_km,
        )
        chosen_plan.assigned_orders.append(assigned_order)
        assigned_orders.append(assigned_order)

        remaining_stock_kg[(warehouse_id, order.product_id)] = round(
            remaining_stock_kg[(warehouse_id, order.product_id)] - order.qty_kg,
            2,
        )
        reserved_stock_delta_kg[(warehouse_id, order.product_id)] += order.qty_kg

    return Leg2AssignmentResult(
        truck_plans=truck_plans,
        assigned_orders=assigned_orders,
        unassigned_orders=unassigned_orders,
        remaining_stock_kg=remaining_stock_kg,
        reserved_stock_delta_kg=dict(reserved_stock_delta_kg),
    )


def compute_marginal_km(
    plan: TruckAssignment,
    order: NormalizedOrder,
    distances_by_source: DistanceMatrix,
    graph: Graph | None = None,
    fallback_cache: dict[str, dict[str, float]] | None = None,
) -> float:
    if order.store_id in plan.visited_stores:
        return 0.0

    return _lookup_distance(
        plan.last_stop,
        order.store_id,
        distances_by_source,
        graph,
        fallback_cache,
    )


def normalized_order_from_unassigned(order: UnassignedOrder) -> NormalizedOrder:
    return NormalizedOrder(
        order_id=order.order_id,
        store_id=order.store_id,
        product_id=order.product_id,
        qty_kg=order.qty_kg,
        priority=order.priority,
        priority_weight=order.priority_weight,
        warehouse_id=order.warehouse_id,
        is_urgent=order.priority == "CRITICAL",
        updated_at=None,
        current_stock=0.0,
        min_stock=0.0,
        candidate_warehouses=tuple(
            CandidateWarehouse(
                warehouse_id=warehouse_id,
                available_kg=0.0,
                distance_km=math.inf,
            )
            for warehouse_id in order.candidate_warehouse_ids
        ),
    )


def _select_candidate_warehouses(
    demand_row: DemandRecord,
    warehouse_stock: Mapping[DemandKey, WarehouseStockRecord],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None,
    fallback_cache: dict[str, dict[str, float]] | None,
) -> list[CandidateWarehouse]:
    candidates: list[CandidateWarehouse] = []

    for stock_record in warehouse_stock.values():
        if stock_record.product_id != demand_row.product_id:
            continue

        distance_km = _lookup_distance(
            stock_record.warehouse_id,
            demand_row.node_id,
            distances_by_source,
            graph,
            fallback_cache,
        )
        if not math.isfinite(distance_km):
            continue

        candidates.append(
            CandidateWarehouse(
                warehouse_id=stock_record.warehouse_id,
                available_kg=available_kg(stock_record),
                distance_km=distance_km,
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.distance_km,
            -candidate.available_kg,
            candidate.warehouse_id,
        ),
    )


def _lookup_distance(
    source: str,
    target: str,
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None,
    fallback_cache: dict[str, dict[str, float]] | None,
) -> float:
    if source == target:
        return 0.0

    source_distances = distances_by_source.get(source)
    if source_distances is not None:
        return source_distances.get(target, math.inf)

    if graph is None or fallback_cache is None:
        return math.inf

    cached_distances = fallback_cache.get(source)
    if cached_distances is None:
        cached_distances, _ = dijkstra(graph, source)
        fallback_cache[source] = cached_distances

    return cached_distances.get(target, math.inf)


def _select_nearest_factory_for_warehouse(
    warehouse_id: str,
    semi_trucks: Iterable[Truck],
    distances_by_source: Mapping[str, Mapping[str, float]],
    graph: Graph | None,
    fallback_cache: dict[str, dict[str, float]] | None,
) -> str | None:
    nearest_factory_id: str | None = None
    nearest_distance = math.inf

    for truck in semi_trucks:
        factory_id = truck.depot_node_id
        distance_km = _lookup_distance(factory_id, warehouse_id, distances_by_source, graph, fallback_cache)
        if distance_km < nearest_distance or (
            math.isclose(distance_km, nearest_distance)
            and (nearest_factory_id is None or factory_id < nearest_factory_id)
        ):
            nearest_distance = distance_km
            nearest_factory_id = factory_id

    if not math.isfinite(nearest_distance):
        return None
    return nearest_factory_id


def _derive_unassigned_reason(
    order: NormalizedOrder,
    truck_plans: Mapping[str, TruckAssignment],
    remaining_stock_kg: Mapping[StockKey, float],
) -> str:
    candidate_plans = [
        plan for plan in truck_plans.values() if plan.depot_node_id == order.warehouse_id
    ]
    if not candidate_plans:
        return "no_truck_for_selected_warehouse"

    if remaining_stock_kg.get((order.warehouse_id, order.product_id), 0.0) < order.qty_kg:
        return "insufficient_available_stock_after_replenishment"

    if not any(plan.remaining_capacity_kg >= order.qty_kg for plan in candidate_plans):
        return "insufficient_truck_capacity"

    return "no_reachable_truck"


def _can_assign_order(
    plan: TruckAssignment,
    order: NormalizedOrder,
    remaining_stock_kg: Mapping[StockKey, float],
) -> bool:
    if plan.depot_node_id != order.warehouse_id:
        return False
    if plan.remaining_capacity_kg < order.qty_kg:
        return False
    if remaining_stock_kg.get((plan.depot_node_id, order.product_id), 0.0) < order.qty_kg:
        return False
    return True


def _make_order_id(store_id: str, product_id: str) -> str:
    return f"{store_id}:{product_id}"


__all__ = [
    "AssignedOrder",
    "CandidateWarehouse",
    "Leg1PlanResult",
    "Leg1Trip",
    "Leg2AssignmentResult",
    "NormalizedOrder",
    "ReplenishmentNeed",
    "TruckAssignment",
    "UnassignedOrder",
    "assign_leg1_simple",
    "assign_leg2",
    "available_kg",
    "build_normalized_orders",
    "compute_leg1_replenishment_needs",
    "compute_marginal_km",
    "normalized_order_from_unassigned",
    "select_candidate_warehouses",
    "sort_orders_for_assignment",
]
