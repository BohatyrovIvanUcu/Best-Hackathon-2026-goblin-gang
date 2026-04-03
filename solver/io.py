from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, TypeAlias

from .graph import Graph, build_graph_from_edges

DemandKey: TypeAlias = tuple[str, str]
SettingsValue: TypeAlias = str | float | int | bool | time

_NULL_VALUES = {"", "null", "none", "nan", "n/a"}
_TRUCK_DEFAULTS = {
    "driver_hourly": "driver_hourly_default",
    "avg_speed_kmh": "avg_speed_default",
    "amortization_per_km": "amortization_default",
    "maintenance_per_km": "maintenance_default",
}


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    name: str
    type: str
    capacity_kg: float
    lat: float | None
    lon: float | None


@dataclass(frozen=True, slots=True)
class Edge:
    from_id: str
    to_id: str
    distance_km: float


@dataclass(frozen=True, slots=True)
class Truck:
    id: str
    name: str
    type: str
    capacity_kg: float
    fuel_per_100km: float
    depot_node_id: str
    driver_hourly: float
    avg_speed_kmh: float
    amortization_per_km: float
    maintenance_per_km: float


@dataclass(frozen=True, slots=True)
class DemandRecord:
    node_id: str
    product_id: str
    current_stock: float
    min_stock: float
    requested_qty: float
    priority: str
    is_urgent: bool
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WarehouseStockRecord:
    warehouse_id: str
    product_id: str
    quantity_kg: float
    reserved_kg: float


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    weight_kg: float
    length_cm: float | None
    width_cm: float | None
    height_cm: float | None


@dataclass(frozen=True, slots=True)
class SolverInputs:
    nodes: dict[str, Node]
    edges: list[Edge]
    graph: Graph
    trucks: dict[str, Truck]
    demand: dict[DemandKey, DemandRecord]
    warehouse_stock: dict[DemandKey, WarehouseStockRecord]
    products: dict[str, Product]
    settings: dict[str, SettingsValue]


def load_nodes_csv(csv_path: str | Path) -> dict[str, Node]:
    nodes: dict[str, Node] = {}

    for row in _read_csv_rows(csv_path):
        node = Node(
            id=_require_text(row, "id"),
            name=_require_text(row, "name"),
            type=_require_text(row, "type").lower(),
            capacity_kg=_require_float(row, "capacity_kg"),
            lat=_optional_float(row, "lat"),
            lon=_optional_float(row, "lon"),
        )
        _store_unique(nodes, node.id, node, csv_path)

    return nodes


def load_edges_csv(csv_path: str | Path) -> list[Edge]:
    edges: list[Edge] = []

    for row in _read_csv_rows(csv_path):
        edge = Edge(
            from_id=_require_text(row, "from_id"),
            to_id=_require_text(row, "to_id"),
            distance_km=_require_float(row, "distance_km"),
        )
        if edge.distance_km < 0:
            raise ValueError(
                f"Edge distance must be non-negative in {csv_path}: "
                f"{edge.from_id} -> {edge.to_id} = {edge.distance_km}"
            )
        edges.append(edge)

    return edges


def load_settings_csv(csv_path: str | Path) -> dict[str, SettingsValue]:
    settings: dict[str, SettingsValue] = {}

    for row in _read_csv_rows(csv_path):
        key = _require_text(row, "key")
        value = _parse_setting_value(key, _require_text(row, "value"))
        _store_unique(settings, key, value, csv_path)

    return settings


