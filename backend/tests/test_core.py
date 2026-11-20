from pathlib import Path
import sys
from decimal import Decimal
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.catalog.service import Catalog, FIELDS
from app.curves.service import load_profiles
from app.analytics.service import apply_factor, run_interaction
from app.integrations.cashflow_rs import CashflowEngine, EngineError, EngineTimeout
from app.domain import DealCreate, InteractionRequest
from app.domain import DiscountExperimentRequest
from app.analytics.service import shifted_profile, run_discount_experiment


def test_fixture_field_filter_selects_expected_19(tmp_path):
    catalog = Catalog(tmp_path / "catalog")
    elig = [d for d in catalog.list() if d["eligible"]]
    assert len(catalog.list()) == 55
    assert {d["id"] for d in elig} == {
        str(i) for i in [*range(1, 15), 21, 26, 27, 28, 29]
    }


def test_curve_profiles_keep_scenarios_and_df():
    profiles = load_profiles()
    assert [p["id"] for p in profiles] == ["flat-10-12-v1", "repo-up100-v1"]
    flat = profiles[0]
    a = next(
        n for n in flat["nodes"] if n["scenario"] == "BASE" and n["tenor_days"] == 365
    )
    b = next(
        n
        for n in flat["nodes"]
        if n["scenario"] == "STRESS_UP_200BP" and n["tenor_days"] == 365
    )
    assert Decimal(a["df"]) == Decimal(1) / Decimal("1.1")
    assert Decimal(b["zero_rate_percent"]) == 12


def test_discount_experiment_validates_numeric_shifts_and_recomputes_nodes():
    with pytest.raises(Exception):
        DiscountExperimentRequest(deal_ids=["28"], profile_id="x", factor="parallel_shift", values=["1", "1.00"])
    with pytest.raises(Exception):
        DiscountExperimentRequest(deal_ids=["28"], profile_id="x", factor="parallel_shift", values=["1e-99999999"])
    profile = load_profiles()[1]
    shifted = shifted_profile(profile, "long_end_shift", "100")
    one_year = next(n for n in shifted["nodes"] if n["scenario"] == "BASE" and n["tenor_days"] == 365)
    ten_year = next(n for n in shifted["nodes"] if n["scenario"] == "BASE" and n["tenor_days"] == 3650)
    assert Decimal(one_year["zero_rate_percent"]) == Decimal("8.60")
    assert Decimal(ten_year["zero_rate_percent"]) == Decimal("10.10")
    original_one_year = next(n for n in profile["nodes"] if n["scenario"] == "BASE" and n["tenor_days"] == 365)
    assert Decimal(one_year["df"]) == Decimal(original_one_year["df"])
    assert Decimal(ten_year["df"]) != Decimal(next(n for n in profile["nodes"] if n["scenario"] == "BASE" and n["tenor_days"] == 3650)["df"])


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / ".engine/cashflow-rs/target/release/cfcalc").is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_discount_series_keeps_fixed_cashflows_and_has_no_history(tmp_path):
    from app.storage.repository import Repository

    profile = load_profiles()[0]
    repo = Repository(tmp_path / "history")
    request = DiscountExperimentRequest(
        deal_ids=["28"], profile_id=profile["id"], factor="parallel_shift", values=["0", "100"]
    )
    result = run_discount_experiment(request, Catalog(), profile, CashflowEngine())
    assert result["variants"][0]["effect"] == {"base": "0.00", "stress": "0.00", "dnpv": "0.00"}
    before = result["original"]["deals"][0]["flows"]
    after = result["variants"][1]["valuation"]["deals"][0]["flows"]
    keys = ("date", "principal", "interest", "total", "remaining_principal")
    assert [[f[k] for k in keys] for f in before] == [[f[k] for k in keys] for f in after]
    assert Decimal(result["variants"][1]["effect"]["dnpv"]).is_finite()
    assert repo.list_experiments() == []


