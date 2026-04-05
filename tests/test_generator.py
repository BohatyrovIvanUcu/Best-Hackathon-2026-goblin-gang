from __future__ import annotations

import json
import shutil
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.routes.upload import GenerateRequest
from backend.config import Settings
from backend.db.generator import SCALE_PRESETS, generate_random_dataset
from backend.db.helpers import connect
from backend.db.solver_runtime import load_solver_inputs_from_db, run_solver_and_persist
from backend.main import app
from solver.graph import build_distance_matrix, reconstruct_path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = REPO_ROOT / ".tmp_tests"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


class GeneratorTests(unittest.TestCase):
    def create_db_path(self) -> Path:
        temp_dir = TEST_TMP_ROOT / uuid4().hex
        temp_dir.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir / "logiflow.db"

    def fetch_nodes_and_edges(
        self,
        database_path: Path,
    ) -> tuple[dict[str, str], list[tuple[str, str]], dict[str, set[str]]]:
        connection = connect(database_path)
        try:
            node_types = {
                str(row["id"]): str(row["type"])
                for row in connection.execute("SELECT id, type FROM nodes ORDER BY id")
            }
            edges = [
                (str(row["from_id"]), str(row["to_id"]))
                for row in connection.execute("SELECT from_id, to_id FROM edges ORDER BY from_id, to_id")
            ]
        finally:
            connection.close()

        adjacency = {node_id: set() for node_id in node_types}
        for left_id, right_id in edges:
            adjacency[left_id].add(right_id)
            adjacency[right_id].add(left_id)

        return node_types, edges, adjacency

    def assert_graph_connected(self, adjacency: dict[str, set[str]]) -> None:
        start = next(iter(adjacency))
        visited = {start}
        queue = deque([start])
        while queue:
            node_id = queue.popleft()
            for neighbor_id in adjacency[node_id]:
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                queue.append(neighbor_id)
        self.assertEqual(set(adjacency), visited)

    def assert_scale_invariants(self, scale: str) -> None:
        database_path = self.create_db_path()
        result = generate_random_dataset(database_path, scale=scale)
        self.assertEqual(result["generated"]["scale"], scale)
        self.assertIsInstance(result["seed"], int)

        node_types, edges, adjacency = self.fetch_nodes_and_edges(database_path)
        allowed_pair_count = sum(
            1
            for index, left_id in enumerate(node_types)
            for right_id in list(node_types)[index + 1 :]
            if {node_types[left_id], node_types[right_id]} != {"factory", "store"}
        )

        self.assertTrue(edges)
        self.assertLess(len(edges), allowed_pair_count)
        self.assertTrue(all(adjacency[node_id] for node_id in adjacency))
        self.assert_graph_connected(adjacency)

        warehouse_ids = [node_id for node_id, node_type in node_types.items() if node_type == "warehouse"]
        self.assertTrue(warehouse_ids)
        reachable_from_warehouses: set[str] = set()
        queue = deque(warehouse_ids)
        reachable_from_warehouses.update(warehouse_ids)
        while queue:
            node_id = queue.popleft()
            for neighbor_id in adjacency[node_id]:
                if neighbor_id in reachable_from_warehouses:
                    continue
                reachable_from_warehouses.add(neighbor_id)
                queue.append(neighbor_id)

        for left_id, right_id in edges:
            self.assertNotEqual({node_types[left_id], node_types[right_id]}, {"factory", "store"})

        for node_id, node_type in node_types.items():
            if node_type == "store":
                self.assertIn(node_id, reachable_from_warehouses)

    def edge_snapshot(self, *, scale: str, seed: int | None) -> tuple[list[tuple[str, str, float]], int]:
        database_path = self.create_db_path()
        result = generate_random_dataset(database_path, scale=scale, seed=seed)
        connection = connect(database_path)
        try:
            edges = [
                (str(row["from_id"]), str(row["to_id"]), float(row["distance_km"]))
                for row in connection.execute(
                    "SELECT from_id, to_id, distance_km FROM edges ORDER BY from_id, to_id"
                )
            ]
        finally:
            connection.close()
        return edges, int(result["seed"])

    def test_scale_presets_generate_sparse_connected_graphs(self) -> None:
        for scale in SCALE_PRESETS:
            with self.subTest(scale=scale):
                self.assert_scale_invariants(scale)

    def test_same_explicit_seed_is_repeatable(self) -> None:
        first_edges, first_seed = self.edge_snapshot(scale="medium", seed=20260405)
        second_edges, second_seed = self.edge_snapshot(scale="medium", seed=20260405)

        self.assertEqual(first_seed, second_seed)
        self.assertEqual(first_edges, second_edges)

    def test_auto_seed_changes_dataset(self) -> None:
        first_edges, first_seed = self.edge_snapshot(scale="medium", seed=None)
        second_edges, second_seed = self.edge_snapshot(scale="medium", seed=None)

        self.assertNotEqual(first_seed, second_seed)
        self.assertNotEqual(first_edges, second_edges)

    def test_scale_presets_return_expected_counts(self) -> None:
        for scale, preset in SCALE_PRESETS.items():
            with self.subTest(scale=scale):
                database_path = self.create_db_path()
                result = generate_random_dataset(database_path, scale=scale, seed=123)
                self.assertEqual(result["generated"]["factories"], preset["n_factories"])
                self.assertEqual(result["generated"]["warehouses"], preset["n_warehouses"])
                self.assertEqual(result["generated"]["stores"], preset["n_stores"])
                self.assertEqual(result["generated"]["trucks"], preset["n_trucks"])
                self.assertEqual(result["imported"]["nodes"], preset["n_factories"] + preset["n_warehouses"] + preset["n_stores"])
                self.assertEqual(result["imported"]["trucks"], preset["n_trucks"])

    def test_generate_endpoint_supports_scale_and_auto_seed(self) -> None:
        database_path = self.create_db_path()
        settings = Settings(
            app_name="Test",
            app_host="127.0.0.1",
            app_port=8000,
            debug=False,
            database_path=database_path,
            cors_origins=["http://localhost:3000"],
        )

        with patch("backend.api.routes.upload.get_settings", return_value=settings), patch(
            "backend.main.settings",
            settings,
        ):
            with TestClient(app) as client:
                response = client.post("/api/generate", json={"scale": "small"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["generated"]["scale"], "small")
        self.assertIsInstance(payload["seed"], int)
        self.assertEqual(payload["generated"]["stores"], SCALE_PRESETS["small"]["n_stores"])

    def test_solver_uses_multi_hop_shortest_paths_on_generated_graph(self) -> None:
        database_path = self.create_db_path()
        generate_random_dataset(database_path, scale="large", seed=20260405)
        run_solver_and_persist(database_path)

        solver_inputs = load_solver_inputs_from_db(database_path)
        relevant_nodes = list(solver_inputs.nodes)
        _, predecessors = build_distance_matrix(solver_inputs.graph, relevant_nodes)

        connection = connect(database_path)
        try:
            route_rows = connection.execute(
                "SELECT stops FROM routes WHERE is_active = 1 ORDER BY id"
            ).fetchall()
            direct_edges = {
                tuple(sorted((str(row["from_id"]), str(row["to_id"]))))
                for row in connection.execute("SELECT from_id, to_id FROM edges")
            }
        finally:
            connection.close()

        found_multi_hop_segment = False
        for route_row in route_rows:
            stops = tuple(json.loads(route_row["stops"]))
            for source, target in zip(stops, stops[1:]):
                if tuple(sorted((source, target))) in direct_edges:
                    continue
                path = reconstruct_path(predecessors[source], source, target)
                if len(path) > 2:
                    found_multi_hop_segment = True
                    break
            if found_multi_hop_segment:
                break

        self.assertTrue(found_multi_hop_segment)

    def test_generate_request_accepts_manual_counts_without_scale(self) -> None:
        payload = GenerateRequest(
            n_factories=2,
            n_warehouses=3,
            n_stores=12,
            n_trucks=5,
            seed=1,
        )
        self.assertIsNone(payload.scale)
        self.assertEqual(payload.n_stores, 12)


if __name__ == "__main__":
    unittest.main()
