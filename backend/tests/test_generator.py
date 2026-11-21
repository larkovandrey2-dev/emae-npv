"""The parameterised generator: valid rows, requested distributions, reproducible files."""

import csv
from collections import Counter
from datetime import date

import pytest

from factor_lab.application.generator import COLUMNS, GeneratorParams, generate


def read(path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_rows_follow_the_requested_distributions(tmp_path):
    p = GeneratorParams.from_dict({
        "rows": 6000, "seed": 3, "currency": {"RUB": 1},
        "maturity": [{"min_years": 0.5, "max_years": 1, "share": 1}, {"min_years": 10, "max_years": 20, "share": 1}],
        "payment_types": {"ANN": 1, "SIMPLE": 1}, "rate_types": {"FLOAT": 1}, "payment_frequency": {"3M": 1},
        "subsidy_share": 0.5, "prepayment": {"PREPAYMENT_MORTGAGE": 1, "": 1},
    })
    out = tmp_path / "deals.csv"
    generate(p, out, workers=2)
    rows = read(out)
    assert len(rows) == 6000 and list(rows[0]) == COLUMNS
    report = date(2022, 8, 31)
    kinds = Counter(r["discriminator"] for r in rows)
    assert set(kinds) == {"ANN", "SIMPLE"} and abs(kinds["ANN"] / 6000 - 0.5) < 0.05
    assert {r["rate_type_cd"] for r in rows} == {"FLOAT"}
    assert {r["interest_payment_rule_period_cd"] for r in rows} == {"3M"}
    remaining = [(date.fromisoformat(r["maturity_dttm"]) - report).days / 365 for r in rows]
    assert all(0.4 < y < 1.01 or 9.9 < y < 20.01 for y in remaining)
    for r in rows:
        assert date.fromisoformat(r["deal_dttm"]) < report < date.fromisoformat(r["maturity_dttm"])
        assert date.fromisoformat(r["deal_dttm"]) >= date(2014, 1, 1), "floating deals need CBKEY1M history"
        assert r["base_indicator"] == "CBKEY1M"
    subsidised = [r for r in rows if r["sub_rate_type_cd"]]
    assert all(r["discriminator"] == "ANN" for r in subsidised), "bullets carry no subsidy"
    assert abs(len(subsidised) / kinds["ANN"] - 0.5) < 0.06
    models = Counter(r["behavior_model_cd"] for r in rows)
    assert abs(models["PREPAYMENT_MORTGAGE"] / 6000 - 0.5) < 0.05


def test_same_seed_same_file_whatever_the_workers(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    generate(GeneratorParams(rows=45_000, seed=9), a, workers=1)
    generate(GeneratorParams(rows=45_000, seed=9), b, workers=3)
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("patch, message", [
    ({"rows": 0}, "rows"),
    ({"payment_types": {"BULLET": 1}}, "payment_types"),
    ({"rate_types": {"FIXED": 0, "FLOAT": 0}}, "rate_types"),
    ({"maturity": [{"min_years": 3, "max_years": 1, "share": 1}]}, "maturity"),
    ({"amount": {"min": 10, "max": 1}}, "amount"),
    ({"subsidy_share": 2}, "subsidy_share"),
])
def test_invalid_parameters_are_explained(patch, message):
    with pytest.raises(ValueError, match=message):
        GeneratorParams.from_dict(patch)
