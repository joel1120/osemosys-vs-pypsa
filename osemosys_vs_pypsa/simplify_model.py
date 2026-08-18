# ruff: noqa: ANN401, T201, S101  # CLI script: untyped JSON, prints, assert-based verify

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TECHNOLOGY_FIELDS_REMOVED = (
    "include_in_joint_reserve_margin",
    "capacity_additional_max_growth_rate",
    "capacity_additional_max_floor",
    "capacity_factor_annual_min",
)

# Not Optional on tz-osemosys's Technology schema (``OSeMOSYSData.RY``, default
# 1.0), so it cannot be dropped -- it is neutralised to 1.0 instead. That is
# genuinely non-binding: CapacityAdequacyB caps annual activity at
# ``AvailabilityFactor x capacity x CapacityToActivityUnit``, while
# CapacityAdequacyA already caps each timeslice at ``CapacityFactor[l] <= 1``,
# whose year-split-weighted sum cannot exceed the same bound.
TECHNOLOGY_FIELDS_NEUTRALISED = ("availability_factor",)

TRADE_FIELDS_REMOVED = (
    "availability_factor",
    "capacity_factor_annual_min",
)

MODE_FIELDS_CLEARED = ("emission_activity_ratio",)


def unwrap(value: Any) -> Any:
    """Unwrap an OSeMOSYSData ``{"is_composed": ..., "data": ...}`` envelope."""
    if isinstance(value, dict) and set(value) == {"is_composed", "data"}:
        return value["data"]
    return value


def _rewrap(original: Any, data: Any) -> Any:
    if isinstance(original, dict) and set(original) == {"is_composed", "data"}:
        return {"is_composed": original["is_composed"], "data": data}
    return data


def _deepcopy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _constant_ry(model: dict[str, Any], *, value: float | bool) -> dict[str, Any]:
    """Build a composed ``{region: {year: value}}`` OSeMOSYSData.RY envelope."""
    years = [str(y) for y in model["time_definition"]["years"]]
    return {
        "is_composed": True,
        "data": {region["id"]: dict.fromkeys(years, value) for region in model["regions"]},
    }


def technology_touches_storage(technology: dict[str, Any]) -> bool:
    for mode in technology.get("operating_modes") or []:
        if unwrap(mode.get("to_storage")) or unwrap(mode.get("from_storage")):
            return True
    return False