def load_trucks_csv(
    csv_path: str | Path,
    settings: dict[str, SettingsValue],
) -> dict[str, Truck]:
    trucks: dict[str, Truck] = {}

    for row in _read_csv_rows(csv_path):
        truck = Truck(
            id=_require_text(row, "id"),
            name=_require_text(row, "name"),
            type=_require_text(row, "type").lower(),
            capacity_kg=_require_float(row, "capacity_kg"),
            fuel_per_100km=_require_float(row, "fuel_per_100km"),
            depot_node_id=_require_text(row, "depot_node_id"),
            driver_hourly=_float_with_setting_default(row, "driver_hourly", settings),
            avg_speed_kmh=_float_with_setting_default(row, "avg_speed_kmh", settings),
            amortization_per_km=_float_with_setting_default(row, "amortization_per_km", settings),
            maintenance_per_km=_float_with_setting_default(row, "maintenance_per_km", settings),
        )
        _store_unique(trucks, truck.id, truck, csv_path)

    return trucks


def load_demand_csv(csv_path: str | Path) -> dict[DemandKey, DemandRecord]:
    demand: dict[DemandKey, DemandRecord] = {}

    for row in _read_csv_rows(csv_path):
        record = DemandRecord(
            node_id=_require_text(row, "node_id"),
            product_id=_require_text(row, "product_id"),
            current_stock=_require_float(row, "current_stock"),
            min_stock=_require_float(row, "min_stock"),
            requested_qty=_require_float(row, "requested_qty"),
            priority=_require_text(row, "priority").upper(),
            is_urgent=_require_bool(row, "is_urgent"),
            updated_at=_optional_datetime(row, "updated_at"),
        )
        _store_unique(demand, (record.node_id, record.product_id), record, csv_path)

    return demand


def load_warehouse_stock_csv(csv_path: str | Path) -> dict[DemandKey, WarehouseStockRecord]:
    warehouse_stock: dict[DemandKey, WarehouseStockRecord] = {}

    for row in _read_csv_rows(csv_path):
        record = WarehouseStockRecord(
            warehouse_id=_require_text(row, "warehouse_id"),
            product_id=_require_text(row, "product_id"),
            quantity_kg=_require_float(row, "quantity_kg"),
            reserved_kg=_require_float(row, "reserved_kg"),
        )
        _store_unique(
            warehouse_stock,
            (record.warehouse_id, record.product_id),
            record,
            csv_path,
        )

    return warehouse_stock


def load_products_csv(csv_path: str | Path) -> dict[str, Product]:
    products: dict[str, Product] = {}

    for row in _read_csv_rows(csv_path):
        product = Product(
            id=_require_text(row, "id"),
            name=_require_text(row, "name"),
            weight_kg=_require_float(row, "weight_kg"),
            length_cm=_optional_float(row, "length_cm"),
            width_cm=_optional_float(row, "width_cm"),
            height_cm=_optional_float(row, "height_cm"),
        )
        _store_unique(products, product.id, product, csv_path)

    return products


def load_solver_inputs(data_dir: str | Path) -> SolverInputs:
    base_dir = Path(data_dir)
    settings = load_settings_csv(base_dir / "settings.csv")
    edges = load_edges_csv(base_dir / "edges.csv")

    return SolverInputs(
        nodes=load_nodes_csv(base_dir / "nodes.csv"),
        edges=edges,
        graph=build_graph_from_edges(edges),
        trucks=load_trucks_csv(base_dir / "trucks.csv", settings),
        demand=load_demand_csv(base_dir / "demand.csv"),
        warehouse_stock=load_warehouse_stock_csv(base_dir / "warehouse_stock.csv"),
        products=load_products_csv(base_dir / "products.csv"),
        settings=settings,
    )


def save_routes_csv(csv_path: str | Path, rows: list[dict[str, object]]) -> Path:
    fieldnames = [
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
    ]
    return _write_csv_rows(csv_path, fieldnames, rows)


def save_route_cargo_csv(csv_path: str | Path, rows: list[dict[str, object]]) -> Path:
    fieldnames = ["route_id", "stop_node_id", "product_id", "qty_kg"]
    return _write_csv_rows(csv_path, fieldnames, rows)


def save_warehouse_stock_csv(csv_path: str | Path, rows: list[dict[str, object]]) -> Path:
    fieldnames = ["warehouse_id", "product_id", "quantity_kg", "reserved_kg"]
    return _write_csv_rows(csv_path, fieldnames, rows)


