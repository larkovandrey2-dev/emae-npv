import csv, io

_NUMERIC_FIELDS = {
    "start_principal_amt",
    "current_principal_amt",
    "interest_rate",
    "base_indicator_rate",
    "interest_payment_rule_day_num",
    "regular_payment_rule_day_num",
    "repricing_rule_day_num",
    "accrued_interest",
    "cap_limit_interest_rate",
    "floor_limit_interest_rate",
    "sub_interest_rate",
    "sub_payment_rule_day_num",
    "sub_repricing_rule_day_num",
    "sub_cap_limit_interest_rate",
    "sub_floor_limit_interest_rate",
    "sub_coeff_index",
    "sub_repricing_rule_first_event_flg",
}


def excel_num(v):
    if v is None:
        return ""
    return str(v).replace(".", ",")


def excel_text(value):
    if value is None:
        return ""
    text = str(value)
    index = 0
    while index < len(text) and (text[index].isspace() or ord(text[index]) < 32):
        index += 1
    if index < len(text) and text[index] in "=+-@":
        return "'" + text
    return text


def experiment_csv(experiment):
    out = io.StringIO(newline="")
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    original = experiment["original"]
    profile = original["profile"]
    w.writerow(["Factor Lab — выгрузка эксперимента"])
    for label, value in [
        ("ID эксперимента", experiment["id"]),
        ("Создан", experiment["created_at"]),
        ("Движок commit", original["engine"]["commit"]),
        ("Движок версия", original["engine"]["version"]),
        ("Движок SHA-256", original["engine"]["sha256"]),
        ("Профиль ID", profile["id"]),
        ("Профиль", profile["name"]),
        ("Источник профиля", profile.get("source", "")),
        ("Синтетический профиль", str(profile.get("synthetic", False)).lower()),
        ("Тип кривой", profile["curve_type"]),
        ("Дата оценки", profile["report_date"]),
        ("Валюта", profile["currency"]),
        ("Соглашение начисления", profile["compounding"]),
        ("ACT/ACT как ACT/365", str(original["act_act_as_365"]).lower()),
        ("Фактор", experiment["factor"]),
        (
            "Единица фактора",
            {
                "amount": "множитель остатка; копейки, ROUND_HALF_UP",
                "rate": "доля годовых",
                "term": "месяцев от даты оценки",
                "scheme": "код схемы",
                "accrued_interest": "рубли начисленных процентов",
                "parallel_shift": "базисные пункты к BASE и STRESS",
                "stress_shift": "базисные пункты к STRESS",
                "long_end_shift": "базисные пункты, линейный вес от 365 до 3650 дней",
            }.get(experiment["factor"], ""),
        ),
        ("Целевая сделка", experiment.get("target_deal_id", "")),
    ]:
        w.writerow([excel_text(label), excel_text(value)])
    w.writerow([])
    w.writerow(["Узлы кривой"])
    w.writerow(["Сценарий", "Срок, дни", "Zero rate, %", "DF в узле"])
    for n in profile["nodes"]:
        w.writerow(
            [
                excel_text(n["scenario"]),
                n["tenor_days"],
                excel_num(n["zero_rate_percent"]),
                excel_num(n["df"]),
            ]
        )
    w.writerow([])
    w.writerow(["Snapshots сделок"])
    w.writerow(["Состояние", "Сделка ID", "Название", "Поле", "Значение"])
    states = [("Исходное", original)] + [
        (f"Вариант {i+1}: {v['value']}", v["valuation"])
        for i, v in enumerate(experiment["variants"])
    ]
    for state_name, val in states:
        for d in val["snapshot"]:
            w.writerow(
                [
                    excel_text(state_name),
                    excel_text(d["id"]),
                    excel_text(d["name"]),
                    "eligible",
                    str(d["eligible"]).lower(),
                ]
            )
            for key, value in d["fields"].items():
                w.writerow(
                    [
                        excel_text(state_name),
                        excel_text(d["id"]),
                        excel_text(d["name"]),
                        excel_text(key),
                        (
                            excel_num(value)
                            if key in _NUMERIC_FIELDS
                            else excel_text(value)
                        ),
                    ]
                )
    w.writerow([])
    header = [
        "Значение",
        "Портфель BASE исходный",
        "Портфель STRESS исходный",
        "Портфель dNPV исходный",
        "Портфель BASE вариант",
        "Портфель STRESS вариант",
        "Портфель dNPV вариант",
        "Эффект BASE",
        "Эффект STRESS",
        "Эффект dNPV",
        "Сделка BASE исходная",
        "Сделка STRESS исходная",
        "Сделка dNPV исходная",
        "Сделка BASE вариант",
        "Сделка STRESS вариант",
        "Сделка dNPV вариант",
        "Эффект сделки BASE",
        "Эффект сделки STRESS",
        "Эффект сделки dNPV",
    ]
    w.writerow(["Метрики и эффекты"])
    w.writerow(header)
    ot = next(
        d["metrics"]
        for d in original["deals"]
        if d["deal_id"] == experiment["target_deal_id"]
    )
    for v in experiment["variants"]:
        vt = next(
            d["metrics"]
            for d in v["valuation"]["deals"]
            if d["deal_id"] == experiment["target_deal_id"]
        )
        values = [
            v["value"],
            *(original["portfolio"][k] for k in ("base", "stress", "dnpv")),
            *(v["valuation"]["portfolio"][k] for k in ("base", "stress", "dnpv")),
            *(v["effect"][k] for k in ("base", "stress", "dnpv")),
            *(ot[k] for k in ("base", "stress", "dnpv")),
            *(vt[k] for k in ("base", "stress", "dnpv")),
            *(v["target_effect"][k] for k in ("base", "stress", "dnpv")),
        ]
        w.writerow(
            [
                (
                    excel_num(x)
                    if isinstance(x, (int, float))
                    or str(x).replace(".", "", 1).lstrip("-").isdigit()
                    else excel_text(x)
                )
                for x in values
            ]
        )
    return "\ufeff" + out.getvalue()
