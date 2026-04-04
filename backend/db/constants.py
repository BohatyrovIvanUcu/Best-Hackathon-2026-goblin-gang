from __future__ import annotations

DEFAULT_SETTINGS: dict[str, str] = {
    "fuel_price": "52.0",
    "driver_hourly_default": "180.0",
    "avg_speed_default": "55.0",
    "amortization_default": "5.0",
    "maintenance_default": "3.0",
    "max_detour_ratio": "0.15",
    "min_priority_enroute": "ELEVATED",
    "unload_min_default": "15",
    "departure_time_default": "08:00",
}

ALLOWED_SETTINGS_KEYS: tuple[str, ...] = tuple(DEFAULT_SETTINGS.keys())

WAREHOUSE_STOCK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS warehouse_stock (
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity_kg REAL NOT NULL CHECK (quantity_kg >= 0),
    reserved_kg REAL DEFAULT 0 CHECK (reserved_kg >= 0),
    PRIMARY KEY (warehouse_id, product_id),
    FOREIGN KEY (warehouse_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
"""

TRUCK_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS truck_state (
    truck_id TEXT PRIMARY KEY,
    status TEXT NOT NULL
        CHECK (status IN ('idle', 'loading', 'loaded', 'en_route', 'unloading', 'completed', 'blocked')),
    active_route_id INTEGER DEFAULT NULL,
    current_node_id TEXT DEFAULT NULL,
    current_lat REAL DEFAULT NULL,
    current_lon REAL DEFAULT NULL,
    last_completed_stop_index INTEGER NOT NULL DEFAULT 0 CHECK (last_completed_stop_index >= 0),
    remaining_capacity_kg REAL NOT NULL CHECK (remaining_capacity_kg >= 0),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (truck_id) REFERENCES trucks(id) ON DELETE CASCADE,
    FOREIGN KEY (current_node_id) REFERENCES nodes(id) ON DELETE SET NULL,
    FOREIGN KEY (active_route_id) REFERENCES routes(id) ON DELETE SET NULL
);
"""

ROUTE_EXECUTION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS route_execution (
    route_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL
        CHECK (status IN ('planned', 'loading', 'in_progress', 'completed', 'cancelled')),
    last_completed_stop_index INTEGER NOT NULL DEFAULT 0 CHECK (last_completed_stop_index >= 0),
    next_stop_index INTEGER DEFAULT 1 CHECK (next_stop_index IS NULL OR next_stop_index >= 0),
    started_at TEXT DEFAULT NULL,
    completed_at TEXT DEFAULT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
);
"""

ROUTE_CARGO_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS route_cargo_state (
    route_id INTEGER NOT NULL,
    stop_node_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    qty_reserved_kg REAL NOT NULL DEFAULT 0 CHECK (qty_reserved_kg >= 0),
    qty_loaded_kg REAL NOT NULL DEFAULT 0 CHECK (qty_loaded_kg >= 0),
    qty_delivered_kg REAL NOT NULL DEFAULT 0 CHECK (qty_delivered_kg >= 0),
    PRIMARY KEY (route_id, stop_node_id, product_id),
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE,
    FOREIGN KEY (stop_node_id) REFERENCES nodes(id) ON DELETE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
);
"""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('factory', 'warehouse', 'store')),
        capacity_kg REAL DEFAULT 0 CHECK (capacity_kg >= 0),
        lat REAL,
        lon REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        distance_km REAL NOT NULL CHECK (distance_km >= 0),
        PRIMARY KEY (from_id, to_id),
        FOREIGN KEY (from_id) REFERENCES nodes(id) ON DELETE CASCADE,
        FOREIGN KEY (to_id) REFERENCES nodes(id) ON DELETE CASCADE,
        CHECK (from_id <> to_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        weight_kg REAL NOT NULL CHECK (weight_kg >= 0),
        length_cm REAL DEFAULT NULL CHECK (length_cm IS NULL OR length_cm >= 0),
        width_cm REAL DEFAULT NULL CHECK (width_cm IS NULL OR width_cm >= 0),
        height_cm REAL DEFAULT NULL CHECK (height_cm IS NULL OR height_cm >= 0)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS trucks (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('semi', 'truck', 'van')),
        capacity_kg REAL NOT NULL CHECK (capacity_kg > 0),
        fuel_per_100km REAL NOT NULL CHECK (fuel_per_100km >= 0),
        depot_node_id TEXT NOT NULL,
        driver_hourly REAL DEFAULT NULL CHECK (driver_hourly IS NULL OR driver_hourly >= 0),
        avg_speed_kmh REAL DEFAULT NULL CHECK (avg_speed_kmh IS NULL OR avg_speed_kmh > 0),
        amortization_per_km REAL DEFAULT NULL CHECK (amortization_per_km IS NULL OR amortization_per_km >= 0),
        maintenance_per_km REAL DEFAULT NULL CHECK (maintenance_per_km IS NULL OR maintenance_per_km >= 0),
        FOREIGN KEY (depot_node_id) REFERENCES nodes(id) ON DELETE RESTRICT
    );
    """,
    WAREHOUSE_STOCK_TABLE_SQL,
    """
    CREATE TABLE IF NOT EXISTS demand (
        node_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        current_stock REAL NOT NULL CHECK (current_stock >= 0),
        min_stock REAL NOT NULL CHECK (min_stock >= 0),
        requested_qty REAL NOT NULL CHECK (requested_qty >= 0),
        priority TEXT NOT NULL CHECK (priority IN ('NORMAL', 'ELEVATED', 'CRITICAL')),
        is_urgent INTEGER DEFAULT 0 CHECK (is_urgent IN (0, 1)),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (node_id, product_id),
        FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS routes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        truck_id TEXT NOT NULL,
        supersedes_route_id INTEGER DEFAULT NULL,
        leg INTEGER NOT NULL CHECK (leg IN (1, 2)),
        stops TEXT NOT NULL,
        total_km REAL NOT NULL CHECK (total_km >= 0),
        total_cost REAL NOT NULL CHECK (total_cost >= 0),
        drive_hours REAL NOT NULL CHECK (drive_hours >= 0),
        total_elapsed_h REAL NOT NULL CHECK (total_elapsed_h >= 0),
        days INTEGER DEFAULT 1 CHECK (days >= 1),
        departure_time TEXT NOT NULL,
        arrival_time TEXT NOT NULL,
        time_status TEXT NOT NULL CHECK (time_status IN ('ok', 'warning', 'multiday')),
        time_warning TEXT DEFAULT NULL,
        timeline TEXT NOT NULL,
        created_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1 CHECK (is_active IN (0, 1)),
        FOREIGN KEY (truck_id) REFERENCES trucks(id) ON DELETE RESTRICT,
        FOREIGN KEY (supersedes_route_id) REFERENCES routes(id) ON DELETE SET NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS route_cargo (
        route_id INTEGER NOT NULL,
        stop_node_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        qty_kg REAL NOT NULL CHECK (qty_kg >= 0),
        PRIMARY KEY (route_id, stop_node_id, product_id),
        FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE,
        FOREIGN KEY (stop_node_id) REFERENCES nodes(id) ON DELETE RESTRICT,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    ROUTE_EXECUTION_TABLE_SQL,
    ROUTE_CARGO_STATE_TABLE_SQL,
    TRUCK_STATE_TABLE_SQL,
)

INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);",
    "CREATE INDEX IF NOT EXISTS idx_trucks_depot_node_id ON trucks(depot_node_id);",
    "CREATE INDEX IF NOT EXISTS idx_stock_product_id ON warehouse_stock(product_id);",
    "CREATE INDEX IF NOT EXISTS idx_demand_priority ON demand(priority);",
    "CREATE INDEX IF NOT EXISTS idx_demand_is_urgent ON demand(is_urgent);",
    "CREATE INDEX IF NOT EXISTS idx_routes_truck_id ON routes(truck_id);",
    "CREATE INDEX IF NOT EXISTS idx_routes_is_active ON routes(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_routes_supersedes_route_id ON routes(supersedes_route_id);",
    "CREATE INDEX IF NOT EXISTS idx_route_cargo_stop_node_id ON route_cargo(stop_node_id);",
    "CREATE INDEX IF NOT EXISTS idx_route_execution_status ON route_execution(status);",
    "CREATE INDEX IF NOT EXISTS idx_truck_state_status ON truck_state(status);",
    "CREATE INDEX IF NOT EXISTS idx_truck_state_active_route_id ON truck_state(active_route_id);",
)
