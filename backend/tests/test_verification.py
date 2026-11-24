from factor_lab.adapters.llm.template import TemplateExplainer
from factor_lab.application.explanation import answer_texts
from factor_lab.application.verification import verify_text

FACTS = [
    {"id": "F1", "label": "EC", "value": -1234567.0, "unit": "RUB", "display": "−1.23M ₽"},
    {"id": "F2", "label": "R²", "value": 0.873, "unit": "ratio", "display": "0.87"},
    {"id": "F3", "label": "share", "value": 0.215, "unit": "share", "display": "21.5%"},
]


def test_numbers_from_facts_pass():
    v = verify_text(["EC fell by −1.23M ₽ [F1], R² 0.87 [F2], share 21.5% [F3] on 2023-08-31."], FACTS)
    assert v["status"] == "ok" and v["checked_numbers"] == 3


def test_invented_number_is_flagged():
    v = verify_text(["Capital rose by 5.7M ₽ [F9]"], FACTS)
    assert v["status"] == "warnings"
    assert v["unverified_numbers"] == ["5.7M"] and v["unknown_fact_ids"] == ["F9"]


def test_template_explainer_is_self_consistent():
    facts = {"facts": FACTS + [{"id": "F4", "label": "EC Shapley: Curve <1Y",
                                "value": -2e6, "unit": "RUB", "display": "−2.00M ₽"}],
             "reliability": {"confidence": "medium", "reasons": ["too few periods"]}, "limitations": []}
    answer = TemplateExplainer().explain(facts, None)
    assert answer["blocks"] and "{{" in answer["headline"]
    assert verify_text(answer_texts(answer, facts["facts"]), facts["facts"])["status"] == "ok"


def test_placeholders_expand_and_unknown_ids_are_flagged():
    answer = {"headline": "EC changed by {{F1}}", "blocks": [
        {"type": "metrics", "text": None, "fact_ids": ["F2", "F7"], "chart": None, "metric": None, "tone": None}],
        "follow_ups": [], "confidence": "medium"}
    v = verify_text(answer_texts(answer, FACTS), FACTS)
    assert v["unknown_fact_ids"] == ["F7"] and v["unverified_numbers"] == []


def test_numbers_inside_text_facts_are_allowed():
    facts = FACTS + [{"id": "F5", "label": "Deals", "value": "54 → 13", "unit": "text", "display": "54 → 13"}]
    assert verify_text(["Fewer deals: 54 → 13 [F5]."], facts)["status"] == "ok"
