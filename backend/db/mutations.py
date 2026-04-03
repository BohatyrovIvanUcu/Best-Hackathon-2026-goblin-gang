from __future__ import annotations

from datetime import datetime
from pathlib import Path

from solver.priority import compute_priority

from backend.db.constants import ALLOWED_SETTINGS_KEYS
from backend.db.helpers import connect, format_kg, normalize_setting_update_value, serialize_setting_value


def update_stock_after_shipment(
    database_path: Path,
    warehouse_id: str,
    product_id: str,
    qty_shipped_kg: float,
) -> dict[str, object]:
    if qty_shipped_kg <= 0:
        raise ValueError("qty_shipped_kg must be greater than 0")

    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT quantity_kg, reserved_kg
            FROM warehouse_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (warehouse_id, product_id),
        ).fetchone()

        if row is None:
            raise LookupError(
                f"Stock row not found for warehouse_id={warehouse_id} and product_id={product_id}"
            )

        quantity_before = float(row["quantity_kg"])
        reserved_before = float(row["reserved_kg"])
        if quantity_before < qty_shipped_kg:
            available_text = format_kg(quantity_before)
            requested_text = format_kg(qty_shipped_kg)
            raise RuntimeError(
                f"Недостатньо товару: доступно {available_text} кг, запрошено {requested_text} кг"
            )

        quantity_after = round(quantity_before - qty_shipped_kg, 2)
        reserved_after = round(max(0.0, reserved_before - qty_shipped_kg), 2)

        connection.execute(
            """
            UPDATE warehouse_stock
            SET quantity_kg = ?, reserved_kg = ?
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (
                quantity_after,
                reserved_after,
                warehouse_id,
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
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "quantity_kg_before": quantity_before,
        "quantity_kg_after": quantity_after,
        "reserved_kg_before": reserved_before,
        "reserved_kg_after": reserved_after,
    }


def update_demand_current_stock(
    database_path: Path,
    node_id: str,
    product_id: str,
    current_stock: float,
) -> dict[str, object]:
    if current_stock < 0:
        raise ValueError("current_stock must be greater than or equal to 0")

    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT node_id, product_id, current_stock, min_stock, requested_qty, priority, is_urgent
            FROM demand
            WHERE node_id = ? AND product_id = ?
            """,
            (node_id, product_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Demand row not found for node_id={node_id} and product_id={product_id}"
            )

        min_stock = float(row["min_stock"])
        previous_priority = str(row["priority"])
        requested_qty = round(max(0.0, min_stock - current_stock), 2)
        next_priority = compute_priority(
            current_stock=current_stock,
            min_stock=min_stock,
            is_urgent=bool(row["is_urgent"]),
        )
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        connection.execute(
            """
            UPDATE demand
            SET current_stock = ?, requested_qty = ?, priority = ?, updated_at = ?
            WHERE node_id = ? AND product_id = ?
            """,
            (
                current_stock,
                requested_qty,
                next_priority,
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
        "current_stock": current_stock,
        "min_stock": min_stock,
        "requested_qty": requested_qty,
        "priority": next_priority,
        "priority_changed": next_priority != previous_priority,
        "previous_priority": previous_priority,
    }


def update_settings_values(
    database_path: Path,
    updates: dict[str, object],
) -> dict[str, object]:
    if not updates:
        raise ValueError("No settings provided for update")

    unknown_keys = sorted(set(updates) - set(ALLOWED_SETTINGS_KEYS))
    if unknown_keys:
        allowed_keys = ", ".join(ALLOWED_SETTINGS_KEYS)
        raise ValueError(
            f"Невідомий ключ: {unknown_keys[0]}. Допустимі: {allowed_keys}"
        )

    normalized_updates = {
        key: normalize_setting_update_value(key, value)
        for key, value in updates.items()
    }

    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        connection.executemany(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [
                (key, serialize_setting_value(value))
                for key, value in normalized_updates.items()
            ],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "updated": normalized_updates,
    }