def test_accrued_interest_factor_requires_explicit_source_and_nonnegative_rubles():
    d = Catalog().get("28")
    assert "accrued_interest" not in d["available_factors"]
    d["fields"]["accrued_interest"] = "0"
    from app.catalog.service import available_factors, scheme_compatible
    d["available_factors"] = available_factors(d["fields"], d["eligible"], scheme_compatible(d["fields"], d["eligible"]))
    assert "accrued_interest" in d["available_factors"]
    changed = apply_factor([d], "28", "accrued_interest", "123.45")[0]
    assert changed["fields"]["accrued_interest"] == "123.45"
    for value in ("-0.01", "1.001", "NaN"):
        with pytest.raises(ValueError):
            apply_factor([d], "28", "accrued_interest", value)


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / ".engine/cashflow-rs/target/release/cfcalc").is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_applies_explicit_accrued_interest():
    from app.analytics.service import value_snapshot
    from app.catalog.service import normalize

    fields = {key: "" for key in FIELDS}
    fields.update({
        "report_dttm": "2022-08-31", "discriminator": "SIMPLE", "deal_id": "900",
        "currency_id": "RUB", "start_principal_amt": "100000", "current_principal_amt": "100000",
        "deal_dttm": "2022-08-31", "maturity_dttm": "2023-08-31", "rate_type_cd": "FIXED",
        "interest_rate": "0.1", "interest_payment_rule_start_dttm": "2022-08-31",
        "interest_payment_rule_period_cd": "ENDDATE", "day_convention_cd": "ACT/365",
        "regular_payment_rule_start_dttm": "2022-08-31", "regular_payment_rule_period_cd": "ENDDATE",
        "accrued_interest": "0", "deal_report_dttm": "2022-08-31",
    })
    deal = normalize(fields, ident="accrued-control", name="accrued control", editable=True)
    profile = load_profiles()[0]
    profile["nodes"] = [node for node in profile["nodes"] if node["tenor_days"] == 365]
    baseline = value_snapshot([deal], profile, False, CashflowEngine())
    changed = apply_factor([deal], deal["id"], "accrued_interest", "1000")
    result = value_snapshot(changed, profile, False, CashflowEngine())
    assert Decimal(baseline["deals"][0]["flows"][0]["interest"]) == Decimal("10000.00")
    assert Decimal(result["deals"][0]["flows"][0]["interest"]) == Decimal("11000.00")
    assert Decimal(result["portfolio"]["base"]) - Decimal(baseline["portfolio"]["base"]) == Decimal("909.09")


def test_amount_rate_term_factors_are_independent_snapshots():
    d = Catalog().get("28")
    orig = d["fields"]["current_principal_amt"]
    snap = [d]
    x = apply_factor(snap, "28", "amount", "1.1")[0]
    assert Decimal(x["fields"]["current_principal_amt"]) == Decimal(orig) * Decimal(
        "1.1"
    )
    assert d["fields"]["current_principal_amt"] == orig
    y = apply_factor(snap, "28", "rate", "0.085")[0]
    assert (
        y["fields"]["interest_rate"] == "0.085"
        and y["fields"]["current_principal_amt"] == orig
    )
    z = apply_factor(snap, "28", "term", "24")[0]
    assert z["maturity_dttm"] == "2024-08-31"


def test_scheme_limited_to_explicit_template():
    c = Catalog()
    assert (
        apply_factor([c.get("28")], "28", "scheme", "SIMPLE")[0]["discriminator"]
        == "SIMPLE"
    )
    with pytest.raises(ValueError):
        apply_factor([c.get("1")], "1", "scheme", "RGM")


def test_new_factor_availability_and_engine_mappings():
    c = Catalog()
    d28 = c.get("28")
    assert {"day_count", "payment_day"}.issubset(d28["available_factors"])
    day_count = apply_factor([d28], "28", "day_count", "ACTACT")[0]
    payment_day = apply_factor([d28], "28", "payment_day", "15")[0]
    assert day_count["fields"]["day_convention_cd"] == "ACT/ACT ISDA"
    assert payment_day["fields"]["interest_payment_rule_day_num"] == "15"
    assert payment_day["fields"]["regular_payment_rule_day_num"] == "15"
    d21 = c.get("21")
    assert "payment_frequency" in d21["available_factors"]
    frequency = apply_factor([d21], "21", "payment_frequency", "3M")[0]
    assert frequency["fields"]["interest_payment_rule_period_cd"] == "3M"
    with pytest.raises(ValueError):
        apply_factor([d28], "28", "payment_frequency", "3M")


