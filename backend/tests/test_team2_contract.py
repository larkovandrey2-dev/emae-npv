import pytest

from factor_lab.adapters.cashflow_http import CalculatorError, _validate_valuation_response
from factor_lab.adapters.curve_parser_http import CurveParserClient, CurveServiceError, REQUIRED_SCENARIOS


def curve_payload():
    return {
        "date": "2026-11-20",
        "tenors": [
            {"tenor_years": y, "base_rate": 0.1,
             "scenarios": [{"scenario": n, "rate": 0.1} for n in sorted(REQUIRED_SCENARIOS)]}
            for y in (0.25, 1.0, 5.0, 10.0)
        ],
    }


def test_curve_parser_accepts_complete_six_scenario_grid():
    result = CurveParserClient._parse(curve_payload())
    assert set(result.shocked) == REQUIRED_SCENARIOS
    assert [p.tenor_years for p in result.base.points] == [0.25, 1, 5, 10]
    assert all(c.trade_date == "2026-11-20" for c in result.shocked.values())


@pytest.mark.parametrize("mutate", [
    lambda p: p["tenors"][0]["scenarios"].pop(),
    lambda p: p["tenors"][0].update(tenor_years=0),
    lambda p: p["tenors"][1].update(tenor_years=0.25),
    lambda p: p.update(date="not-a-date"),
    lambda p: p["tenors"][0].update(base_rate=float("nan")),
])
def test_curve_parser_rejects_incomplete_or_invalid_curve(mutate):
    payload = curve_payload()
    mutate(payload)
    with pytest.raises(CurveServiceError):
        CurveParserClient._parse(payload)


def test_valuation_response_requires_every_requested_scenario_at_portfolio_and_deal_level():
    scenarios = ["BASE", "PARALLEL_UP"]
    response = {
        "scenarios": [{"scenario": name, "total": {"npv": 10.0}} for name in scenarios],
        "deals": [{"deal_id": 1, "scenarios": [
            {"scenario": name, "summary": {"npv": 5.0}} for name in scenarios
        ]}],
    }
    assert _validate_valuation_response(response, scenarios) is response
    response["deals"][0]["scenarios"].pop()
    with pytest.raises(CalculatorError):
        _validate_valuation_response(response, scenarios)


def test_portfolio_http_rejects_partial_valuation():
    import httpx
    from factor_lab.adapters.cashflow_http import HttpPortfolioSession

    curves = CurveParserClient._parse(curve_payload()).all()

    def respond(request):
        if request.url.path == "/api/v1/portfolios":
            return httpx.Response(201, json={"id": "test"})
        assert request.url.path == "/api/v1/portfolios/test/valuations"
        return httpx.Response(200, json={
            "as_of": "2026-11-20", "valued": 1, "matured": 0,
            "failed_total": 1, "failures": [{"deal_id": 2, "error": "schedule"}],
            "sets": [{"name": "own", "npv": dict.fromkeys(curves, 10.0)}], "deals": [],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        session = HttpPortfolioSession(client, "http://calculator.test", [])
        with pytest.raises(CalculatorError, match="не оценено сделок: 1"):
            session.value("2026-11-20", [("own", curves)])
