from __future__ import annotations

import pytest

from osemosys_vs_pypsa.build_years import build_years_for, restrict_build_years

YEARS = list(range(2023, 2051))
REGIONS = ["R1", "R2"]


def make_model() -> dict:
    return {
        "time_definition": {"years": YEARS},
        "regions": [{"id": r} for r in REGIONS],
        "technologies": [{"id": "coal"}, {"id": "wind"}],
        "trade": [
            {
                "commodity": "electricity",
                "trade_routes": {"is_composed": True, "data": {"R1": {"R2": {"2023": True}}}},
            }
        ],
    }


def test_build_years_matches_converter_slicing():
    assert build_years_for(YEARS, 1) == YEARS
    assert build_years_for(YEARS, 5) == [2023, 2028, 2033, 2038, 2043, 2048]


def test_step_one_is_a_noop():
    model = make_model()
    assert restrict_build_years(model, 1) is model
    assert model["technologies"][0].get("capacity_additional_max") is None


def test_rejects_invalid_step():
    with pytest.raises(ValueError, match="step must be >= 1"):
        restrict_build_years(make_model(), 0)


def test_forbids_only_off_step_years():
    model = restrict_build_years(make_model(), 5)
    allowed = set(build_years_for(YEARS, 5))

    for technology in model["technologies"]:
        data = technology["capacity_additional_max"]["data"]
        assert set(data) == set(REGIONS)
        for per_year in data.values():
            forbidden = {int(y) for y in per_year}
            assert forbidden == set(YEARS) - allowed
            assert set(per_year.values()) == {0.0}
            # Build years must be ABSENT, not zero: tz-osemosys masks the
            # constraint on `>= 0`, so a present-but-large value would add a row
            # and NaN leaves the year genuinely unconstrained.
            assert not (allowed & forbidden)


def test_trade_mirrors_its_own_region_pairs():
    model = restrict_build_years(make_model(), 5)
    data = model["trade"][0]["capacity_additional_max"]["data"]
    assert set(data) == {"R1"}
    assert set(data["R1"]) == {"R2"}
    assert set(data["R1"]["R2"].values()) == {0.0}


def test_refuses_to_overwrite_an_existing_bound():
    model = make_model()
    model["technologies"][0]["capacity_additional_max"] = {"is_composed": True, "data": {}}
    with pytest.raises(ValueError, match="already sets capacity_additional_max"):
        restrict_build_years(model, 5)
