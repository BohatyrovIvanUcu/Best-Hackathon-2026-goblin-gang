from __future__ import annotations

import sqlite3
import shutil
import unittest
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backend.database import (
    apply_reroute_request,
    complete_route_stop,
    complete_truck_loading,
    depart_truck,
    fetch_warehouse_dashboard_data,
    fetch_route_by_truck_id,
    fetch_route_execution_data,
    fetch_routes_data,
    issue_outbound_route_item,
    import_demo_data,
    initialize_database,
    load_dynamic_solver_inputs_from_db,
    mark_inbound_route_arrived,
    receive_inbound_route,
    run_solver_and_persist,
    start_truck_loading,
)
from backend.db.helpers import connect
from backend.db.workflows import _build_locked_prefix_route_plan
from solver.routing import RoutePlan, solve_network


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DATA_DIR = REPO_ROOT / "demo_data"
TEST_TMP_ROOT = REPO_ROOT / ".tmp_tests"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


class ExecutionRoutingTests(unittest.TestCase):
    def create_db_path(self) -> Path:
        temp_dir = TEST_TMP_ROOT / uuid4().hex
        temp_dir.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir / "logiflow.db"

    def seed_demo_db(self) -> Path:
        database_path = self.create_db_path()
        import_demo_data(database_path, DEMO_DATA_DIR)
        return database_path

    def seed_small_db(self, *, demand_by_store: dict[str, float]) -> Path:
        database_path = self.create_db_path()
        initialize_database(database_path)
        connection = connect(database_path)
        try:
            connection.execute("BEGIN")
            connection.executemany(
                """
                INSERT INTO nodes(id, name, type, capacity_kg, lat, lon)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                [
                    ("W1", "Warehouse 1", "warehouse", 5000.0, 50.45, 30.52),
                    ("S1", "Store 1", "store", 100.0, 50.46, 30.53),
                    ("S2", "Store 2", "store", 100.0, 50.47, 30.54),
                    ("S3", "Store 3", "store", 100.0, 50.48, 30.55),
                ],
            )
            connection.executemany(
                """
                INSERT INTO edges(from_id, to_id, distance_km)
                VALUES(?, ?, ?)
                """,
                [
                    ("W1", "S1", 1.0),
                    ("W1", "S2", 2.0),
                    ("W1", "S3", 3.0),
                    ("S1", "S2", 1.0),
                    ("S2", "S3", 1.0),
                    ("S1", "S3", 2.0),
                ],
            )
            connection.execute(
                """
                INSERT INTO products(id, name, weight_kg, length_cm, width_cm, height_cm)
                VALUES('P1', 'Product 1', 1.0, 1.0, 1.0, 1.0)
                """
            )
            connection.execute(
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
                VALUES('T1', 'Truck 1', 'truck', 200.0, 10.0, 'W1', 100.0, 50.0, 1.0, 1.0)
                """
            )
            connection.execute(
                """
                INSERT INTO warehouse_stock(warehouse_id, product_id, quantity_kg, reserved_kg)
                VALUES('W1', 'P1', 1000.0, 0.0)
                """
            )
            updated_at = datetime(2026, 4, 4, 12, 0, 0).strftime("%Y-%m-%d %H:%M:%S")
            demand_rows = {
                "S1": float(demand_by_store.get("S1", 0.0)),
                "S2": float(demand_by_store.get("S2", 0.0)),
                "S3": float(demand_by_store.get("S3", 0.0)),
            }
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
                VALUES(?, 'P1', 0.0, 10.0, ?, 'CRITICAL', 0, ?)
                """,
                [
                    (store_id, qty, updated_at)
                    for store_id, qty in sorted(demand_rows.items())
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return database_path

    def seed_warehouse_worker_db(self) -> Path:
        database_path = self.create_db_path()
        initialize_database(database_path)
        connection = connect(database_path)
        try:
            connection.execute("BEGIN")
            connection.executemany(
                """
                INSERT INTO nodes(id, name, type, capacity_kg, lat, lon)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                [
                    ("F1", "Завод 1", "factory", 5000.0, 50.45, 30.50),
                    ("W1", "Склад Київ", "warehouse", 5000.0, 50.46, 30.52),
                    ("S1", "Магазин 1", "store", 100.0, 50.47, 30.54),
                ],
            )
            connection.executemany(
                """
                INSERT INTO products(id, name, weight_kg, length_cm, width_cm, height_cm)
                VALUES(?, ?, 1.0, 1.0, 1.0, 1.0)
                """,
                [
                    ("P1", "Вода",),
                    ("P2", "Батарейки",),
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
                VALUES(?, ?, 'truck', ?, 10.0, ?, 100.0, 50.0, 1.0, 1.0)
                """,
                [
                    ("TIN", "Вхідна фура", 300.0, "F1"),
                    ("TOUT", "Вихідна фура", 300.0, "W1"),
                ],
            )
            connection.executemany(
                """
                INSERT INTO warehouse_stock(warehouse_id, product_id, quantity_kg, reserved_kg)
                VALUES(?, ?, ?, ?)
                """,
                [
                    ("W1", "P1", 500.0, 80.0),
                    ("W1", "P2", 220.0, 60.0),
                ],
            )
            created_at = "2026-04-05 08:00:00"
            timeline_inbound = (
                '[{"node_id":"F1","event":"departure","time":"08:00"},'
                '{"node_id":"W1","event":"arrival","time":"09:30"},'
                '{"node_id":"F1","event":"return","time":"11:00"}]'
            )
            timeline_outbound = (
                '[{"node_id":"W1","event":"departure","time":"10:00"},'
                '{"node_id":"S1","event":"arrival","time":"11:00"},'
                '{"node_id":"W1","event":"return","time":"12:00"}]'
            )
            connection.executemany(
                """
                INSERT INTO routes(
                    id,
                    truck_id,
                    supersedes_route_id,
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
                VALUES(?, ?, NULL, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'ok', NULL, ?, ?, 1)
                """,
                [
                    (1, "TIN", 1, '["F1", "W1", "F1"]', 42.0, 900.0, 2.0, 3.0, "08:00", "11:00", timeline_inbound, created_at),
                    (2, "TOUT", 2, '["W1", "S1", "W1"]', 18.0, 450.0, 1.0, 2.0, "10:00", "12:00", timeline_outbound, created_at),
                ],
            )
            connection.executemany(
                """
                INSERT INTO route_cargo(route_id, stop_node_id, product_id, qty_kg)
                VALUES(?, ?, ?, ?)
                """,
                [
                    (1, "W1", "P1", 120.0),
                    (2, "S1", "P1", 80.0),
                    (2, "S1", "P2", 60.0),
                ],
            )
            connection.executemany(
                """
                INSERT INTO route_execution(
                    route_id,
                    status,
                    last_completed_stop_index,
                    next_stop_index,
                    started_at,
                    warehouse_arrived_at,
                    warehouse_received_at,
                    completed_at,
                    updated_at
                )
                VALUES(?, 'planned', 0, 1, NULL, NULL, NULL, NULL, ?)
                """,
                [
                    (1, created_at),
                    (2, created_at),
                ],
            )
            connection.executemany(
                """
                INSERT INTO route_cargo_state(
                    route_id,
                    stop_node_id,
                    product_id,
                    qty_reserved_kg,
                    qty_loaded_kg,
                    qty_delivered_kg
                )
                VALUES(?, ?, ?, ?, 0, 0)
                """,
                [
                    (1, "W1", "P1", 120.0),
                    (2, "S1", "P1", 80.0),
                    (2, "S1", "P2", 60.0),
                ],
            )
            connection.executemany(
                """
                INSERT INTO truck_state(
                    truck_id,
                    status,
                    active_route_id,
                    current_node_id,
                    current_lat,
                    current_lon,
                    last_completed_stop_index,
                    remaining_capacity_kg,
                    updated_at
                )
                VALUES(?, 'idle', ?, ?, NULL, NULL, 0, ?, ?)
                """,
                [
                    ("TIN", 1, "F1", 300.0, created_at),
                    ("TOUT", 2, "W1", 300.0, created_at),
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return database_path

    def first_truck_with_active_route(self, database_path: Path) -> str:
        connection = connect(database_path)
        try:
            row = connection.execute(
                """
                SELECT truck_id
                FROM truck_state
                WHERE active_route_id IS NOT NULL
                ORDER BY truck_id
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        return str(row["truck_id"])

    def active_route_id_for_truck(self, database_path: Path, truck_id: str) -> int:
        connection = connect(database_path)
        try:
            row = connection.execute(
                """
                SELECT active_route_id
                FROM truck_state
                WHERE truck_id = ?
                """,
                (truck_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["active_route_id"])
        return int(row["active_route_id"])

    def issue_all_active_route_items_if_needed(self, database_path: Path, truck_id: str) -> None:
        route_payload = fetch_route_by_truck_id(database_path, truck_id)["route"]
        if int(route_payload["leg"]) != 2:
            return
        warehouse_id = str(route_payload["stops"][0])
        load_items = route_payload["stops_details"][0]["cargo_to_load"]
        for item in load_items:
            issue_outbound_route_item(
                database_path,
                warehouse_id=warehouse_id,
                route_id=int(route_payload["id"]),
                stop_node_id=str(item["for_store"]),
                product_id=str(item["product_id"]),
            )

    def test_initialize_database_creates_execution_tables_and_supersedes_column(self) -> None:
        database_path = self.create_db_path()
        initialize_database(database_path)

        connection = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            route_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(routes)")
            }
        finally:
            connection.close()

        self.assertIn("truck_state", tables)
        self.assertIn("route_execution", tables)
        self.assertIn("route_cargo_state", tables)
        self.assertIn("supersedes_route_id", route_columns)

    def test_full_solve_seeds_execution_state(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)

        connection = connect(database_path)
        try:
            active_routes = int(
                connection.execute("SELECT COUNT(*) AS count FROM routes WHERE is_active = 1").fetchone()["count"]
            )
            execution_routes = int(
                connection.execute("SELECT COUNT(*) AS count FROM route_execution").fetchone()["count"]
            )
            cargo_rows = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM route_cargo WHERE route_id IN (SELECT id FROM routes WHERE is_active = 1)"
                ).fetchone()["count"]
            )
            cargo_state_rows = int(
                connection.execute("SELECT COUNT(*) AS count FROM route_cargo_state").fetchone()["count"]
            )
            trucks = int(connection.execute("SELECT COUNT(*) AS count FROM trucks").fetchone()["count"])
            truck_state_rows = int(connection.execute("SELECT COUNT(*) AS count FROM truck_state").fetchone()["count"])
            execution_row = connection.execute(
                """
                SELECT status, last_completed_stop_index, next_stop_index
                FROM route_execution
                ORDER BY route_id
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(active_routes, execution_routes)
        self.assertEqual(cargo_rows, cargo_state_rows)
        self.assertEqual(trucks, truck_state_rows)
        self.assertEqual("planned", execution_row["status"])
        self.assertEqual(0, execution_row["last_completed_stop_index"])
        self.assertEqual(1, execution_row["next_stop_index"])

    def test_depart_rejects_invalid_status_transition(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)
        truck_id = self.first_truck_with_active_route(database_path)

        with self.assertRaises(ValueError):
            depart_truck(database_path, truck_id=truck_id)

    def test_loading_complete_moves_reserved_cargo_to_loaded(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)
        truck_id = self.first_truck_with_active_route(database_path)
        route_id = self.active_route_id_for_truck(database_path, truck_id)

        start_truck_loading(database_path, truck_id=truck_id)
        self.issue_all_active_route_items_if_needed(database_path, truck_id)
        result = complete_truck_loading(database_path, truck_id=truck_id)

        connection = connect(database_path)
        try:
            cargo_totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(qty_reserved_kg), 0) AS reserved_total,
                    COALESCE(SUM(qty_loaded_kg), 0) AS loaded_total
                FROM route_cargo_state
                WHERE route_id = ?
                """,
                (route_id,),
            ).fetchone()
            truck_state_row = connection.execute(
                """
                SELECT status, remaining_capacity_kg
                FROM truck_state
                WHERE truck_id = ?
                """,
                (truck_id,),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual("loaded", result["truck_status"])
        self.assertEqual(0.0, float(cargo_totals["reserved_total"]))
        self.assertGreater(float(cargo_totals["loaded_total"]), 0.0)
        self.assertEqual("loaded", truck_state_row["status"])
        self.assertLess(float(truck_state_row["remaining_capacity_kg"]), 1e9)

    def test_stop_complete_advances_progress_and_resets_truck(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)
        truck_id = self.first_truck_with_active_route(database_path)
        route_id = self.active_route_id_for_truck(database_path, truck_id)

        start_truck_loading(database_path, truck_id=truck_id)
        self.issue_all_active_route_items_if_needed(database_path, truck_id)
        complete_truck_loading(database_path, truck_id=truck_id)
        depart_truck(database_path, truck_id=truck_id)

        execution = fetch_route_execution_data(database_path, route_id)
        while execution["next_stop_index"] is not None:
            complete_route_stop(database_path, route_id=route_id)
            execution = fetch_route_execution_data(database_path, route_id)

        self.assertEqual("completed", execution["route_status"])
        self.assertEqual("idle", execution["truck_state"]["status"])

    def test_route_reads_include_execution_state(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)

        routes_payload = fetch_routes_data(database_path)
        self.assertTrue(routes_payload["routes"])
        route_item = routes_payload["routes"][0]
        self.assertIn("execution", route_item)
        self.assertIn("truck_state", route_item["execution"])

        detail_payload = fetch_route_by_truck_id(database_path, route_item["truck_id"])
        self.assertIn("execution", detail_payload["route"])
        self.assertIn("cargo_state", detail_payload["route"]["execution"])

    def test_warehouse_dashboard_is_scoped_to_selected_warehouse(self) -> None:
        database_path = self.seed_demo_db()
        run_solver_and_persist(database_path)

        kyiv_dashboard = fetch_warehouse_dashboard_data(database_path, "WAREHOUSE_1")
        kharkiv_dashboard = fetch_warehouse_dashboard_data(database_path, "WAREHOUSE_2")

        self.assertEqual("WAREHOUSE_1", kyiv_dashboard["warehouse"]["id"])
        self.assertEqual("WAREHOUSE_2", kharkiv_dashboard["warehouse"]["id"])
        self.assertTrue(all(route["from_node_name"] for route in kyiv_dashboard["inbound"]))
        self.assertTrue(
            all(route["items"] for route in kyiv_dashboard["outbound"]),
            "Outbound routes should include item-level warehouse issue rows",
        )
        self.assertNotEqual(
            {route["route_id"] for route in kyiv_dashboard["outbound"]},
            {route["route_id"] for route in kharkiv_dashboard["outbound"]},
        )

    def test_inbound_arrive_then_receive_updates_stock_and_queue(self) -> None:
        database_path = self.seed_warehouse_worker_db()

        dashboard_before = fetch_warehouse_dashboard_data(database_path, "W1")
        inbound_route = dashboard_before["inbound"][0]
        received_item = inbound_route["items"][0]
        stock_before = next(
            row for row in dashboard_before["stock"] if row["product_id"] == received_item["product_id"]
        )

        with self.assertRaises(ValueError):
            receive_inbound_route(database_path, warehouse_id="W1", route_id=inbound_route["route_id"])

        mark_inbound_route_arrived(
            database_path,
            warehouse_id="W1",
            route_id=inbound_route["route_id"],
        )
        dashboard_arrived = fetch_warehouse_dashboard_data(database_path, "W1")
        same_route_after_arrive = next(
            route for route in dashboard_arrived["inbound"] if route["route_id"] == inbound_route["route_id"]
        )
        self.assertFalse(same_route_after_arrive["can_arrive"])
        self.assertTrue(same_route_after_arrive["can_receive"])

        receive_inbound_route(
            database_path,
            warehouse_id="W1",
            route_id=inbound_route["route_id"],
        )
        dashboard_after = fetch_warehouse_dashboard_data(database_path, "W1")
        stock_after = next(
            row for row in dashboard_after["stock"] if row["product_id"] == received_item["product_id"]
        )

        self.assertNotIn(
            inbound_route["route_id"],
            {route["route_id"] for route in dashboard_after["inbound"]},
        )
        self.assertAlmostEqual(
            float(stock_before["quantity_kg"]) + float(received_item["qty_kg"]),
            float(stock_after["quantity_kg"]),
            places=2,
        )

    def test_outbound_items_must_be_issued_before_loading_complete(self) -> None:
        database_path = self.seed_warehouse_worker_db()

        dashboard = fetch_warehouse_dashboard_data(database_path, "W1")
        outbound_route = dashboard["outbound"][0]
        truck_id = outbound_route["truck_id"]

        start_truck_loading(database_path, truck_id=truck_id)
        with self.assertRaises(ValueError):
            complete_truck_loading(database_path, truck_id=truck_id)

        for item in outbound_route["items"]:
            issue_outbound_route_item(
                database_path,
                warehouse_id="W1",
                route_id=outbound_route["route_id"],
                stop_node_id=item["stop_node_id"],
                product_id=item["product_id"],
            )

        result = complete_truck_loading(database_path, truck_id=truck_id)
        dashboard_after = fetch_warehouse_dashboard_data(database_path, "W1")
        same_route = next(route for route in dashboard_after["outbound"] if route["route_id"] == outbound_route["route_id"])

        self.assertEqual("loaded", result["truck_status"])
        self.assertEqual(0.0, float(same_route["total_reserved_kg"]))
        self.assertGreater(float(same_route["total_loaded_kg"]), 0.0)

    def test_reroute_replaces_idle_route_and_preserves_lineage(self) -> None:
        database_path = self.seed_small_db(demand_by_store={"S1": 10.0})
        run_solver_and_persist(database_path)
        before_route = fetch_route_by_truck_id(database_path, "T1")["route"]

        result = apply_reroute_request(
            database_path,
            node_id="S2",
            product_id="P1",
            qty=15.0,
            reroute_reason="idle_test",
            allow_in_progress=False,
        )

        self.assertIn("T1", result["changed_truck_ids"])
        new_route_id = result["route_id_mapping"][before_route["id"]]
        after_route = fetch_route_by_truck_id(database_path, "T1")["route"]
        self.assertEqual(new_route_id, after_route["id"])
        self.assertEqual(before_route["id"], after_route["supersedes_route_id"])
        self.assertIn("S2", after_route["stops"])

    def test_reroute_with_allow_in_progress_false_leaves_en_route_truck_unchanged(self) -> None:
        database_path = self.seed_small_db(demand_by_store={"S1": 10.0})
        run_solver_and_persist(database_path)
        truck_id = "T1"
        route_id = self.active_route_id_for_truck(database_path, truck_id)

        start_truck_loading(database_path, truck_id=truck_id)
        self.issue_all_active_route_items_if_needed(database_path, truck_id)
        complete_truck_loading(database_path, truck_id=truck_id)
        depart_truck(database_path, truck_id=truck_id)

        result = apply_reroute_request(
            database_path,
            node_id="S2",
            product_id="P1",
            qty=15.0,
            reroute_reason="phase1_test",
            allow_in_progress=False,
        )

        self.assertNotIn(truck_id, result["changed_truck_ids"])
        self.assertEqual(route_id, self.active_route_id_for_truck(database_path, truck_id))

    def test_locked_prefix_helper_preserves_prefix_and_cargo(self) -> None:
        database_path = self.seed_small_db(
            demand_by_store={
                "S1": 10.0,
                "S2": 10.0,
                "S3": 10.0,
            }
        )
        run_solver_and_persist(database_path)
        truck_id = "T1"
        route_id = self.active_route_id_for_truck(database_path, truck_id)

        start_truck_loading(database_path, truck_id=truck_id)
        self.issue_all_active_route_items_if_needed(database_path, truck_id)
        complete_truck_loading(database_path, truck_id=truck_id)
        depart_truck(database_path, truck_id=truck_id)
        complete_route_stop(database_path, route_id=route_id)

        dynamic_inputs = load_dynamic_solver_inputs_from_db(database_path)
        active_route_id = dynamic_inputs.truck_states[truck_id].active_route_id
        self.assertIsNotNone(active_route_id)
        active_route = dynamic_inputs.active_routes[int(active_route_id)]
        route_execution = dynamic_inputs.route_execution[int(active_route_id)]
        distances = solve_network(dynamic_inputs.static_inputs).distances

        new_plan = RoutePlan(
            truck_id=truck_id,
            leg=2,
            stops=("W1", "S3", "S2", "W1"),
            total_km=0.0,
            drive_hours=0.0,
            unload_hours=0.0,
            total_elapsed_h=0.0,
            total_cost=0.0,
            days=1,
            departure_time="08:00",
            arrival_time="08:00",
            time_status="ok",
            time_warning=None,
            timeline=tuple(),
            cargo_by_stop_product={},
            stop_priority_by_node={},
        )
        merged_plan = _build_locked_prefix_route_plan(
            dynamic_inputs=dynamic_inputs,
            active_route=active_route,
            route_execution=route_execution,
            new_plan=new_plan,
            departure_time=None,
            distances_by_source=distances,
        )

        self.assertIsNotNone(merged_plan)
        self.assertEqual(tuple(active_route.stops[:2]), tuple(merged_plan.stops[:2]))
        self.assertEqual(set(active_route.stops[2:-1]), set(merged_plan.stops[2:-1]))
        self.assertEqual(dict(active_route.cargo_by_stop_product), dict(merged_plan.cargo_by_stop_product))


if __name__ == "__main__":
    unittest.main()
