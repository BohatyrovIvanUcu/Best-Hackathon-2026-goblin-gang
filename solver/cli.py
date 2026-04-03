from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from typing import Sequence

from .io import load_solver_inputs, save_solver_output_csvs
from .routing import SolveResult, solve_network


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m solver",
        description="Run the delivery solver on a CSV dataset directory.",
    )
    parser.add_argument(
        "--data",
        "-d",
        default="demo_data",
        help="Input directory with nodes.csv, edges.csv, trucks.csv, demand.csv, warehouse_stock.csv, products.csv, settings.csv",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="solver_output",
        help="Output directory for routes.csv, route_cargo.csv and warehouse_stock.csv",
    )
    parser.add_argument(
        "--departure-time",
        help="Optional departure time override in HH:MM format",
    )
    parser.add_argument(
        "--created-at",
        help="Optional created_at timestamp override in ISO format, for example 2026-04-03 10:00:00",
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help="Print execution summary as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    data_dir = Path(args.data)
    output_dir = Path(args.output)
    departure_time = _parse_departure_time(args.departure_time)
    created_at = _parse_created_at(args.created_at)

    solver_inputs = load_solver_inputs(data_dir)
    result = solve_network(
        solver_inputs,
        departure_time_override=departure_time,
        created_at=created_at,
    )
    output_paths = save_solver_output_csvs(
        output_dir,
        result.routes_table,
        result.route_cargo_table,
        result.warehouse_stock_table,
    )

    summary = build_summary(result, output_paths)
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(format_summary(summary))

    return 0


def build_summary(result: SolveResult, output_paths: dict[str, Path]) -> dict[str, object]:
    unassigned_reason_counts = Counter(item.reason for item in result.assignment.unassigned_orders)
    leg_counts = Counter(route["leg"] for route in result.routes_table)
    time_warning_count = sum(1 for route in result.routes_table if route.get("time_warning"))

    return {
        "routes_count": len(result.routes_table),
        "route_cargo_count": len(result.route_cargo_table),
        "warehouse_stock_count": len(result.warehouse_stock_table),
        "leg1_routes_count": int(leg_counts.get(1, 0)),
        "leg2_routes_count": int(leg_counts.get(2, 0)),
        "leg1_needs_count": len(result.leg1_plan.needs),
        "leg1_unassigned_needs_count": len(result.leg1_plan.unassigned_needs),
        "unassigned_orders_count": len(result.assignment.unassigned_orders),
        "unassigned_reason_counts": dict(sorted(unassigned_reason_counts.items())),
        "time_warning_routes_count": time_warning_count,
        "output_paths": {key: str(path) for key, path in output_paths.items()},
    }


def format_summary(summary: dict[str, object]) -> str:
    lines = [
        "Solver run completed.",
        f"Routes: {summary['routes_count']} (Leg 1: {summary['leg1_routes_count']}, Leg 2: {summary['leg2_routes_count']})",
        f"Route cargo rows: {summary['route_cargo_count']}",
        f"Warehouse stock rows: {summary['warehouse_stock_count']}",
        f"Leg 1 needs: {summary['leg1_needs_count']} (unassigned: {summary['leg1_unassigned_needs_count']})",
        f"Unassigned orders: {summary['unassigned_orders_count']}",
        f"Time warnings: {summary['time_warning_routes_count']}",
        "Output files:",
    ]

    output_paths = summary["output_paths"]
    if isinstance(output_paths, dict):
        for key in sorted(output_paths):
            lines.append(f"  {key}: {output_paths[key]}")

    unassigned_reason_counts = summary["unassigned_reason_counts"]
    if isinstance(unassigned_reason_counts, dict) and unassigned_reason_counts:
        lines.append("Unassigned reasons:")
        for reason, count in unassigned_reason_counts.items():
            lines.append(f"  {reason}: {count}")

    return "\n".join(lines)


def _parse_departure_time(raw_value: str | None) -> time | None:
    if raw_value is None:
        return None
    return time.fromisoformat(raw_value)


def _parse_created_at(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    return datetime.fromisoformat(raw_value)


__all__ = [
    "build_parser",
    "build_summary",
    "format_summary",
    "main",
]