def save_solver_output_csvs(
    output_dir: str | Path,
    routes_rows: list[dict[str, object]],
    route_cargo_rows: list[dict[str, object]],
    warehouse_stock_rows: list[dict[str, object]],
) -> dict[str, Path]:
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    return {
        "routes": save_routes_csv(base_dir / "routes.csv", routes_rows),
        "route_cargo": save_route_cargo_csv(base_dir / "route_cargo.csv", route_cargo_rows),
        "warehouse_stock": save_warehouse_stock_csv(
            base_dir / "warehouse_stock.csv",
            warehouse_stock_rows,
        ),
    }


def _read_csv_rows(csv_path: str | Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        return list(reader)


def _parse_setting_value(key: str, raw_value: str) -> SettingsValue:
    if key in {
        "fuel_price",
        "driver_hourly_default",
        "avg_speed_default",
        "amortization_default",
        "maintenance_default",
        "max_detour_ratio",
    }:
        return float(raw_value)

    if key == "unload_min_default":
        return int(float(raw_value))

    if key == "departure_time_default":
        return time.fromisoformat(raw_value)

    if raw_value in {"0", "1"}:
        return raw_value == "1"

    return raw_value


def _float_with_setting_default(
    row: dict[str, str],
    field_name: str,
    settings: dict[str, SettingsValue],
) -> float:
    local_value = _optional_float(row, field_name)
    if local_value is not None:
        return local_value

    setting_key = _TRUCK_DEFAULTS[field_name]
    setting_value = settings.get(setting_key)
    if not isinstance(setting_value, (float, int)):
        raise ValueError(
            f"Missing numeric setting '{setting_key}' required for trucks default resolution"
        )

    return float(setting_value)


def _require_text(row: dict[str, str], field_name: str) -> str:
    raw_value = _normalized_value(row.get(field_name))
    if raw_value is None:
        raise ValueError(f"Missing required value '{field_name}' in row: {row}")
    return raw_value


def _require_float(row: dict[str, str], field_name: str) -> float:
    raw_value = _require_text(row, field_name)
    return float(raw_value)


def _optional_float(row: dict[str, str], field_name: str) -> float | None:
    raw_value = _normalized_value(row.get(field_name))
    if raw_value is None:
        return None
    return float(raw_value)


def _require_bool(row: dict[str, str], field_name: str) -> bool:
    raw_value = _require_text(row, field_name).lower()
    if raw_value in {"1", "true", "yes"}:
        return True
    if raw_value in {"0", "false", "no"}:
        return False
    raise ValueError(f"Unsupported boolean value for '{field_name}': {raw_value}")


def _optional_datetime(row: dict[str, str], field_name: str) -> datetime | None:
    raw_value = _normalized_value(row.get(field_name))
    if raw_value is None:
        return None
    return datetime.fromisoformat(raw_value)


def _normalized_value(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None

    value = raw_value.strip()
    if value.lower() in _NULL_VALUES:
        return None

    return value


def _store_unique(store: dict[Any, Any], key: Any, value: Any, csv_path: str | Path) -> None:
    if key in store:
        raise ValueError(f"Duplicate key {key!r} found in {csv_path}")
    store[key] = value


def _write_csv_rows(
    csv_path: str | Path,
    fieldnames: list[str],
    rows: list[dict[str, object]],
) -> Path:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})

    return path


__all__ = [
    "DemandKey",
    "DemandRecord",
    "Edge",
    "Node",
    "Product",
    "SettingsValue",
    "SolverInputs",
    "Truck",
    "WarehouseStockRecord",
    "load_demand_csv",
    "load_edges_csv",
    "load_nodes_csv",
    "load_products_csv",
    "load_settings_csv",
    "load_solver_inputs",
    "load_trucks_csv",
    "load_warehouse_stock_csv",
    "save_route_cargo_csv",
    "save_routes_csv",
    "save_solver_output_csvs",
    "save_warehouse_stock_csv",
]
