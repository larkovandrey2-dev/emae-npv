from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from uuid import uuid4
from copy import deepcopy
from ..config import ASSESSMENT_DATE
from ..integrations.cashflow_rs import EngineError
from ..domain import dec
from ..catalog.service import available_factors, scheme_compatible
import time


class InputError(ValueError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def metric(base, stress):
    return {
        "base": format(base, "f"),
        "stress": format(stress, "f"),
        "dnpv": format(stress - base, "f"),
    }


def _amt(s):
    return Decimal(str(s))


def _snap_metrics(result, deal_ids):
    per = {}
    b = Decimal(0)
    s = Decimal(0)
    for ident in deal_ids:
        row = result["base"]["summary"][ident]
        row_s = result["stress"]["summary"][ident]
        bn = Decimal(row["npv"])
        sn = Decimal(row_s["npv"])
        b += bn
        s += sn
        per[ident] = metric(bn, sn)
    return metric(b, s), per


def value_from_pair(snapshot, profile, act_act_as_365, engine, pair, id_map=None):
    ids = [d["id"] for d in snapshot]
    id_map = id_map or {x: x for x in ids}
    for ident in ids:
        key = id_map[ident]
        a = pair["base"]["flows"][key]
        b = pair["stress"]["flows"][key]
        if [
            (
                x["date"],
                x["principal"],
                x["interest"],
                x["total"],
                x["remaining_principal"],
            )
            for x in a
        ] != [
            (
                x["date"],
                x["principal"],
                x["interest"],
                x["total"],
                x["remaining_principal"],
            )
            for x in b
        ]:
            raise EngineError("FIXED-потоки различаются между BASE и STRESS")
    view = {
        scenario: {
            bucket: {d["id"]: pair[scenario][bucket][id_map[d["id"]]] for d in snapshot}
            for bucket in ("summary", "flows")
        }
        for scenario in ("base", "stress")
    }
    portfolio, per = _snap_metrics(view, ids)
    output = []
    for ident in ids:
        base = pair["base"]["flows"][id_map[ident]]
        stress = pair["stress"]["flows"][id_map[ident]]
        if len(base) != len(stress):
            raise EngineError("Число потоков сценариев не совпадает")
        flows = [
            {
                "date": a["date"],
                "principal": a["principal"],
                "interest": a["interest"],
                "total": a["total"],
                "remaining_principal": a["remaining_principal"],
                "df_base": a["df"],
                "df_stress": b["df"],
                "pv_base": a["pv"],
                "pv_stress": b["pv"],
            }
            for a, b in zip(base, stress)
        ]
        output.append({"deal_id": ident, "metrics": per[ident], "flows": flows})
    meta = engine.metadata()
    return {
        "id": str(uuid4()),
        "created_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "profile": deepcopy(profile),
        "act_act_as_365": act_act_as_365,
        "snapshot": deepcopy(snapshot),
        "portfolio": portfolio,
        "deals": output,
        "engine": {
            "commit": meta["commit"],
            "sha256": meta["sha256"],
            "version": meta["version"],
        },
        "checks": [
            "Сверены ID и полнота summary/потоков",
            "Потоки FIXED совпадают в BASE и STRESS",
            "NPV взят из summary; портфель — сумма сделок",
        ],
    }


def value_snapshot(snapshot, profile, act_act_as_365, engine, deadline=None):
    ids = [d["id"] for d in snapshot]
    if len(set(ids)) != len(ids):
        raise InputError("deal_ids", "ID сделок должны быть уникальными")
    pair = engine.run_pair(snapshot, profile, act_act_as_365, deadline=deadline)
    return value_from_pair(snapshot, profile, act_act_as_365, engine, pair)


def _discount_effect(original, changed):
    return {
        "base": format(Decimal(changed["portfolio"]["base"]) - Decimal(original["portfolio"]["base"]), "f"),
        "stress": format(Decimal(changed["portfolio"]["stress"]) - Decimal(original["portfolio"]["stress"]), "f"),
        "dnpv": format(Decimal(changed["portfolio"]["dnpv"]) - Decimal(original["portfolio"]["dnpv"]), "f"),
    }


def shifted_profile(profile, factor, value):
    bp = dec(value)
    changed = deepcopy(profile)
    for node in changed["nodes"]:
        scenario = node["scenario"]
        apply = factor != "stress_shift" or scenario == profile["stress_scenario"]
        if not apply:
            continue
        weight = Decimal(1)
        if factor == "long_end_shift":
            days = Decimal(node["tenor_days"])
            weight = min(Decimal(1), max(Decimal(0), (days - 365) / Decimal(3285)))
        rate = Decimal(node["zero_rate_percent"]) + bp * weight / Decimal(100)
        node["zero_rate_percent"] = format(rate, "f")
        t = Decimal(node["tenor_days"]) / Decimal(365)
        if changed["compounding"] == "annual":
            if rate <= -100:
                raise InputError("values", "Сдвиг даёт недопустимую годовую ставку ниже −100%")
            df = Decimal(1) / (Decimal(1) + rate / 100) ** t
        else:
            df = (-rate / 100 * t).exp()
        if not df.is_finite() or df <= 0:
            raise InputError("values", "Сдвиг даёт недопустимый коэффициент дисконтирования")
        node["df"] = format(df, "f")
    return changed


def run_discount_experiment(request, catalog, profile, engine):
    deadline = time.monotonic() + 60
    ids = request.deal_ids
    if len(set(ids)) != len(ids):
        raise InputError("deal_ids", "ID сделок должны быть уникальными")
    snapshot = []
    for ident in ids:
        deal = catalog.get(ident)
        if not deal:
            raise KeyError(f"Сделка {ident} не найдена")
        if not deal["eligible"]:
            raise InputError("deal_ids", f"Сделка {ident} недоступна: {', '.join(deal['restrictions'])}")
        snapshot.append(deal)
    original = value_snapshot(snapshot, profile, request.act_act_as_365, engine, deadline)
    variants = []
    for value in request.values:
        curve = shifted_profile(profile, request.factor, value)
        valuation = value_snapshot(snapshot, curve, request.act_act_as_365, engine, deadline)
        # A curve change must not alter undiscounted contractual cash flows.
        for before, after in zip(original["deals"], valuation["deals"]):
            keys = ("date", "principal", "interest", "total", "remaining_principal")
            if [[flow[k] for k in keys] for flow in before["flows"]] != [[flow[k] for k in keys] for flow in after["flows"]]:
                raise EngineError("Сдвиг кривой изменил недисконтированные FIXED-потоки")
        variants.append({"value": value, "valuation": valuation, "effect": _discount_effect(original, valuation)})
    return {"original": original, "variants": variants}


def apply_factor(snapshot, target_id, factor, value):
    copied = deepcopy(snapshot)
    d = next((x for x in copied if x["id"] == target_id), None)
    if d is None:
        raise InputError("target_deal_id", "Целевая сделка отсутствует в snapshot")
    if not d["eligible"]:
        raise InputError("target_deal_id", "Сделка не входит в поддерживаемый набор")
    f = d["fields"]
    v = str(value)
    supported = available_factors(f, d["eligible"], scheme_compatible(f, d["eligible"]))
    if factor not in supported:
        raise InputError("factor", f"Фактор {factor} недоступен для этой сделки")
    if factor == "amount":
        m = dec(v)
        if m <= 0 or m > Decimal("1000000000") or max(0, -m.as_tuple().exponent) > 8:
            raise InputError(
                "values",
                "Множитель суммы должен быть положительным, не больше 1 000 000 000 и с точностью до 8 знаков",
            )
        amount = (_amt(f["current_principal_amt"]) * m).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount < Decimal("0.01"):
            raise InputError("values", "Новый остаток меньше одной копейки")
        if amount > Decimal("1000000000000000"):
            raise InputError("values", "Новый остаток превышает 1 квадриллион рублей")
        f["current_principal_amt"] = format(amount, "f")
        d["current_principal_amt"] = f["current_principal_amt"]
    elif factor == "rate":
        x = dec(v)
        if x < 0 or x > 1 or max(0, -x.as_tuple().exponent) > 8:
            raise InputError(
                "values", "Ставка должна быть долей от 0 до 1 с точностью до 8 знаков"
            )
        f["interest_rate"] = format(x, "f")
        d["interest_rate"] = f["interest_rate"]
    elif factor == "accrued_interest":
        x = dec(v)
        if not (f.get("accrued_interest") or "").strip():
            raise InputError("factor", "Начисленные проценты доступны только при явно заданном исходном значении")
        if x < 0 or x > Decimal("1000000000000000") or max(0, -x.as_tuple().exponent) > 2:
            raise InputError("values", "Начисленные проценты задаются в рублях от 0 до 1 квадриллиона с точностью до копеек")
        f["accrued_interest"] = format(x, "f")
    elif factor == "term":
        try:
            months = int(v)
        except Exception as e:
            raise InputError(
                "values", "Срок задаётся целыми месяцами от 1 до 360"
            ) from e
        if str(months) != v or not 1 <= months <= 360:
            raise InputError("values", "Срок задаётся целыми месяцами от 1 до 360")
        # month arithmetic clips to final calendar day
        from calendar import monthrange

        anchor = date.fromisoformat(ASSESSMENT_DATE)
        y = anchor.year + (anchor.month - 1 + months) // 12
        m = (anchor.month - 1 + months) % 12 + 1
        end = date(y, m, min(anchor.day, monthrange(y, m)[1]))
        f["maturity_dttm"] = end.isoformat()
        d["maturity_dttm"] = end.isoformat()
    elif factor == "scheme":
        if not d["scheme_editable"]:
            raise InputError(
                "values",
                "Смену схемы можно проверять только на согласованном шаблоне ID 28 или новой сделке",
            )
        if v not in ("ANN", "RGM", "SIMPLE"):
            raise InputError("values", "Неизвестная схема")
        f["discriminator"] = v
        d["discriminator"] = v
    elif factor == "day_count":
        mapping = {"ACT365": "ACT/365", "ACTACT": "ACT/ACT ISDA"}
        if v not in mapping:
            raise InputError("values", "Выберите ACT365 или ACTACT")
        f["day_convention_cd"] = mapping[v]
        d["day_convention_cd"] = mapping[v]
    elif factor == "payment_day":
        try:
            day = int(v)
        except ValueError as e:
            raise InputError("values", "День платежа задаётся числом от 1 до 31") from e
        if str(day) != v or not 1 <= day <= 31:
            raise InputError("values", "День платежа задаётся числом от 1 до 31")
        f["interest_payment_rule_day_num"] = v
        f["regular_payment_rule_day_num"] = v
    elif factor == "payment_frequency":
        if v not in ("1M", "3M", "6M"):
            raise InputError("values", "Частота задаётся как 1M, 3M или 6M")
        # For SIMPLE the regular principal date is maturity; only interest has
        # a recurring schedule, so changing this field has a real isolated effect.
        f["interest_payment_rule_period_cd"] = v
    else:
        raise InputError("factor", "Неизвестный фактор")
    d["available_factors"] = available_factors(
        f, d["eligible"], scheme_compatible(f, d["eligible"])
    )
    return copied


def run_interaction(request, catalog, profile, engine):
    ids = request.deal_ids
    if len(set(ids)) != len(ids):
        raise InputError("deal_ids", "ID сделок должны быть уникальными")
    if request.target_deal_id not in ids:
        raise InputError("target_deal_id", "Целевая сделка должна входить в портфель")
    if request.factor_a == request.factor_b:
        raise InputError("factor_b", "Выберите два разных фактора")
    snapshot = []
    for ident in ids:
        d = catalog.get(ident)
        if not d:
            raise KeyError(f"Не найдена сделка {ident}")
        if not d["eligible"]:
            raise InputError("deal_ids", f"Сделка {ident} недоступна: {', '.join(d['restrictions'])}")
        snapshot.append(deepcopy(d))
    original_catalog = snapshot
    # All four cells are independently derived from the catalog snapshot. Values
    # (including amount multipliers) therefore never compound between cells.
    a_base = request.base_value_a
    b_base = request.base_value_b
    anchor = deepcopy(original_catalog)
    if a_base is not None:
        anchor = apply_factor(anchor, request.target_deal_id, request.factor_a, a_base)
    if b_base is not None:
        anchor = apply_factor(anchor, request.target_deal_id, request.factor_b, b_base)
    anchor_target = next(d for d in anchor if d["id"] == request.target_deal_id)
    supported = available_factors(anchor_target["fields"], anchor_target["eligible"], scheme_compatible(anchor_target["fields"], anchor_target["eligible"]))
    original_target = next(d for d in original_catalog if d["id"] == request.target_deal_id)
    initially_supported = available_factors(original_target["fields"], original_target["eligible"], scheme_compatible(original_target["fields"], original_target["eligible"]))
    for field, factor in (("factor_a", request.factor_a), ("factor_b", request.factor_b)):
        if factor not in supported or factor not in initially_supported:
            raise InputError(field, f"Фактор {factor} недоступен для исходного шаблона")
    if request.act_act_as_365 and "day_count" in (request.factor_a, request.factor_b):
        raise InputError("factor_a", "Фактор day_count недоступен в режиме Excel ACT/365")
    writes = {
        "amount": {"current_principal_amt"}, "rate": {"interest_rate"},
        "term": {"maturity_dttm"}, "scheme": {"discriminator"},
        "day_count": {"day_convention_cd"},
        "payment_day": {"interest_payment_rule_day_num", "regular_payment_rule_day_num"},
        "payment_frequency": {"interest_payment_rule_period_cd"},
        "accrued_interest": {"accrued_interest"},
    }
    if writes[request.factor_a] & writes[request.factor_b]:
        raise InputError("factor_b", "Факторы изменяют пересекающиеся поля расписания")
    def from_anchor(a_value=None, b_value=None):
        state = deepcopy(original_catalog)
        if a_base is not None:
            state = apply_factor(state, request.target_deal_id, request.factor_a, a_base)
        if b_base is not None:
            state = apply_factor(state, request.target_deal_id, request.factor_b, b_base)
        # Re-apply each axis from the original snapshot to avoid compounding.
        if a_value is not None:
            state = apply_factor(original_catalog, request.target_deal_id, request.factor_a, a_value)
            if b_base is not None:
                state = apply_factor(state, request.target_deal_id, request.factor_b, b_base)
        if b_value is not None:
            # Reconstruct from the catalog so both selected factor levels are direct.
            state = deepcopy(original_catalog)
            if a_value is not None:
                state = apply_factor(state, request.target_deal_id, request.factor_a, a_value)
            elif a_base is not None:
                state = apply_factor(state, request.target_deal_id, request.factor_a, a_base)
            state = apply_factor(state, request.target_deal_id, request.factor_b, b_value)
        return state
    states = {
        "original": from_anchor(),
        "a": from_anchor(a_value=request.value_a),
        "b": from_anchor(b_value=request.value_b),
        "both": from_anchor(a_value=request.value_a, b_value=request.value_b),
    }
    # Both one-factor states must accept the other edit, otherwise the two
    # factors depend on edit order and cannot be interpreted independently.
    try:
        apply_factor(states["a"], request.target_deal_id, request.factor_b, request.value_b)
        apply_factor(states["b"], request.target_deal_id, request.factor_a, request.value_a)
    except InputError as e:
        raise InputError("factor_b", "Факторы зависят от порядка применения и не образуют независимую пару") from e
    combined, maps = [], {}
    for state_name, state in states.items():
        mapping = {}
        for deal in state:
            internal = f"{state_name}:{deal['id']}"
            clone = deepcopy(deal)
            clone["id"] = internal
            combined.append(clone)
            mapping[deal["id"]] = internal
        maps[state_name] = mapping
    pair = engine.run_pair(combined, profile, request.act_act_as_365)
    valuations = {
        name: value_from_pair(state, profile, request.act_act_as_365, engine, pair, maps[name])
        for name, state in states.items()
    }
    keys = ("base", "stress", "dnpv")
    def effect(name):
        return {key: format(Decimal(valuations[name]["portfolio"][key]) - Decimal(valuations["original"]["portfolio"][key]), "f") for key in keys}
    effects = {name: effect(name) for name in ("a", "b", "both")}
    effects["interaction"] = {key: format(Decimal(effects["both"][key]) - Decimal(effects["a"][key]) - Decimal(effects["b"][key]), "f") for key in keys}
    return {"states": valuations, "effects": effects}


def run_experiment(request, catalog, profile, engine, repository):
    if request.factor == "day_count" and request.act_act_as_365:
        raise InputError("factor", "Фактор day_count недоступен в режиме Excel ACT/365")
    ids = request.deal_ids
    if len(set(ids)) != len(ids):
        raise InputError("deal_ids", "ID сделок должны быть уникальными")
    if request.target_deal_id not in ids:
        raise InputError("target_deal_id", "Целевая сделка должна входить в портфель")
    snapshot = []
    for ident in ids:
        d = catalog.get(ident)
        if not d:
            raise KeyError(f"Не найдена сделка {ident}")
        if not d["eligible"]:
            raise InputError(
                "deal_ids", f"Сделка {ident} недоступна: {', '.join(d['restrictions'])}"
            )
        snapshot.append(deepcopy(d))
    # Один пакет состояний: BASE и STRESS вызываются ровно по одному разу для всей серии.
    snapshots = [snapshot] + [
        apply_factor(snapshot, request.target_deal_id, request.factor, v)
        for v in request.values
    ]
    combined = []
    maps = []
    for state_index, state in enumerate(snapshots):
        mapping = {}
        for d in state:
            internal = f"state{state_index}:{d['id']}"
            mapping[d["id"]] = internal
            clone = deepcopy(d)
            clone["id"] = internal
            combined.append(clone)
        maps.append(mapping)
    pair = engine.run_pair(combined, profile, request.act_act_as_365)
    valuations = [
        value_from_pair(state, profile, request.act_act_as_365, engine, pair, mapping)
        for state, mapping in zip(snapshots, maps)
    ]
    if request.factor == "amount":
        for valuation in valuations[1:]:
            valuation["checks"].append(
                "Остаток варианта округлён до копейки по правилу ROUND_HALF_UP"
            )
    original = valuations[0]
    variants = []
    for value, valuation in zip(request.values, valuations[1:]):
        effect = {
            k: format(
                Decimal(valuation["portfolio"][k]) - Decimal(original["portfolio"][k]),
                "f",
            )
            for k in ("base", "stress", "dnpv")
        }
        orig_target = next(
            x for x in original["deals"] if x["deal_id"] == request.target_deal_id
        )["metrics"]
        changed_target = next(
            x for x in valuation["deals"] if x["deal_id"] == request.target_deal_id
        )["metrics"]
        target_effect = {
            k: format(Decimal(changed_target[k]) - Decimal(orig_target[k]), "f")
            for k in ("base", "stress", "dnpv")
        }
        pct = {
            k: (
                format(
                    Decimal(effect[k]) / Decimal(original["portfolio"][k]) * 100, "f"
                )
                if Decimal(original["portfolio"][k]) != 0
                else None
            )
            for k in ("base", "stress", "dnpv")
        }
        variants.append(
            {
                "value": value,
                "valuation": valuation,
                "effect": effect,
                "target_effect": target_effect,
                "percent_effect": pct,
            }
        )
    from datetime import datetime, timezone

    experiment = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "target_deal_id": request.target_deal_id,
        "factor": request.factor,
        "values": request.values,
        "original": original,
        "variants": variants,
    }
    repository.save_experiment(experiment)
    return experiment
