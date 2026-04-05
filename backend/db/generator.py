from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from random import Random, SystemRandom
from typing import Literal
from uuid import uuid4

from solver.priority import compute_priority

from backend.db.constants import DEFAULT_SETTINGS
from backend.db.importer import import_demo_data

NodeRow = dict[str, object]
EdgeRow = dict[str, object]
ScaleName = Literal["small", "medium", "large"]

_PRODUCTS: tuple[dict[str, object], ...] = (
    {
        "id": "product_A",
        "name": "Product A",
        "weight_kg": 1.0,
        "length_cm": 30.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
    },
    {
        "id": "product_B",
        "name": "Product B",
        "weight_kg": 1.5,
        "length_cm": 40.0,
        "width_cm": 30.0,
        "height_cm": 20.0,
    },
    {
        "id": "product_C",
        "name": "Product C",
        "weight_kg": 2.0,
        "length_cm": 50.0,
        "width_cm": 40.0,
        "height_cm": 25.0,
    },
)

SCALE_PRESETS: dict[ScaleName, dict[str, int]] = {
    "small": {
        "n_factories": 2,
        "n_warehouses": 3,
        "n_stores": 12,
        "n_trucks": 5,
    },
    "medium": {
        "n_factories": 3,
        "n_warehouses": 5,
        "n_stores": 24,
        "n_trucks": 9,
    },
    "large": {
        "n_factories": 4,
        "n_warehouses": 7,
        "n_stores": 40,
        "n_trucks": 14,
    },
}

_STORE_ATTACHMENT_TO_STORE_PROBABILITY = 0.82
_STORE_EXTRA_WAREHOUSE_PROBABILITY = 0.1
_STORE_EXTRA_STORE_PROBABILITY = 0.2
_WAREHOUSE_EXTRA_LINK_PROBABILITY = 0.18
_FACTORY_EXTRA_LINK_PROBABILITY = 0.12


def generate_random_dataset(
    database_path: Path,
    *,
    n_factories: int | None = None,
    n_warehouses: int | None = None,
    n_stores: int | None = None,
    n_trucks: int | None = None,
    seed: int | None = None,
    scale: ScaleName | None = None,
) -> dict[str, object]:
    generation_counts = _resolve_generation_counts(
        scale=scale,
        n_factories=n_factories,
        n_warehouses=n_warehouses,
        n_stores=n_stores,
        n_trucks=n_trucks,
    )
    actual_seed = seed if seed is not None else SystemRandom().randrange(1, 2**31)

    rng = Random(actual_seed)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    temp_parent = database_path.parent / "_tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)

    temp_dir = temp_parent / f"logiflow_generated_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)

    factories = _generate_nodes(rng, "FACTORY", "factory", generation_counts["n_factories"], 45000.0)
    warehouses = _generate_nodes(rng, "WAREHOUSE", "warehouse", generation_counts["n_warehouses"], 15000.0)
    stores = _generate_nodes(rng, "STORE", "store", generation_counts["n_stores"], 5000.0)
    nodes = factories + warehouses + stores

    edges = _generate_edges(
        nodes=nodes,
        factories=factories,
        warehouses=warehouses,
        stores=stores,
        rng=rng,
    )
    trucks = _generate_trucks(
        n_trucks=generation_counts["n_trucks"],
        factories=factories,
        warehouses=warehouses,
    )
    warehouse_stock = _generate_warehouse_stock(rng, warehouses)
    demand = _generate_demand(rng, stores, generated_at)

    _write_csv(
        temp_dir / "nodes.csv",
        ["id", "name", "type", "capacity_kg", "lat", "lon"],
        nodes,
    )
    _write_csv(
        temp_dir / "edges.csv",
        ["from_id", "to_id", "distance_km"],
        edges,
    )
    _write_csv(
        temp_dir / "products.csv",
        ["id", "name", "weight_kg", "length_cm", "width_cm", "height_cm"],
        list(_PRODUCTS),
    )
    _write_csv(
        temp_dir / "trucks.csv",
        [
            "id",
            "name",
            "type",
            "capacity_kg",
            "fuel_per_100km",
            "depot_node_id",
            "driver_hourly",
            "avg_speed_kmh",
            "amortization_per_km",
            "maintenance_per_km",
        ],
        trucks,
    )
    _write_csv(
        temp_dir / "warehouse_stock.csv",
        ["warehouse_id", "product_id", "quantity_kg", "reserved_kg"],
        warehouse_stock,
    )
    _write_csv(
        temp_dir / "demand.csv",
        [
            "node_id",
            "product_id",
            "current_stock",
            "min_stock",
            "requested_qty",
            "priority",
            "is_urgent",
            "updated_at",
        ],
        demand,
    )
    _write_csv(
        temp_dir / "settings.csv",
        ["key", "value"],
        [{"key": key, "value": value} for key, value in DEFAULT_SETTINGS.items()],
    )
    _write_csv(
        temp_dir / "routes.csv",
        [
            "id",
            "truck_id",
            "leg",
            "stops",
            "total_km",
            "total_cost",
            "drive_hours",
            "total_elapsed_h",
            "days",
            "departure_time",
            "arrival_time",
            "time_status",
            "time_warning",
            "timeline",
            "created_at",
            "is_active",
        ],
        [],
    )
    _write_csv(
        temp_dir / "route_cargo.csv",
        ["route_id", "stop_node_id", "product_id", "qty_kg"],
        [],
    )

    imported_counts = import_demo_data(database_path, temp_dir)

    return {
        "status": "ok",
        "seed": actual_seed,
        "generated": {
            "scale": scale or "custom",
            "factories": generation_counts["n_factories"],
            "warehouses": generation_counts["n_warehouses"],
            "stores": generation_counts["n_stores"],
            "trucks": generation_counts["n_trucks"],
            "products": len(_PRODUCTS),
            "edges": len(edges),
        },
        "imported": imported_counts,
    }


