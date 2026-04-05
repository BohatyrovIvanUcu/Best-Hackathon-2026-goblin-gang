from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, time
from pathlib import Path


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def read_optional_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def serialize_setting_value(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def parse_setting_value(key: str, raw_value: str) -> object:
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


def normalize_setting_update_value(key: str, value: object) -> object:
    if key in {
        "fuel_price",
        "driver_hourly_default",
        "avg_speed_default",
        "amortization_default",
        "maintenance_default",
        "max_detour_ratio",
    }:
        if not isinstance(value, (int, float)):
            raise ValueError(f"Setting '{key}' must be numeric")
        return float(value)

    if key == "unload_min_default":
        if not isinstance(value, (int, float)):
            raise ValueError(f"Setting '{key}' must be numeric")
        return int(value)

    if key == "departure_time_default":
        if not isinstance(value, str):
            raise ValueError(f"Setting '{key}' must be a string in HH:MM format")
        return time.fromisoformat(value)

    if key == "min_priority_enroute":
        if not isinstance(value, str):
            raise ValueError(f"Setting '{key}' must be a string")
        normalized = value.upper()
        if normalized not in {"NORMAL", "ELEVATED", "CRITICAL"}:
            raise ValueError(
                "Setting 'min_priority_enroute' must be one of: NORMAL, ELEVATED, CRITICAL"
            )
        return normalized

    return value


def parse_datetime_value(raw_value: str | None) -> object:
    if not raw_value:
        return None
    return datetime.fromisoformat(raw_value)


def normalize_route_row(row: dict[str, str]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "truck_id": row["truck_id"],
        "supersedes_route_id": int(row["supersedes_route_id"]) if row.get("supersedes_route_id") else None,
        "leg": int(row["leg"]),
        "stops": row["stops"],
        "total_km": float(row["total_km"]),
        "total_cost": float(row["total_cost"]),
        "drive_hours": float(row["drive_hours"]),
        "total_elapsed_h": float(row["total_elapsed_h"]),
        "days": int(row["days"]),
        "departure_time": row["departure_time"],
        "arrival_time": row["arrival_time"],
        "time_status": row["time_status"],
        "time_warning": row["time_warning"] or None,
        "timeline": row["timeline"],
        "created_at": row["created_at"],
        "is_active": int(row["is_active"]),
    }


def normalize_route_cargo_row(row: dict[str, str]) -> dict[str, object]:
    return {
        "route_id": int(row["route_id"]),
        "stop_node_id": row["stop_node_id"],
        "product_id": row["product_id"],
        "qty_kg": float(row["qty_kg"]),
    }


def format_kg(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded)