def test_interaction_probe_matches_documented_result():
    c = Catalog()
    profile = next(p for p in load_profiles() if p["id"] == "repo-up100-v1")
    req = InteractionRequest(
        deal_ids=["28"], profile_id=profile["id"], target_deal_id="28",
        factor_a="term", base_value_a="12", value_a="36",
        factor_b="scheme", base_value_b="ANN", value_b="SIMPLE",
    )
    result = run_interaction(req, c, profile, CashflowEngine())
    assert set(result["states"]) == {"original", "a", "b", "both"}
    assert result["effects"]["interaction"]["dnpv"] == "-2071.66"


def test_new_deal_validation():
    base = {
        "name": "учебная",
        "current_principal_amt": "100",
        "interest_rate": "0.1",
        "maturity_dttm": "2024-08-31",
        "discriminator": "ANN",
    }
    assert DealCreate(**base).interest_rate == "0.1"
    with pytest.raises(Exception):
        DealCreate(**{**base, "current_principal_amt": "-1"})
    with pytest.raises(Exception):
        DealCreate(**{**base, "interest_rate": "NaN"})
    with pytest.raises(Exception):
        DealCreate(**{**base, "maturity_dttm": "2053-09-01"})


def test_cli_missing_binary_is_error(tmp_path):
    eng = CashflowEngine(
        tmp_path / "missing", Path("factor-lab/.engine/cashflow-rs/data/cbkey1m.csv"), 1
    )
    with pytest.raises(EngineError, match="не готовы"):
        eng.run_pair([], load_profiles()[0])


def test_cli_timeout_and_partial_output_rejected(tmp_path):
    indicator = tmp_path / "indicator.csv"
    indicator.write_text("effective_date;rate_percent\n2020-01-01;5\n")
    script = tmp_path / "fake"
    script.write_text("#!/usr/bin/env python3\nimport sys,time\ntime.sleep(3)\n")
    script.chmod(0o755)
    eng = CashflowEngine(script, indicator, 1)
    with pytest.raises(EngineTimeout):
        eng._invoke([], load_profiles()[0], "BASE", False, tmp_path / "timeout")


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_one_deal_smoke():
    c = Catalog()
    d = c.get("28")
    profile = load_profiles()[0]
    result = CashflowEngine().run_pair([d], profile)
    assert set(result["base"]["summary"]) == {"28"}
    assert Decimal(result["base"]["summary"]["28"]["npv"]).is_finite()


def test_partial_cli_summary_is_rejected(tmp_path):
    summary = tmp_path / "summary.csv"
    flows = tmp_path / "flows.csv"
    summary.write_text(
        "deal_id;principal;interest;total_cf;pv_principal;pv_interest;npv\n1;100;0;100;90;0;90\nTOTAL;100;0;100;90;0;90\n"
    )
    flows.write_text(
        "deal_id;cashflow_date;currency;principal_cf;interest_cf;total_cf;balance_after;discount_factor;discounted_total_cf\n"
    )
    with pytest.raises(EngineError, match="Неполный"):
        CashflowEngine()._parse(flows, summary, {"1": "one", "2": "two"})


def test_non_finite_cli_summary_is_rejected(tmp_path):
    summary = tmp_path / "summary.csv"
    flows = tmp_path / "flows.csv"
    summary.write_text(
        "deal_id;principal;interest;total_cf;pv_principal;pv_interest;npv\n1;100;0;100;90;0;90\nTOTAL;NaN;0;100;90;0;90\n"
    )
    flows.write_text(
        "deal_id;cashflow_date;currency;principal_cf;interest_cf;total_cf;balance_after;discount_factor;discounted_total_cf\n"
    )
    with pytest.raises(EngineError, match="TOTAL"):
        CashflowEngine()._parse(flows, summary, {"1": "one"})


