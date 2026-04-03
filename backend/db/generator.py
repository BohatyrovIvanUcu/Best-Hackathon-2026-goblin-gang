from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
from random import Random
from uuid import uuid4

from solver.priority import compute_priority

from backend.db.constants import DEFAULT_SETTINGS
from backend.db.importer import import_demo_data

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


def generate_random_dataset(
    database_path: Path,
    *,
    n_factories: int,
    n_warehouses: int,
    n_stores: int,
    n_trucks: int,
    seed: int,
) -> dict[str, object]:
    if min(n_factories, n_warehouses, n_stores, n_trucks) <= 0:
        raise ValueError("All generator counts must be greater than 0")

    rng = Random(seed)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    temp_parent = database_path.parent / "_tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)

    temp_dir = temp_parent / f"logiflow_generated_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)

    factories = _generate_nodes(rng, "FACTORY", "factory", n_factories, 45000.0)
    warehouses = _generate_nodes(rng, "WAREHOUSE", "warehouse", n_warehouses, 15000.0)
    stores = _generate_nodes(rng, "STORE", "store", n_stores, 5000.0)
    nodes = factories + warehouses + stores

    edges = _generate_edges(nodes)
    trucks = _generate_trucks(
        n_trucks=n_trucks,
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
        "seed": seed,
        "generated": {
            "factories": n_factories,
            "warehouses": n_warehouses,
            "stores": n_stores,
            "trucks": n_trucks,
            "products": len(_PRODUCTS),
        },
        "imported": imported_counts,
    }


def _generate_nodes(
    rng: Random,
    prefix: str,
    node_type: str,
    count: int,
    capacity_kg: float,
) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
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


def _generate_edges(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    for source in nodes:
        for target in nodes:
            if source["id"] == target["id"]:
                continue
            distance = _estimate_distance_km(
                float(source["lat"]),
                float(source["lon"]),
                float(target["lat"]),
                float(target["lon"]),
            )
            edges.append(
                {
                    "from_id": source["id"],
                    "to_id": target["id"],
                    "distance_km": distance,
                }
            )
    return edges


def _estimate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat_km = (lat1 - lat2) * 111.0
    lon_km = (lon1 - lon2) * 73.0
    return round(max(1.0, math.hypot(lat_km, lon_km)), 2)


def _generate_trucks(
    *,
    n_trucks: int,
    factories: list[dict[str, object]],
    warehouses: list[dict[str, object]],
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
    warehouses: list[dict[str, object]],
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
    stores: list[dict[str, object]],
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
