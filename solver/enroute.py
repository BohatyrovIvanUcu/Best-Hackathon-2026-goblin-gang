from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .graph import PredecessorMatrix, reconstruct_path


@dataclass(frozen=True, slots=True)
class InsertCandidate:
    insert_after_index: int | None
    detour_km: float
    detour_ratio: float
    savings: float
    passes_through_existing_path: bool


def detour_km(
    source: str,
    candidate: str,
    target: str,
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> float:
    return (
        _distance_between(source, candidate, distances_by_source)
        + _distance_between(candidate, target, distances_by_source)
        - _distance_between(source, target, distances_by_source)
    )


def detour_ratio(detour_km_value: float, base_route_km: float) -> float:
    safe_base = max(base_route_km, 1e-9)
    return detour_km_value / safe_base


def savings(
    base_route_km: float,
    detour_km_value: float,
    separate_trip_km_value: float,
    cost_per_km: float,
) -> float:
    cost_with_stop = (base_route_km + detour_km_value) * cost_per_km
    cost_without_stop = base_route_km * cost_per_km + separate_trip_km_value * cost_per_km
    return cost_without_stop - cost_with_stop


def separate_trip_km(
    depot_node_id: str,
    candidate_node_id: str,
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> float:
    return _distance_between(depot_node_id, candidate_node_id, distances_by_source) + _distance_between(
        candidate_node_id,
        depot_node_id,
        distances_by_source,
    )


def segment_contains_node(
    source: str,
    target: str,
    candidate_node_id: str,
    predecessors_by_source: PredecessorMatrix | None,
) -> bool:
    if predecessors_by_source is None or source == target:
        return False

    predecessors = predecessors_by_source.get(source)
    if predecessors is None:
        return False

    try:
        return candidate_node_id in reconstruct_path(predecessors, source, target)[1:-1]
    except ValueError:
        return False


def try_insert_candidate(
    route_nodes: list[str],
    candidate_node_id: str,
    depot_node_id: str,
    base_route_km: float,
    cost_per_km: float,
    max_detour_ratio: float,
    distances_by_source: Mapping[str, Mapping[str, float]],
    predecessors_by_source: PredecessorMatrix | None = None,
) -> InsertCandidate | None:
    if candidate_node_id in route_nodes[1:-1]:
        separate_km = separate_trip_km(depot_node_id, candidate_node_id, distances_by_source)
        return InsertCandidate(
            insert_after_index=None,
            detour_km=0.0,
            detour_ratio=0.0,
            savings=round(separate_km * cost_per_km, 2),
            passes_through_existing_path=False,
        )

    for segment_index in range(len(route_nodes) - 1):
        source = route_nodes[segment_index]
        target = route_nodes[segment_index + 1]
        if not segment_contains_node(source, target, candidate_node_id, predecessors_by_source):
            continue

        separate_km = separate_trip_km(depot_node_id, candidate_node_id, distances_by_source)
        return InsertCandidate(
            insert_after_index=segment_index,
            detour_km=0.0,
            detour_ratio=0.0,
            savings=round(separate_km * cost_per_km, 2),
            passes_through_existing_path=True,
        )

    best_candidate: InsertCandidate | None = None
    for segment_index in range(len(route_nodes) - 1):
        source = route_nodes[segment_index]
        target = route_nodes[segment_index + 1]
        extra_km = detour_km(source, candidate_node_id, target, distances_by_source)
        extra_ratio = detour_ratio(extra_km, base_route_km)
        if extra_ratio > max_detour_ratio:
            continue

        separate_km = separate_trip_km(depot_node_id, candidate_node_id, distances_by_source)
        candidate = InsertCandidate(
            insert_after_index=segment_index,
            detour_km=round(extra_km, 2),
            detour_ratio=round(extra_ratio, 4),
            savings=round(savings(base_route_km, extra_km, separate_km, cost_per_km), 2),
            passes_through_existing_path=False,
        )
        if best_candidate is None or _candidate_rank(candidate) < _candidate_rank(best_candidate):
            best_candidate = candidate

    return best_candidate


def _candidate_rank(candidate: InsertCandidate) -> tuple[float, float, float, bool]:
    return (
        -candidate.savings,
        candidate.detour_ratio,
        candidate.detour_km,
        not candidate.passes_through_existing_path,
    )


def _distance_between(
    source: str,
    target: str,
    distances_by_source: Mapping[str, Mapping[str, float]],
) -> float:
    if source == target:
        return 0.0
    return distances_by_source.get(source, {}).get(target, math.inf)


__all__ = [
    "InsertCandidate",
    "detour_km",
    "detour_ratio",
    "savings",
    "segment_contains_node",
    "separate_trip_km",
    "try_insert_candidate",
]