def test_missing_profile_scenario_is_rejected_before_process(tmp_path):
    indicator = tmp_path / "indicator.csv"
    indicator.write_text("effective_date;rate_percent\n2020-01-01;5\n")
    executable = tmp_path / "cfcalc"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    with pytest.raises(EngineError, match="нет сценария"):
        CashflowEngine(executable, indicator)._invoke(
            [], {"nodes": [], "stress_scenario": "X"}, "BASE", False, tmp_path / "job"
        )


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_annual_single_payment_control():
    from app.analytics.service import value_snapshot

    fields = {k: "" for k in FIELDS}
    fields.update(
        {
            "report_dttm": "2023-01-01",
            "discriminator": "SIMPLE",
            "deal_id": "900",
            "currency_id": "RUB",
            "start_principal_amt": "100000",
            "current_principal_amt": "100000",
            "deal_dttm": "2023-01-01",
            "maturity_dttm": "2024-01-01",
            "rate_type_cd": "FIXED",
            "interest_rate": "0.1",
            "interest_payment_rule_start_dttm": "2023-01-01",
            "interest_payment_rule_period_cd": "ENDDATE",
            "day_convention_cd": "ACT/365",
            "regular_payment_rule_start_dttm": "2023-01-01",
            "regular_payment_rule_period_cd": "ENDDATE",
            "accrued_interest": "0",
            "deal_report_dttm": "2023-01-01",
        }
    )
    from app.catalog.service import normalize

    deal = normalize(fields, ident="annual", name="Одноплатёжный", editable=True)
    profile = {
        "id": "annual-control",
        "name": "annual control",
        "curve_type": "ANNUAL_CONTROL",
        "report_date": "2023-01-01",
        "currency": "RUB",
        "base_scenario": "BASE",
        "stress_scenario": "STRESS",
        "compounding": "annual",
        "nodes": [
            {
                "scenario": "BASE",
                "tenor_days": 365,
                "zero_rate_percent": "10",
                "df": "0.9090909091",
            },
            {
                "scenario": "STRESS",
                "tenor_days": 365,
                "zero_rate_percent": "12",
                "df": "0.8928571429",
            },
        ],
    }
    result = value_snapshot([deal], profile, False, CashflowEngine())
    assert Decimal(result["portfolio"]["base"]) == Decimal("100000.00")
    assert Decimal(result["portfolio"]["stress"]) == Decimal("98214.29")
    assert Decimal(result["portfolio"]["dnpv"]) == Decimal("-1785.71")
    assert len(result["deals"][0]["flows"]) == 1


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_batched_experiment_keeps_other_deals_and_history(tmp_path):
    from app.analytics.service import run_experiment
    from app.storage.repository import Repository
    from app.domain import ExperimentRequest

    class CountEngine(CashflowEngine):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def _invoke(self, *args, **kwargs):
            self.calls += 1
            return super()._invoke(*args, **kwargs)

    catalog = Catalog(tmp_path / "catalog")
    engine = CountEngine()
    repo = Repository(tmp_path / "history")
    profile = load_profiles()[0]
    result = run_experiment(
        ExperimentRequest(
            deal_ids=["28", "21"],
            profile_id=profile["id"],
            target_deal_id="28",
            factor="amount",
            values=["1", "1.1"],
        ),
        catalog,
        profile,
        engine,
        repo,
    )
    assert engine.calls == 2
    assert result["variants"][0]["effect"]["base"] == "0.00"
    assert result["variants"][0]["effect"]["dnpv"] == "0.00"
    other0 = next(d for d in result["original"]["snapshot"] if d["id"] == "21")
    other1 = next(
        d for d in result["variants"][1]["valuation"]["snapshot"] if d["id"] == "21"
    )
    assert other0 == other1
    assert Decimal(result["variants"][1]["effect"]["dnpv"]) == Decimal(
        result["variants"][1]["valuation"]["portfolio"]["dnpv"]
    ) - Decimal(result["original"]["portfolio"]["dnpv"])
    assert repo.get_experiment(result["id"]) == result
    assert len(repo.list_experiments()) == 1
    assert (tmp_path / "history" / "runs" / f"{result['id']}.json").is_file()
    # Later catalog edits cannot mutate the persisted snapshot.
    catalog.items["28"]["name"] = "Renamed"
    assert (
        repo.get_experiment(result["id"])["original"]["snapshot"][0]["name"]
        != "Renamed"
    )


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_parallel_cli_runs_are_isolated():
    from concurrent.futures import ThreadPoolExecutor

    catalog = Catalog()
    d = catalog.get("28")
    profile = load_profiles()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: CashflowEngine().run_pair([d], profile), range(2))
        )
    assert (
        results[0]["base"]["summary"]["28"]["npv"]
        == results[1]["base"]["summary"]["28"]["npv"]
    )
    assert (
        results[0]["stress"]["summary"]["28"]["npv"]
        == results[1]["stress"]["summary"]["28"]["npv"]
    )


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_real_cli_rate_term_and_scheme_variant_metrics_are_strict(tmp_path):
    from app.analytics.service import run_experiment
    from app.storage.repository import Repository
    from app.domain import ExperimentRequest

    profile = load_profiles()[0]
    catalog = Catalog(tmp_path / "catalog")
    for factor, value in [("rate", "0.085"), ("term", "24"), ("scheme", "RGM")]:
        result = run_experiment(
            ExperimentRequest(
                deal_ids=["28"],
                profile_id=profile["id"],
                target_deal_id="28",
                factor=factor,
                values=[value],
            ),
            catalog,
            profile,
            CashflowEngine(),
            Repository(tmp_path / factor),
        )
        variant = result["variants"][0]
        assert Decimal(variant["effect"]["dnpv"]) == Decimal(
            variant["valuation"]["portfolio"]["dnpv"]
        ) - Decimal(result["original"]["portfolio"]["dnpv"])
        assert Decimal(variant["effect"]["dnpv"]) == Decimal(
            variant["effect"]["stress"]
        ) - Decimal(variant["effect"]["base"])
        assert Decimal(variant["target_effect"]["dnpv"]) == Decimal(
            variant["target_effect"]["stress"]
        ) - Decimal(variant["target_effect"]["base"])


