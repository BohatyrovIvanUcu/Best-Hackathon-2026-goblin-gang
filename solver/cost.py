from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .io import SettingsValue

_NULL_VALUES = {"", "null", "none", "nan", "n/a"}
_TRUCK_DEFAULTS = {
    "driver_hourly": "driver_hourly_default",
    "avg_speed_kmh": "avg_speed_default",
    "amortization_per_km": "amortization_default",
    "maintenance_per_km": "maintenance_default",
}


@dataclass(frozen=True, slots=True)
class TruckCostParams:
    fuel_per_100km: float
    fuel_price: float
    driver_hourly: float
    avg_speed_kmh: float
    amortization_per_km: float
    maintenance_per_km: float


def resolve_truck_cost_params(
    truck: Any,
    settings: Mapping[str, SettingsValue],
) -> TruckCostParams:
    """Resolve all inputs needed for cost_per_km, with settings fallbacks for NULLs."""
    return TruckCostParams(
        fuel_per_100km=_require_numeric_field(truck, "fuel_per_100km"),
        fuel_price=_require_numeric_setting(settings, "fuel_price"),
        driver_hourly=_resolve_truck_field(truck, "driver_hourly", settings),
        avg_speed_kmh=_resolve_truck_field(truck, "avg_speed_kmh", settings),
        amortization_per_km=_resolve_truck_field(truck, "amortization_per_km", settings),
        maintenance_per_km=_resolve_truck_field(truck, "maintenance_per_km", settings),
    )


def compute_cost_per_km(
    truck: Any,
    settings: Mapping[str, SettingsValue],
) -> float:
    params = resolve_truck_cost_params(truck, settings)
    if params.avg_speed_kmh <= 0:
        raise ValueError("avg_speed_kmh must be positive to compute cost_per_km")

    fuel_cost_per_km = params.fuel_per_100km * params.fuel_price / 100.0
    driver_cost_per_km = params.driver_hourly / params.avg_speed_kmh

    return (
        fuel_cost_per_km
        + driver_cost_per_km
        + params.amortization_per_km
        + params.maintenance_per_km
    )


def _resolve_truck_field(
    truck: Any,
    field_name: str,
    settings: Mapping[str, SettingsValue],
) -> float:
    field_value = _coerce_optional_float(_get_value(truck, field_name))
    if field_value is not None:
        return field_value

    default_setting = _TRUCK_DEFAULTS[field_name]
    return _require_numeric_setting(settings, default_setting)


def _require_numeric_field(record: Any, field_name: str) -> float:
    field_value = _coerce_optional_float(_get_value(record, field_name))
    if field_value is None:
        raise ValueError(f"Missing numeric field '{field_name}'")
    return field_value


def _require_numeric_setting(
    settings: Mapping[str, SettingsValue],
    key: str,
) -> float:
    raw_value = settings.get(key)
    if isinstance(raw_value, bool):
        raise ValueError(f"Setting '{key}' must be numeric, got boolean")
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    raise ValueError(f"Setting '{key}' must be numeric, got {raw_value!r}")


def _get_value(record: Any, field_name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field_name)
    return getattr(record, field_name, None)


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean value cannot be used as numeric truck parameter")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() in _NULL_VALUES:
            return None
        return float(normalized)
    raise ValueError(f"Unsupported numeric value: {value!r}")


__all__ = [
    "TruckCostParams",
    "compute_cost_per_km",
    "resolve_truck_cost_params",
]