def _resolve_generation_counts(
    *,
    scale: ScaleName | None,
    n_factories: int | None,
    n_warehouses: int | None,
    n_stores: int | None,
    n_trucks: int | None,
) -> dict[str, int]:
    if scale is not None:
        return dict(SCALE_PRESETS[scale])

    raw_counts = {
        "n_factories": n_factories,
        "n_warehouses": n_warehouses,
        "n_stores": n_stores,
        "n_trucks": n_trucks,
    }
    if any(value is None for value in raw_counts.values()):
        raise ValueError("Either scale or all generator counts must be provided")

    counts = {key: int(value) for key, value in raw_counts.items() if value is not None}
    if min(counts.values()) <= 0:
        raise ValueError("All generator counts must be greater than 0")

    return counts


def _generate_nodes(
    rng: Random,
    prefix: str,
    node_type: str,
    count: int,
    capacity_kg: float,
) -> list[NodeRow]:
    nodes: list[NodeRow] = []
    for index in range(1, count + 1):
        lat = round(rng.uniform(48.0, 50.8), 4)
        lon = round(rng.uniform(23.0, 37.5), 4)
        nodes.append(
            {
                "id": f"{prefix}_{index}",
                "name": f"{prefix.title()} {index}",
                "type": node_type,
                "capacity_kg": capacity_kg,
                "lat": lat,
                "lon": lon,
            }
        )
    return nodes


