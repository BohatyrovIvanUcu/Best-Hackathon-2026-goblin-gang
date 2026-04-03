from __future__ import annotations

import sqlite3
from pathlib import Path

from solver.io import (
    load_demand_csv,
    load_edges_csv,
    load_nodes_csv,
    load_products_csv,
    load_settings_csv,
    load_trucks_csv,
    load_warehouse_stock_csv,
)

from backend.db.helpers import (
    normalize_route_cargo_row,
    normalize_route_row,
    read_optional_csv_rows,
    serialize_setting_value,
)
from backend.db.schema import initialize_database


def import_demo_data(database_path: Path, data_dir: Path) -> dict[str, int]:
    """Load CSV data into SQLite in a repeatable transaction."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    nodes = load_nodes_csv(data_dir / "nodes.csv")
    edges = load_edges_csv(data_dir / "edges.csv")
    products = load_products_csv(data_dir / "products.csv")
    settings = load_settings_csv(data_dir / "settings.csv")
    trucks = load_trucks_csv(data_dir / "trucks.csv", settings)
    warehouse_stock = load_warehouse_stock_csv(data_dir / "warehouse_stock.csv")
    demand = load_demand_csv(data_dir / "demand.csv")
    routes_rows = read_optional_csv_rows(data_dir / "routes.csv")
    route_cargo_rows = read_optional_csv_rows(data_dir / "route_cargo.csv")

    initialize_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("BEGIN")

        clear_import_target_tables(connection)

        connection.executemany(
            """
            INSERT INTO nodes(id, name, type, capacity_kg, lat, lon)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                (node.id, node.name, node.type, node.capacity_kg, node.lat, node.lon)
                for node in nodes.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO edges(from_id, to_id, distance_km)
            VALUES(?, ?, ?)
            """,
            [(edge.from_id, edge.to_id, edge.distance_km) for edge in edges],
        )
        connection.executemany(
            """
            INSERT INTO products(id, name, weight_kg, length_cm, width_cm, height_cm)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    product.id,
                    product.name,
                    product.weight_kg,
                    product.length_cm,
                    product.width_cm,
                    product.height_cm,
                )
                for product in products.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO trucks(
                id,
                name,
                type,
                capacity_kg,
                fuel_per_100km,
                depot_node_id,
                driver_hourly,
                avg_speed_kmh,
                amortization_per_km,
                maintenance_per_km
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    truck.id,
                    truck.name,
                    truck.type,
                    truck.capacity_kg,
                    truck.fuel_per_100km,
                    truck.depot_node_id,
                    truck.driver_hourly,
                    truck.avg_speed_kmh,
                    truck.amortization_per_km,
                    truck.maintenance_per_km,
                )
                for truck in trucks.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO warehouse_stock(warehouse_id, product_id, quantity_kg, reserved_kg)
            VALUES(?, ?, ?, ?)
            """,
            [
                (
                    stock.warehouse_id,
                    stock.product_id,
                    stock.quantity_kg,
                    stock.reserved_kg,
                )
                for stock in warehouse_stock.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO demand(
                node_id,
                product_id,
                current_stock,
                min_stock,
                requested_qty,
                priority,
                is_urgent,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.node_id,
                    record.product_id,
                    record.current_stock,
                    record.min_stock,
                    record.requested_qty,
                    record.priority,
                    int(record.is_urgent),
                    record.updated_at.isoformat(sep=" ") if record.updated_at else "",
                )
                for record in demand.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [(key, serialize_setting_value(value)) for key, value in settings.items()],
        )
        if routes_rows:
            connection.executemany(
                """
                INSERT INTO routes(
                    id,
                    truck_id,
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
                VALUES(
                    :id,
                    :truck_id,
                    :leg,
                    :stops,
                    :total_km,
                    :total_cost,
                    :drive_hours,
                    :total_elapsed_h,
                    :days,
                    :departure_time,
                    :arrival_time,
                    :time_status,
                    :time_warning,
                    :timeline,
                    :created_at,
                    :is_active
                )
                """,
                [normalize_route_row(row) for row in routes_rows],
            )
        if route_cargo_rows:
            connection.executemany(
                """
                INSERT INTO route_cargo(route_id, stop_node_id, product_id, qty_kg)
                VALUES(:route_id, :stop_node_id, :product_id, :qty_kg)
                """,
                [normalize_route_cargo_row(row) for row in route_cargo_rows],
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "products": len(products),
        "trucks": len(trucks),
        "warehouse_stock": len(warehouse_stock),
        "demand": len(demand),
        "settings": len(settings),
        "routes": len(routes_rows),
        "route_cargo": len(route_cargo_rows),
    }


def clear_import_target_tables(connection: sqlite3.Connection) -> None:
    for table_name in (
        "route_cargo",
        "routes",
        "demand",
        "warehouse_stock",
        "trucks",
        "settings",
        "products",
        "edges",
        "nodes",
    ):
        connection.execute(f"DELETE FROM {table_name}")
