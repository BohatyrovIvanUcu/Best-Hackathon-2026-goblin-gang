from __future__ import annotations

from backend.db.constants import ALLOWED_SETTINGS_KEYS, DEFAULT_SETTINGS
from backend.db.generator import generate_random_dataset
from backend.db.importer import clear_import_target_tables, import_demo_data
from backend.db.execution import (
    complete_route_stop,
    complete_truck_loading,
    depart_truck,
    start_truck_loading,
    update_truck_position,
)
from backend.db.mutations import (
    update_demand_current_stock,
    update_settings_values,
    update_stock_after_shipment,
)
from backend.db.read_api import (
    fetch_demand_data,
    fetch_network_data,
    fetch_route_by_truck_id,
    fetch_route_execution_data,
    fetch_routes_data,
    fetch_settings_data,
    fetch_stock_data,
)
from backend.db.schema import initialize_database
from backend.db.solver_runtime import (
    build_solve_response,
    build_solve_summary,
    load_dynamic_solver_inputs_from_db,
    load_solver_inputs_from_db,
    persist_solve_result,
    run_solver_and_persist,
)
from backend.db.workflows import apply_reroute_request, apply_urgent_request, mark_demand_as_urgent

__all__ = [
    "ALLOWED_SETTINGS_KEYS",
    "DEFAULT_SETTINGS",
    "build_solve_response",
    "build_solve_summary",
    "clear_import_target_tables",
    "complete_route_stop",
    "complete_truck_loading",
    "depart_truck",
    "fetch_demand_data",
    "fetch_network_data",
    "fetch_route_by_truck_id",
    "fetch_route_execution_data",
    "fetch_routes_data",
    "fetch_settings_data",
    "fetch_stock_data",
    "generate_random_dataset",
    "import_demo_data",
    "initialize_database",
    "load_dynamic_solver_inputs_from_db",
    "load_solver_inputs_from_db",
    "apply_reroute_request",
    "apply_urgent_request",
    "mark_demand_as_urgent",
    "persist_solve_result",
    "run_solver_and_persist",
    "start_truck_loading",
    "update_demand_current_stock",
    "update_settings_values",
    "update_stock_after_shipment",
    "update_truck_position",
]
