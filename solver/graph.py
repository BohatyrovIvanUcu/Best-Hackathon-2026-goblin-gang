from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping
from typing import Protocol, TypeAlias

Graph: TypeAlias = dict[str, dict[str, float]]
DistanceMatrix: TypeAlias = dict[str, dict[str, float]]
PredecessorMap: TypeAlias = dict[str, str | None]
PredecessorMatrix: TypeAlias = dict[str, PredecessorMap]


class EdgeLike(Protocol):
    from_id: str
    to_id: str
    distance_km: float


class TruckLike(Protocol):
    type: str
    depot_node_id: str


class DemandLike(Protocol):
    node_id: str
    requested_qty: float


def build_graph_from_edges(edges: Iterable[EdgeLike]) -> Graph:
    """Build an undirected adjacency list from edge rows."""
    graph: Graph = {}

    for edge in edges:
        if edge.distance_km < 0:
            raise ValueError(
                f"Negative edge weight is not supported by Dijkstra: "
                f"{edge.from_id} -> {edge.to_id} = {edge.distance_km}"
            )

        _add_undirected_edge(graph, edge.from_id, edge.to_id, edge.distance_km)

    return graph


def dijkstra(graph: Mapping[str, Mapping[str, float]], source: str) -> tuple[dict[str, float], PredecessorMap]:
    """Compute shortest paths from one source with a heap-based priority queue."""
    distances: dict[str, float] = {source: 0.0}
    predecessors: PredecessorMap = {source: None}
    heap: list[tuple[float, str]] = [(0.0, source)]

    while heap:
        current_distance, node_id = heapq.heappop(heap)

        if current_distance > distances.get(node_id, math.inf):
            continue

        for neighbor_id, edge_distance in graph.get(node_id, {}).items():
            candidate_distance = current_distance + edge_distance
            if candidate_distance >= distances.get(neighbor_id, math.inf):
                continue

            distances[neighbor_id] = candidate_distance
            predecessors[neighbor_id] = node_id
            heapq.heappush(heap, (candidate_distance, neighbor_id))

    return distances, predecessors


def collect_relevant_leg2_nodes(
    trucks: Mapping[str, TruckLike],
    demand_rows: Iterable[DemandLike],
) -> list[str]:
    """Collect depot nodes for trucks/vans and stores with positive demand."""
    relevant_nodes: list[str] = []
    seen: set[str] = set()

    for truck in trucks.values():
        if truck.type not in {"truck", "van"}:
            continue
        if truck.depot_node_id not in seen:
            seen.add(truck.depot_node_id)
            relevant_nodes.append(truck.depot_node_id)

    for demand_row in demand_rows:
        if demand_row.requested_qty <= 0:
            continue
        if demand_row.node_id not in seen:
            seen.add(demand_row.node_id)
            relevant_nodes.append(demand_row.node_id)

    return relevant_nodes


def build_distance_matrix(
    graph: Mapping[str, Mapping[str, float]],
    relevant_nodes: Iterable[str],
) -> tuple[DistanceMatrix, PredecessorMatrix]:
    """Run Dijkstra for every relevant node and keep only relevant distances."""
    unique_nodes = list(dict.fromkeys(relevant_nodes))
    distances_by_source: DistanceMatrix = {}
    predecessors_by_source: PredecessorMatrix = {}

    for source in unique_nodes:
        distances, predecessors = dijkstra(graph, source)
        distances_by_source[source] = {
            target: distances.get(target, math.inf) for target in unique_nodes
        }
        predecessors_by_source[source] = predecessors

    return distances_by_source, predecessors_by_source


def build_leg2_distance_matrix(
    graph: Mapping[str, Mapping[str, float]],
    trucks: Mapping[str, TruckLike],
    demand_rows: Iterable[DemandLike],
) -> tuple[list[str], DistanceMatrix, PredecessorMatrix]:
    """Build stage-2 shortest-path data for all leg-2 relevant nodes."""
    relevant_nodes = collect_relevant_leg2_nodes(trucks, demand_rows)
    distances_by_source, predecessors_by_source = build_distance_matrix(graph, relevant_nodes)
    return relevant_nodes, distances_by_source, predecessors_by_source


def reconstruct_path(
    predecessors: Mapping[str, str | None],
    start: str,
    end: str,
) -> list[str]:
    """Reconstruct the shortest path from a source-specific predecessor map."""
    if start == end:
        return [start]

    if end not in predecessors:
        raise ValueError(f"No path from {start} to {end}")

    path: list[str] = []
    current: str | None = end

    while current is not None:
        path.append(current)
        if current == start:
            path.reverse()
            return path
        current = predecessors.get(current)

    raise ValueError(f"No path from {start} to {end}")


def reconstruct_route_path(
    route: Iterable[str],
    predecessors_by_source: Mapping[str, Mapping[str, str | None]],
) -> list[str]:
    route_nodes = list(route)
    if not route_nodes:
        return []

    full_path: list[str] = []
    for source, target in zip(route_nodes, route_nodes[1:]):
        predecessors = predecessors_by_source.get(source)
        if predecessors is None:
            raise ValueError(f"Missing predecessors for route source {source}")
        segment = reconstruct_path(predecessors, source, target)
        full_path.extend(segment if not full_path else segment[1:])

    return full_path or route_nodes[:1]


def _add_undirected_edge(graph: Graph, left: str, right: str, distance_km: float) -> None:
    graph.setdefault(left, {})
    graph.setdefault(right, {})

    previous_left = graph[left].get(right, distance_km)
    previous_right = graph[right].get(left, distance_km)
    best_distance = min(distance_km, previous_left, previous_right)

    graph[left][right] = best_distance
    graph[right][left] = best_distance


__all__ = [
    "DistanceMatrix",
    "Graph",
    "PredecessorMap",
    "PredecessorMatrix",
    "build_distance_matrix",
    "build_leg2_distance_matrix",
    "build_graph_from_edges",
    "collect_relevant_leg2_nodes",
    "dijkstra",
    "reconstruct_path",
    "reconstruct_route_path",
]
