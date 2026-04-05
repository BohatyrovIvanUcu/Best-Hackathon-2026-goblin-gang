from __future__ import annotations

PRIORITY_WEIGHTS: dict[str, float] = {
    "NORMAL": 1.0,
    "ELEVATED": 2.0,
    "CRITICAL": 4.0,
}


def compute_priority(current_stock: float, min_stock: float, is_urgent: bool | int) -> str:
    """Recompute demand priority from stock levels instead of trusting CSV labels."""
    if bool(is_urgent):
        return "CRITICAL"

    if min_stock <= 0:
        return "NORMAL"

    ratio = current_stock / min_stock
    if ratio < 0.2:
        return "CRITICAL"
    if ratio < 0.5:
        return "ELEVATED"
    return "NORMAL"


def priority_weight(priority: str) -> float:
    normalized_priority = priority.upper()
    try:
        return PRIORITY_WEIGHTS[normalized_priority]
    except KeyError as error:
        raise ValueError(f"Unsupported priority '{priority}'") from error


__all__ = [
    "PRIORITY_WEIGHTS",
    "compute_priority",
    "priority_weight",
]