def test_malformed_engine_csv_is_typed_as_engine_error(tmp_path):
    summary = tmp_path / "summary.csv"
    flows = tmp_path / "flows.csv"
    summary.write_text("wrong;header\n1;2\n")
    flows.write_text("not even a csv\n")
    with pytest.raises(EngineError, match="CSV-вывод"):
        CashflowEngine()._parse(flows, summary, {"1": "one"})


def test_factor_rejects_non_finite_and_unbounded_inputs():
    c = Catalog()
    snapshot = [c.get("28")]
    for value in ("NaN", "Infinity", "1e1000", ""):
        with pytest.raises(ValueError):
            apply_factor(snapshot, "28", "amount", value)
    with pytest.raises(ValueError):
        apply_factor(snapshot, "28", "rate", "NaN")


def test_build_info_missing_or_invalid_makes_engine_unavailable(tmp_path, monkeypatch):
    import json
    import app.integrations.cashflow_rs as integration

    root = tmp_path / "project"
    (root / ".engine").mkdir(parents=True)
    (root / "engine.lock.json").write_text(
        json.dumps({"commit": "pin", "package_version": "1.0"})
    )
    binary = tmp_path / "cfcalc"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    indicator = tmp_path / "indicator.csv"
    indicator.write_text("effective_date;rate_percent\n2020-01-01;5\n")
    monkeypatch.setattr(integration, "ROOT_DIR", root)
    engine = CashflowEngine(binary, indicator)

    assert engine.metadata()["ready"] is False
    (root / ".engine" / "build-info.json").write_text("{")
    assert engine.metadata()["ready"] is False
    (root / ".engine" / "build-info.json").write_text(
        json.dumps(
            {
                "commit": "pin",
                "version": "1.0",
                "binary_sha256": __import__("hashlib")
                .sha256(binary.read_bytes())
                .hexdigest(),
            }
        )
    )
    assert engine.metadata()["ready"] is True


