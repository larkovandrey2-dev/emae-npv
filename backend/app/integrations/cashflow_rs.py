from __future__ import annotations
import csv, hashlib, json, os, subprocess, tempfile, time
from pathlib import Path
from decimal import Decimal
from datetime import date
from typing import Any
from ..config import ENGINE_BIN, INDICATOR_FILE, PROCESS_TIMEOUT, RUNTIME, ROOT_DIR
from ..catalog.service import FIELDS


class EngineError(RuntimeError):
    def __init__(self, message, technical=None):
        super().__init__(message)
        self.technical = technical


class EngineTimeout(EngineError):
    pass


class CashflowEngine:
    def __init__(
        self,
        binary: Path = ENGINE_BIN,
        indicator: Path = INDICATOR_FILE,
        timeout: int = PROCESS_TIMEOUT,
    ):
        self.binary = binary
        self.indicator = indicator
        self.timeout = timeout

    def metadata(self):
        lock_path = ROOT_DIR / "engine.lock.json"
        info_path = ROOT_DIR / ".engine" / "build-info.json"
        binary_exists = self.binary.is_file()
        sha = None
        try:
            if binary_exists:
                sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
            lock = json.loads(lock_path.read_text())
            info = json.loads(info_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"ready": False, "commit": None, "sha256": sha, "version": None}
        if not isinstance(lock, dict) or not isinstance(info, dict):
            return {"ready": False, "commit": None, "sha256": sha, "version": None}

        ready = (
            binary_exists
            and os.access(self.binary, os.X_OK)
            and self.indicator.is_file()
            and info.get("commit") == lock.get("commit")
            and info.get("version") == lock.get("package_version")
            and bool(info.get("binary_sha256"))
            and sha == info.get("binary_sha256")
        )
        return {
            "ready": ready,
            "commit": info.get("commit", lock.get("commit")),
            "sha256": sha,
            "version": info.get("version", lock.get("package_version")),
        }

    def run_pair(
        self,
        deals: list[dict[str, Any]],
        profile: dict[str, Any],
        act_act_as_365: bool = False,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        meta = self.metadata()
        if not meta["ready"]:
            raise EngineError("Расчётный движок или его build metadata не готовы")
        if not self.indicator.is_file():
            raise EngineError("Не найдена история индикатора, необходимая CLI")
        RUNTIME.mkdir(parents=True, exist_ok=True)
        deadline = deadline or (time.monotonic() + 60)
        with tempfile.TemporaryDirectory(prefix="run-", dir=RUNTIME) as td:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineTimeout("Превышено общее время расчёта серии")
            base = self._invoke(
                deals,
                profile,
                profile["base_scenario"],
                act_act_as_365,
                Path(td) / "base",
                min(self.timeout, remaining),
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineTimeout("Превышено общее время расчёта серии")
            stress = self._invoke(
                deals,
                profile,
                profile["stress_scenario"],
                act_act_as_365,
                Path(td) / "stress",
                min(self.timeout, remaining),
            )
        return {"base": base, "stress": stress}

    def _invoke(self, deals, profile, scenario, compat, directory, timeout=None):
        directory.mkdir()
        mapping = {}
        source = directory / "deals.csv"
        curve = directory / "curve.csv"
        out = directory / "flows.csv"
        summary = directory / "summary.csv"
        with source.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";", lineterminator="\n")
            w.writeheader()
            for i, d in enumerate(deals, 1):
                row = dict(d["fields"])
                eid = i
                mapping[str(eid)] = d["id"]
                row["deal_id"] = str(eid)
                w.writerow({k: row.get(k, "") for k in FIELDS})
        with curve.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(
                [
                    "curve_type",
                    "curve_date",
                    "currency",
                    "scenario",
                    "tenor_days",
                    "zero_rate_percent",
                ]
            )
            for n in profile["nodes"]:
                if n["scenario"] == scenario:
                    w.writerow(
                        [
                            profile["curve_type"],
                            profile["report_date"],
                            profile["currency"],
                            scenario,
                            n["tenor_days"],
                            n["zero_rate_percent"],
                        ]
                    )
        if not any(n["scenario"] == scenario for n in profile["nodes"]):
            raise EngineError(f"В кривой нет сценария {scenario}")
        ids = ",".join(mapping)
        args = [
            str(self.binary),
            "--deals",
            str(source),
            "--discount-curve",
            str(curve),
            "--curve-type",
            profile["curve_type"],
            "--scenario",
            scenario,
            "--discount-compounding",
            profile["compounding"],
            "--indicator",
            f"CBKEY1M={self.indicator}",
            "--deal-id",
            ids,
            "--out",
            str(out),
            "--summary",
            str(summary),
            "--threads",
            "2",
        ]
        if compat:
            args.append("--act-act-as-365")
        try:
            p = subprocess.run(
                args,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=max(0.001, timeout if timeout is not None else self.timeout),
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise EngineTimeout("Превышено время расчёта движка") from e
        except OSError as e:
            raise EngineError("Не удалось запустить расчётный движок", str(e)) from e
        if p.returncode != 0:
            raise EngineError(
                f"Движок завершился с кодом {p.returncode}", p.stderr[-4000:]
            )
        if not out.exists() or not summary.exists():
            raise EngineError("Движок не создал полный результат")
        return self._parse(out, summary, mapping, profile, deals)

    def _parse(self, out, summary, mapping, profile=None, deals=None):
        try:
            return self._parse_outputs(out, summary, mapping, profile, deals)
        except EngineError:
            raise
        except (
            OSError,
            csv.Error,
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            ArithmeticError,
        ) as e:
            raise EngineError(
                "Некорректный или неполный CSV-вывод движка", str(e)
            ) from e

    def _parse_outputs(self, out, summary, mapping, profile=None, deals=None):
        with summary.open(encoding="utf-8-sig", newline="") as f:
            sums = list(csv.DictReader(f, delimiter=";"))
        expected = set(mapping)
        data_rows = [r for r in sums if r["deal_id"] != "TOTAL"]
        actual = [r["deal_id"] for r in data_rows]
        if (
            len(actual) != len(set(actual))
            or set(actual) != expected
            or len([r for r in sums if r["deal_id"] == "TOTAL"]) != 1
        ):
            raise EngineError("Неполный, повторный или неожиданный summary движка")
        sm = {mapping[r["deal_id"]]: r for r in sums if r["deal_id"] != "TOTAL"}
        total = next(r for r in sums if r["deal_id"] == "TOTAL")
        tolerance = Decimal("0.005") * (len(expected) + 1)
        for field in (
            "principal",
            "interest",
            "total_cf",
            "pv_principal",
            "pv_interest",
            "npv",
        ):
            try:
                reported = Decimal(total[field])
                components = sum((Decimal(r[field]) for r in sm.values()), Decimal(0))
                if not reported.is_finite() or abs(reported - components) > tolerance:
                    raise ValueError()
            except Exception as e:
                raise EngineError(
                    "TOTAL движка не сходится с суммой сделок в пределах округления"
                ) from e
        with out.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        flows = {v: [] for v in mapping.values()}
        for r in rows:
            ident = mapping.get(r.get("deal_id"))
            if ident is None:
                raise EngineError("Неожиданный ID в потоках движка")
            vals = [
                r[k]
                for k in (
                    "principal_cf",
                    "interest_cf",
                    "total_cf",
                    "balance_after",
                    "discount_factor",
                    "discounted_total_cf",
                )
            ]
            try:
                nums = [Decimal(v) for v in vals]
                if not all(n.is_finite() for n in nums) or nums[4] <= 0:
                    raise ValueError()
                if abs(nums[0] + nums[1] - nums[2]) > Decimal("0.011"):
                    raise ValueError()
            except Exception as e:
                raise EngineError("Некорректные или не конечные значения потока") from e
            if profile and (
                r["currency"] != profile["currency"]
                or r["curve_date"] != profile["report_date"]
            ):
                raise EngineError("Движок вернул неожиданную валюту или дату кривой")
            try:
                flow_date = date.fromisoformat(r["cashflow_date"])
                curve_date = date.fromisoformat(r["curve_date"])
                if profile and curve_date.isoformat() != profile["report_date"]:
                    raise ValueError()
            except Exception as e:
                raise EngineError("Некорректная дата потока или кривой") from e
            flows[ident].append(
                {
                    "date": r["cashflow_date"],
                    "principal": vals[0],
                    "interest": vals[1],
                    "total": vals[2],
                    "remaining_principal": vals[3],
                    "df": vals[4],
                    "pv_principal": r["discounted_principal_cf"],
                    "pv_interest": r["discounted_interest_cf"],
                    "pv": vals[5],
                }
            )
        deal_by_id = {d["id"]: d for d in (deals or [])}
        for ident, fs in flows.items():
            if not fs:
                raise EngineError(f"Нет потоков для сделки {ident}")
            dates = [date.fromisoformat(x["date"]) for x in fs]
            if dates != sorted(dates) or len(dates) != len(set(dates)):
                raise EngineError("Даты потоков не упорядочены или повторяются")
            if Decimal(fs[-1]["remaining_principal"]) != 0:
                raise EngineError(f"Остаток тела не закрыт для сделки {ident}")
            if profile:
                report = date.fromisoformat(profile["report_date"])
                maturity = (
                    date.fromisoformat(
                        deal_by_id[ident]["fields"]["maturity_dttm"][:10]
                    )
                    if ident in deal_by_id
                    else None
                )
                if dates[0] <= report or (
                    maturity and any(day > maturity for day in dates)
                ):
                    raise EngineError("Дата потока находится вне срока оценки сделки")
            s = sm[ident]
            tolerance = Decimal("0.005") * (len(fs) + 1)
            fields = (
                "npv",
                "principal",
                "interest",
                "total_cf",
                "pv_principal",
                "pv_interest",
            )
            try:
                if not all(Decimal(s[k]).is_finite() for k in fields):
                    raise ValueError()
                check_fields = (
                    ("principal", "principal"),
                    ("interest", "interest"),
                    ("total_cf", "total"),
                    ("pv_principal", "pv_principal"),
                    ("pv_interest", "pv_interest"),
                    ("npv", "pv"),
                )
                for summary_key, flow_key in check_fields:
                    if (
                        abs(
                            sum((Decimal(x[flow_key]) for x in fs), Decimal(0))
                            - Decimal(s[summary_key])
                        )
                        > tolerance
                    ):
                        raise ValueError()
                if ident in deal_by_id:
                    initial = Decimal(
                        deal_by_id[ident]["fields"]["current_principal_amt"]
                    )
                    if (
                        abs(
                            Decimal(fs[0]["remaining_principal"])
                            + Decimal(fs[0]["principal"])
                            - initial
                        )
                        > tolerance
                    ):
                        raise ValueError()
                    if (
                        abs(
                            sum((Decimal(x["principal"]) for x in fs), Decimal(0))
                            - initial
                        )
                        > tolerance
                    ):
                        raise ValueError()
            except Exception as e:
                raise EngineError(
                    "Потоки не сходятся с summary или остатком сделки в пределах округления"
                ) from e
        return {"summary": sm, "total": total, "flows": flows}
