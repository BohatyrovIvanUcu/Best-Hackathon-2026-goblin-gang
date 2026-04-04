from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.db.constants import (
    DEFAULT_SETTINGS,
    INDEX_STATEMENTS,
    SCHEMA_STATEMENTS,
    WAREHOUSE_STOCK_TABLE_SQL,
)


def _apply_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)

    for statement in INDEX_STATEMENTS:
        connection.execute(statement)


def _seed_default_settings(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO NOTHING;
        """,
        DEFAULT_SETTINGS.items(),
    )


def _migrate_warehouse_stock_constraint(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'warehouse_stock'
        """
    ).fetchone()
    if row is None or row[0] is None:
        return False

    normalized_sql = "".join(row[0].lower().split())
    legacy_constraint = "reserved_kgrealdefault0check(reserved_kg>=0andreserved_kg<=quantity_kg)"
    if legacy_constraint not in normalized_sql:
        return False

    connection.execute("ALTER TABLE warehouse_stock RENAME TO warehouse_stock_legacy;")
    connection.execute(WAREHOUSE_STOCK_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO warehouse_stock(warehouse_id, product_id, quantity_kg, reserved_kg)
        SELECT warehouse_id, product_id, quantity_kg, reserved_kg
        FROM warehouse_stock_legacy;
        """
    )
    connection.execute("DROP TABLE warehouse_stock_legacy;")
    return True


def _column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row[1]) == column_name for row in rows)


def _migrate_routes_supersedes_column(connection: sqlite3.Connection) -> bool:
    if _column_exists(connection, "routes", "supersedes_route_id"):
        return False

    connection.execute("ALTER TABLE routes ADD COLUMN supersedes_route_id INTEGER DEFAULT NULL;")
    return True


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        _apply_schema(connection)
        if _migrate_warehouse_stock_constraint(connection):
            _apply_schema(connection)
        if _migrate_routes_supersedes_column(connection):
            _apply_schema(connection)
        _seed_default_settings(connection)
        connection.commit()
    finally:
        connection.close()