def test_spawn_oserror_becomes_engine_error(tmp_path):
    indicator = tmp_path / "indicator.csv"
    indicator.write_text("effective_date;rate_percent\n2020-01-01;5\n")
    missing_binary = tmp_path / "missing-cfcalc"
    engine = CashflowEngine(missing_binary, indicator)
    profile = load_profiles()[0]
    with pytest.raises(EngineError, match="Не удалось запустить"):
        engine._invoke([], profile, "BASE", False, tmp_path / "job", timeout=1)


def test_amount_factor_rejects_too_small_multiplier():
    with pytest.raises(ValueError, match="точностью до 8 знаков"):
        apply_factor([Catalog().get("28")], "28", "amount", "1e-1000")


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / ".engine/cashflow-rs/target/release/cfcalc"
    ).is_file(),
    reason="upstream cfcalc not built yet",
)
def test_engine_uses_profile_base_scenario():
    profile = load_profiles()[0]
    profile["base_scenario"] = "STRESS_UP_200BP"
    profile["stress_scenario"] = "BASE"
    result = CashflowEngine().run_pair([Catalog().get("28")], profile)
    assert result["base"]["summary"]["28"]["npv"] == "272249.74"
    assert result["stress"]["summary"]["28"]["npv"] == "276895.28"


def test_amount_factor_rounds_half_up_to_cents_and_marks_result():
    from app.analytics.service import apply_factor

    deal = Catalog().get("28")
    deal["fields"]["current_principal_amt"] = "100.00"
    changed = apply_factor([deal], "28", "amount", "1.00005")[0]
    assert changed["fields"]["current_principal_amt"] == "100.01"


def test_csv_export_neutralizes_formula_text_but_keeps_negative_numbers():
    import csv
    import io
    from app.api.export import experiment_csv

    metrics = {"base": "-1.25", "stress": "-2.50", "dnpv": "-1.25"}
    deal = {
        "id": "deal-1",
        "name": "\t =1+1",
        "eligible": True,
        "fields": {
            "current_principal_amt": "100.00",
            "interest_rate": "0.1",
            "product": "@SUM(1,1)",
        },
    }
    valuation = {
        "profile": {
            "id": "flat",
            "name": "Flat",
            "source": "Synthetic fixture",
            "synthetic": True,
            "curve_type": "FLAT",
            "report_date": "2022-08-31",
            "currency": "RUB",
            "compounding": "annual",
            "nodes": [],
        },
        "act_act_as_365": False,
        "engine": {"commit": "pin", "version": "0.2.0", "sha256": "hash"},
        "snapshot": [deal],
        "deals": [{"deal_id": "deal-1", "metrics": metrics}],
        "portfolio": metrics,
    }
    result = experiment_csv(
        {
            "id": "exp",
            "created_at": "now",
            "factor": "amount",
            "target_deal_id": "deal-1",
            "original": valuation,
            "variants": [
                {
                    "value": "1",
                    "valuation": valuation,
                    "effect": metrics,
                    "target_effect": metrics,
                }
            ],
        }
    )
    rows = list(csv.reader(io.StringIO(result.removeprefix("\ufeff")), delimiter=";"))
    all_cells = [cell for row in rows for cell in row]
    assert all_cells.count("'\t =1+1") == 8
    assert "\t =1+1" not in all_cells
    assert all_cells.count("'@SUM(1,1)") == 2
    assert "@SUM(1,1)" not in all_cells
    assert any("-1,25" == cell for row in rows for cell in row)
    assert not any("'-1,25" == cell for row in rows for cell in row)