def _generate_edges(
    *,
    nodes: list[NodeRow],
    factories: list[NodeRow],
    warehouses: list[NodeRow],
    stores: list[NodeRow],
    rng: Random,
) -> list[EdgeRow]:
    if not warehouses:
        raise ValueError("Generator requires at least one warehouse to keep the graph connected")

    node_by_id = {str(node["id"]): node for node in nodes}
    degree_by_id: defaultdict[str, int] = defaultdict(int)
    edge_pairs: set[tuple[str, str]] = set()
    max_store_degree = max(2, min(4, math.ceil(len(stores) / 14) + 2))
    max_warehouse_degree = max(3, min(7, math.ceil(len(stores) / 10) + len(factories)))

    def add_edge(left_id: str, right_id: str) -> bool:
        if left_id == right_id:
            return False
        if not _is_allowed_edge(node_by_id[left_id], node_by_id[right_id]):
            return False
        pair = tuple(sorted((left_id, right_id)))
        if pair in edge_pairs:
            return False
        if _is_store(node_by_id[left_id]) and degree_by_id[left_id] >= max_store_degree:
            return False
        if _is_store(node_by_id[right_id]) and degree_by_id[right_id] >= max_store_degree:
            return False
        if _is_warehouse(node_by_id[left_id]) and degree_by_id[left_id] >= max_warehouse_degree:
            return False
        if _is_warehouse(node_by_id[right_id]) and degree_by_id[right_id] >= max_warehouse_degree:
            return False
        edge_pairs.add(pair)
        degree_by_id[left_id] += 1
        degree_by_id[right_id] += 1
        return True

    shuffled_warehouses = list(warehouses)
    rng.shuffle(shuffled_warehouses)
    for index, warehouse in enumerate(shuffled_warehouses[1:], start=1):
        parent = _pick_biased_candidate(
            rng=rng,
            source=warehouse,
            candidates=shuffled_warehouses[:index],
            top_k=min(3, index),
        )
        add_edge(str(warehouse["id"]), str(parent["id"]))

    for factory in factories:
        warehouse = _pick_biased_candidate(
            rng=rng,
            source=factory,
            candidates=warehouses,
            top_k=min(3, len(warehouses)),
        )
        add_edge(str(factory["id"]), str(warehouse["id"]))

    connected_stores: list[NodeRow] = []
    shuffled_stores = list(stores)
    rng.shuffle(shuffled_stores)

    for store in shuffled_stores:
        candidate_stores = [
            candidate for candidate in connected_stores if degree_by_id[str(candidate["id"])] < max_store_degree
        ]
        should_attach_to_store = bool(candidate_stores) and rng.random() < _STORE_ATTACHMENT_TO_STORE_PROBABILITY
        if should_attach_to_store:
            target = _pick_biased_candidate(
                rng=rng,
                source=store,
                candidates=candidate_stores,
                top_k=min(4, len(candidate_stores)),
            )
        else:
            target = _pick_biased_candidate(
                rng=rng,
                source=store,
                candidates=warehouses,
                top_k=min(3, len(warehouses)),
            )
        store_id = str(store["id"])
        added = add_edge(store_id, str(target["id"]))
        if not added:
            fallback_warehouses = sorted(
                warehouses,
                key=lambda warehouse: (
                    degree_by_id[str(warehouse["id"])],
                    _estimate_distance_between(store, warehouse),
                ),
            )
            for fallback in fallback_warehouses:
                if add_edge(store_id, str(fallback["id"])):
                    added = True
                    break
        if not added:
            fallback_stores = sorted(
                connected_stores,
                key=lambda candidate: (
                    degree_by_id[str(candidate["id"])],
                    _estimate_distance_between(store, candidate),
                ),
            )
            for fallback in fallback_stores:
                if add_edge(store_id, str(fallback["id"])):
                    added = True
                    break
        if not added:
            raise ValueError(f"Unable to connect store node {store_id} while keeping graph connected")
        connected_stores.append(store)

    _add_extra_edges(
        rng=rng,
        sources=factories,
        candidate_groups=[warehouses],
        probability=_FACTORY_EXTRA_LINK_PROBABILITY,
        edge_pairs=edge_pairs,
        node_by_id=node_by_id,
        degree_by_id=degree_by_id,
        add_edge=add_edge,
        max_candidates_per_source=1,
    )
    _add_extra_edges(
        rng=rng,
        sources=warehouses,
        candidate_groups=[warehouses],
        probability=_WAREHOUSE_EXTRA_LINK_PROBABILITY,
        edge_pairs=edge_pairs,
        node_by_id=node_by_id,
        degree_by_id=degree_by_id,
        add_edge=add_edge,
        max_candidates_per_source=1,
    )
    _add_extra_edges(
        rng=rng,
        sources=stores,
        candidate_groups=[warehouses],
        probability=_STORE_EXTRA_WAREHOUSE_PROBABILITY,
        edge_pairs=edge_pairs,
        node_by_id=node_by_id,
        degree_by_id=degree_by_id,
        add_edge=add_edge,
        max_candidates_per_source=1,
    )
    _add_extra_edges(
        rng=rng,
        sources=stores,
        candidate_groups=[stores],
        probability=_STORE_EXTRA_STORE_PROBABILITY,
        edge_pairs=edge_pairs,
        node_by_id=node_by_id,
        degree_by_id=degree_by_id,
        add_edge=add_edge,
        max_candidates_per_source=1,
    )

    return [
        {
            "from_id": left_id,
            "to_id": right_id,
            "distance_km": _estimate_distance_between(node_by_id[left_id], node_by_id[right_id]),
        }
        for left_id, right_id in sorted(edge_pairs)
    ]


def _add_extra_edges(
    *,
    rng: Random,
    sources: list[NodeRow],
    candidate_groups: list[list[NodeRow]],
    probability: float,
    edge_pairs: set[tuple[str, str]],
    node_by_id: dict[str, NodeRow],
    degree_by_id: defaultdict[str, int],
    add_edge,
    max_candidates_per_source: int,
) -> None:
    for source in sources:
        source_id = str(source["id"])
        if _is_store(source) and degree_by_id[source_id] >= max(2, min(4, math.ceil(len(sources) / 16) + 2)):
            continue
        if rng.random() > probability:
            continue

        candidates: list[NodeRow] = []
        for group in candidate_groups:
            for candidate in group:
                candidate_id = str(candidate["id"])
                if candidate_id == source_id:
                    continue
                if tuple(sorted((source_id, candidate_id))) in edge_pairs:
                    continue
                if not _is_allowed_edge(source, candidate):
                    continue
                candidates.append(candidate)

        if not candidates:
            continue

        rng.shuffle(candidates)
        attempts = 0
        added = 0
        for candidate in sorted(
            candidates,
            key=lambda candidate: _estimate_distance_between(source, candidate),
        ):
            attempts += 1
            if attempts > 4:
                break
            if add_edge(source_id, str(candidate["id"])):
                added += 1
            if added >= max_candidates_per_source:
                break