def simplify(model: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (simplified_model, report)."""
    report: dict[str, Any] = {}

    report["dropped_storage"] = [s["id"] for s in model.get("storage") or []]

    dropped_technologies: list[str] = []
    kept_technologies: list[dict[str, Any]] = []
    for technology in model["technologies"]:
        if technology_touches_storage(technology):
            dropped_technologies.append(technology["id"])
        else:
            kept_technologies.append(_deepcopy(technology))
    report["dropped_technologies"] = dropped_technologies

    simplified = dict(model)
    simplified["storage"] = None
    simplified["cost_of_capital_storage"] = None
    simplified["technologies"] = kept_technologies

    # Also not Optional on the Model schema, so it is neutralised to 1.0 rather
    # than dropped -- reserve_margin_fully_defined() only objects to values != 1.
    #
    # 1.0 alone is NOT neutral. RM3 is
    #     ReserveMargin x DemandNeedingReserveMargin <= TotalCapacityInReserveMargin
    # where the capacity side is masked by ReserveMarginTagTechnology (all NaN
    # once include_in_joint_reserve_margin is dropped) and the demand side by
    # ReserveMarginTagFuel (1 by default, and set true on electricity here).
    # That leaves "sum of production <= 0", which is infeasible. Clearing the
    # commodity tag empties the demand side too, so RM3 becomes 0 <= 0.
    report["neutralised_reserve_margin"] = model.get("reserve_margin") is not None
    simplified["reserve_margin"] = _constant_ry(model, value=1.0)

    simplified["commodities"] = _deepcopy(model["commodities"])
    untagged_commodities = 0
    for commodity in simplified["commodities"]:
        if commodity.get("include_in_joint_reserve_margin") is not None:
            untagged_commodities += 1
        commodity["include_in_joint_reserve_margin"] = _constant_ry(model, value=False)
    report["untagged_reserve_margin_commodities"] = untagged_commodities

    report["dropped_impacts"] = [i["id"] for i in model.get("impacts") or []]
    simplified["impacts"] = []

    removed_fields: dict[str, int] = dict.fromkeys(TECHNOLOGY_FIELDS_REMOVED, 0)
    neutralised_fields: dict[str, int] = dict.fromkeys(TECHNOLOGY_FIELDS_NEUTRALISED, 0)
    cleared_mode_fields: dict[str, int] = dict.fromkeys(MODE_FIELDS_CLEARED, 0)
    for technology in kept_technologies:
        for field_name in TECHNOLOGY_FIELDS_REMOVED:
            if technology.get(field_name) is not None:
                removed_fields[field_name] += 1
            technology[field_name] = None

        for field_name in TECHNOLOGY_FIELDS_NEUTRALISED:
            if technology.get(field_name) is not None:
                neutralised_fields[field_name] += 1
            technology[field_name] = _constant_ry(model, value=1.0)

        for mode in technology.get("operating_modes") or []:
            for field_name in MODE_FIELDS_CLEARED:
                if mode.get(field_name) is not None:
                    cleared_mode_fields[field_name] += 1
                mode[field_name] = None

    report["removed_technology_fields"] = removed_fields
    report["neutralised_technology_fields"] = neutralised_fields
    report["cleared_mode_fields"] = cleared_mode_fields

    simplified["trade"] = _deepcopy(model.get("trade") or [])
    removed_trade_fields: dict[str, int] = dict.fromkeys(TRADE_FIELDS_REMOVED, 0)
    for trade in simplified["trade"]:
        for field_name in TRADE_FIELDS_REMOVED:
            if trade.get(field_name) is not None:
                removed_trade_fields[field_name] += 1
            trade[field_name] = None
    report["removed_trade_fields"] = removed_trade_fields

    cost_of_capital = model.get("cost_of_capital")
    if cost_of_capital is not None:
        simplified["cost_of_capital"] = _rewrap(
            cost_of_capital,
            {
                region: {t: v for t, v in by_tech.items() if t not in dropped_technologies}
                for region, by_tech in unwrap(cost_of_capital).items()
            },
        )

    return simplified, report


def _all_equal(value: Any, *, expected: float | bool) -> bool:
    return bool(value) and all(
        v == expected for by_year in unwrap(value).values() for v in by_year.values()
    )


def verify(simplified: dict[str, Any]) -> None:
    """Fail loudly if anything that should have been removed or neutralised survived."""
    assert not simplified.get("storage"), "storage survived"
    assert _all_equal(simplified.get("reserve_margin"), expected=1.0), (
        "reserve_margin is not all 1.0"
    )
    assert not simplified.get("impacts"), "impacts survived"

    for commodity in simplified["commodities"]:
        tag = commodity.get("include_in_joint_reserve_margin")
        assert _all_equal(tag, expected=False), (
            f"{commodity['id']}.include_in_joint_reserve_margin is not all false -- "
            "RM3 would force production to zero"
        )

    for trade in simplified.get("trade") or []:
        for field_name in TRADE_FIELDS_REMOVED:
            assert trade.get(field_name) is None, f"trade.{field_name} survived"

    for technology in simplified["technologies"]:
        tech_id = technology["id"]
        for field_name in TECHNOLOGY_FIELDS_REMOVED:
            assert technology.get(field_name) is None, f"{tech_id}.{field_name} survived"
        for field_name in TECHNOLOGY_FIELDS_NEUTRALISED:
            assert _all_equal(technology.get(field_name), expected=1.0), (
                f"{tech_id}.{field_name} is not all 1.0"
            )

        for mode in technology.get("operating_modes") or []:
            assert not unwrap(mode.get("to_storage")), f"{tech_id}/{mode['id']} to_storage survived"
            assert not unwrap(mode.get("from_storage")), f"{tech_id}/{mode['id']} from_storage"
            for field_name in MODE_FIELDS_CLEARED:
                assert mode.get(field_name) is None, f"{tech_id}/{mode['id']}.{field_name} survived"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m osemosys_vs_pypsa.simplify_model IN.json OUT.json")
        return 2
    src, dst = Path(argv[1]), Path(argv[2])

    model = json.loads(src.read_text())
    simplified, report = simplify(model)
    verify(simplified)
    dst.write_text(json.dumps(simplified))

    print(f"read  {src}  ({src.stat().st_size / 1e6:.1f} MB)")
    for key, value in report.items():
        print(f"  {key}: {value}")
    print(f"  technologies remaining: {len(simplified['technologies'])}")
    print(f"  commodities remaining:  {[c['id'] for c in simplified['commodities']]}")
    print(f"wrote {dst}  ({dst.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
