import math

import pytest

from factor_lab.domain.curves import Curve, CurvePoint, blend
from factor_lab.domain.metrics import Valuation
from factor_lab.domain.portfolio import ScheduledFlow, roll_forward
from factor_lab.domain.shapley import exact_shapley


def curve(date, rates):
    return Curve(date, tuple(CurvePoint(t, r) for t, r in zip([0.5, 1, 3, 10], rates)))


def test_blend_moves_only_selected_buckets():
    a, b = curve("a", [0.1] * 4), curve("b", [0.2] * 4)
    mixed = blend(a, b, {"curve_medium"})
    assert [p.rate for p in mixed.points] == [0.1, 0.1, 0.2, 0.1]
    assert blend(a, b, set()) == a


def test_ec_is_minimum_dnpv():
    v = Valuation("2024-01-01", "2024-01-01", {"BASE": 100, "UP": 90, "DOWN": 103, "STEEP": 95})
    assert v.ec == -10 and v.worst_scenario == "UP"
    assert v.metric("dnpv_steep") == -5


def test_shapley_is_efficient_and_finds_interaction():
    # v = 3a + 2b + 5ab  (pure interaction term 5 split equally)
    def v(s):
        a, b = "a" in s, "b" in s
        return 3 * a + 2 * b + 5 * (a and b)

    phi, inter, _ = exact_shapley(["a", "b", "c"], v)
    assert math.isclose(sum(phi.values()), v({"a", "b"}) - v(set()))
    assert math.isclose(phi["a"], 5.5) and math.isclose(phi["b"], 4.5) and phi["c"] == 0
    assert math.isclose(inter[("a", "b")], 5)


def test_roll_forward_uses_schedule_and_drops_matured():
    deal = {"deal_id": "1", "report_dttm": "2022-08-31", "deal_report_dttm": "2022-08-31",
            "maturity_dttm": "2023-02-28", "current_principal_amt": "300", "accrued_interest": "5",
            "principal_payment": "", "interest_payment": ""}
    flows = [ScheduledFlow("2022-09-30", 100, 200), ScheduledFlow("2022-10-31", 100, 100),
             ScheduledFlow("2023-02-28", 100, 0)]
    r = roll_forward(deal, flows, "2022-10-31")
    assert r["current_principal_amt"] == "100.00" and r["report_dttm"] == "2022-10-31"
    assert r["deal_report_dttm"] == "2022-10-31" and r["accrued_interest"] == ""
    assert roll_forward(deal, flows, "2023-03-31") is None
    with pytest.raises(ValueError):
        roll_forward(deal, flows, "2022-01-01")


def test_roll_forward_trims_explicit_schedule():
    deal = {"deal_id": "2", "report_dttm": "2022-08-31", "maturity_dttm": "2024-01-01",
            "current_principal_amt": "30", "principal_payment": "2022-10-01,10 2023-01-01,10 2024-01-01,10",
            "interest_payment": "2022-10-01 2023-01-01 2024-01-01"}
    r = roll_forward(deal, [], "2022-12-31")
    assert r["principal_payment"] == "2023-01-01,10.0 2024-01-01,10.0"
    assert r["current_principal_amt"] == "20.00"
    assert r["interest_payment"] == "2023-01-01 2024-01-01"