def _pick_biased_candidate(
    *,
    rng: Random,
    source: NodeRow,
    candidates: list[NodeRow],
    top_k: int,
) -> NodeRow:
    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: _estimate_distance_between(source, candidate),
    )
    window = ranked_candidates[: max(1, top_k)]
    return rng.choice(window)


def _is_allowed_edge(left: NodeRow, right: NodeRow) -> bool:
    left_type = str(left["type"])
    right_type = str(right["type"])
    edge_types = {left_type, right_type}
    return edge_types != {"factory", "store"}


def _is_store(node: NodeRow) -> bool:
    return str(node["type"]) == "store"


def _is_warehouse(node: NodeRow) -> bool:
    return str(node["type"]) == "warehouse"


def _estimate_distance_between(left: NodeRow, right: NodeRow) -> float:
    return _estimate_distance_km(
        float(left["lat"]),
        float(left["lon"]),
        float(right["lat"]),
        float(right["lon"]),
    )


def _estimate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat_km = (lat1 - lat2) * 111.0
    lon_km = (lon1 - lon2) * 73.0
    return round(max(1.0, math.hypot(lat_km, lon_km)), 2)


def _generate_trucks(
    *,
    n_trucks: int,
    factories: list[NodeRow],
    warehouses: list[NodeRow],
) -> list[dict[str, object]]:
    trucks: list[dict[str, object]] = []
    warehouse_ids = [item["id"] for item in warehouses]
    factory_ids = [item["id"] for item in factories]

    for index in range(1, n_trucks + 1):
        if index <= max(1, n_trucks // 4):
            truck_type = "semi"
            depot_node_id = factory_ids[(index - 1) % len(factory_ids)]
            capacity_kg = 20000.0
            fuel_per_100km = 35.0
            name = f"Semi {index}"
        elif index <= max(2, (n_trucks * 3) // 4):
            truck_type = "truck"
            depot_node_id = warehouse_ids[(index - 1) % len(warehouse_ids)]
            capacity_kg = 5000.0
            fuel_per_100km = 22.0
            name = f"Truck {index}"
        else:
            truck_type = "van"
            depot_node_id = warehouse_ids[(index - 1) % len(warehouse_ids)]
            capacity_kg = 1800.0
            fuel_per_100km = 14.0
            name = f"Van {index}"

        trucks.append(
            {
                "id": f"T{index}",
                "name": name,
                "type": truck_type,
                "capacity_kg": capacity_kg,
                "fuel_per_100km": fuel_per_100km,
                "depot_node_id": depot_node_id,
                "driver_hourly": "",
                "avg_speed_kmh": "",
                "amortization_per_km": "",
                "maintenance_per_km": "",
            }
        )

    return trucks


def _generate_warehouse_stock(
    rng: Random,
    warehouses: list[NodeRow],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for warehouse in warehouses:
        for product in _PRODUCTS:
            rows.append(
                {
                    "warehouse_id": warehouse["id"],
                    "product_id": product["id"],
                    "quantity_kg": round(rng.uniform(1200.0, 5000.0), 2),
                    "reserved_kg": 0.0,
                }
            )
    return rows


def _generate_demand(
    rng: Random,
    stores: list[NodeRow],
    updated_at: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for store in stores:
        for product in _PRODUCTS:
            min_stock = float(rng.randint(70, 140))
            current_stock = round(rng.uniform(0.0, min_stock * 1.1), 2)
            requested_qty = round(max(0.0, min_stock - current_stock), 2)
            priority = compute_priority(
                current_stock=current_stock,
                min_stock=min_stock,
                is_urgent=False,
            )
            rows.append(
                {
                    "node_id": store["id"],
                    "product_id": product["id"],
                    "current_stock": current_stock,
                    "min_stock": min_stock,
                    "requested_qty": requested_qty,
                    "priority": priority,
                    "is_urgent": 0,
                    "updated_at": updated_at,
                }
            )
    return rows


def _write_csv(csv_path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


__all__ = [
    "SCALE_PRESETS",
    "generate_random_dataset",
]
