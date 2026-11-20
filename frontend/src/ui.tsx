import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError, api, money, pct } from "./api";
import {
  addPercentagePoints,
  fractionToPercent,
  percentToFraction,
} from "./units.js";
import { ErrorBox, FlowTable, Metric } from "./components";
import type {
  CurveProfile,
  Deal,
  Experiment,
  HistoryItem,
  Valuation,
  InteractionResult,
  DiscountExperiment,
} from "./api";
const initialIds = ["1", "2", "28"];
const schemeLabel: Record<string, string> = {
  ANN: "Аннуитет",
  RGM: "Равное погашение тела",
  SIMPLE: "Тело в конце срока",
};
const factorNames: Record<string, string> = {
  amount: "Размер сделки",
  rate: "Договорная ставка",
  term: "Срок",
  scheme: "Схема",
  day_count: "База дней",
  payment_day: "День платежа",
  payment_frequency: "Частота платежей",
  accrued_interest: "Начисленные проценты",
  parallel_shift: "Параллельный сдвиг кривой",
  stress_shift: "Сдвиг стрессовой кривой",
  long_end_shift: "Сдвиг длинного конца кривой",
};
function decimal(value: string, digits = 2) {
  return new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(Number(value));
}
function formatFactorValue(factor: string, value: string) {
  if (factor === "rate") return `${fractionToPercent(value)}%`;
  if (factor === "term") return `${value} мес.`;
  if (factor === "amount")
    return `${decimal(String(Number(value) * 100), 1)}% от исходной суммы (${value}×)`;
  if (["parallel_shift", "stress_shift", "long_end_shift"].includes(factor)) return `${value} б.п.`;
  if (factor === "accrued_interest") return `${money(value)} ₽`;
  return schemeLabel[value] || value;
}
export default function App() {
  const [search, setSearch] = useState(""),
    [deals, setDeals] = useState<Deal[]>([]),
    [curves, setCurves] = useState<CurveProfile[]>([]),
    [history, setHistory] = useState<HistoryItem[]>([]),
    [health, setHealth] = useState("Проверяем подключение…");
  const [actActAs365, setActActAs365] = useState(false);
  const [selected, setSelected] = useState<string[]>([]),
    [profile, setProfile] = useState(""),
    [valuation, setValuation] = useState<Valuation | null>(null),
    [stale, setStale] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [technicalError, setTechnicalError] = useState(""),
    [experimentError, setExperimentError] = useState("");
  const [tab, setTab] = useState<"portfolio" | "valuation" | "experiment">(
    "portfolio",
  );
  const [createOpen, setCreateOpen] = useState(false),
    [form, setForm] = useState({
      name: "Учебный кредит",
      current_principal_amt: "1000000",
      interest_rate: "12",
      maturity_dttm: "2027-08-31",
      discriminator: "ANN",
    }),
    [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [target, setTarget] = useState(""),
    [factor, setFactor] = useState("amount"),
    [values, setValues] = useState("0.8; 0.9; 1; 1.1; 1.2"),
    [metric, setMetric] = useState("base"),
    [experiment, setExperiment] = useState<Experiment | null>(null),
    [experimentStale, setExperimentStale] = useState(false),
    [expanded, setExpanded] = useState<string | null>(null),
    [detail, setDetail] = useState<Experiment | null>(null);
  const [showAvailableOnly, setShowAvailableOnly] = useState(false);
  const [experimentMode, setExperimentMode] = useState<"single" | "two" | "discount">("single");
  const [discountFactor, setDiscountFactor] = useState("parallel_shift");
  const [discountValues, setDiscountValues] = useState("-100; -50; 0; 50; 100");
  const [discountResult, setDiscountResult] = useState<DiscountExperiment | null>(null);
  const [discountExpanded, setDiscountExpanded] = useState<number | null>(null);
  const [factorA, setFactorA] = useState("term"), [valueA, setValueA] = useState("36");
  const [factorB, setFactorB] = useState("scheme"), [valueB, setValueB] = useState("SIMPLE");
  const [baseValueA, setBaseValueA] = useState(""), [baseValueB, setBaseValueB] = useState("");
  const [interaction, setInteraction] = useState<InteractionResult | null>(null);
  useEffect(() => { setInteraction(null); }, [selected, target, profile, actActAs365]);
  useEffect(() => { if (discountResult) setExperimentStale(true); }, [selected, profile, actActAs365]);
  function reportError(error: unknown) {
    setError(
      error instanceof Error ? error.message : "Не удалось выполнить запрос.",
    );
    setTechnicalError(error instanceof ApiError ? error.technical || "" : "");
  }
  function displayDealId(id: string) {
    return id.startsWith("new-") ? "Своя сделка" : `ID ${id}`;
  }
  const eligible = useMemo(() => deals.filter((d) => d.eligible), [deals]);
  const current = deals.find((d) => d.id === target);
  const pairFactors = (current?.available_factors || []).filter((key) => ["term", "scheme", "amount", "rate", "day_count", "payment_day", "payment_frequency", "accrued_interest"].includes(key));
  const activeCurve = curves.find((c) => c.id === profile);
  const shownExperiment = detail || experiment;
  const displayedCurve = shownExperiment?.original.profile || activeCurve;
  const curveChartData = useMemo(() => {
    const m = new Map<number, any>();
    displayedCurve?.nodes.forEach((n) => {
      const row = m.get(n.tenor_days) || { tenor_days: n.tenor_days };
      row[n.scenario === "BASE" ? "base" : "stress"] = Number(
        n.zero_rate_percent,
      );
      m.set(n.tenor_days, row);
    });
    return [...m.values()].sort((a, b) => a.tenor_days - b.tenor_days);
  }, [displayedCurve]);
  const experimentChartData = useMemo(() => {
    if (!shownExperiment) return [];
    const factorType = shownExperiment.factor;
    const rows = shownExperiment.variants.map((variant, index) => {
      const raw = Number(variant.value);
      const categorical = ["scheme", "day_count", "payment_frequency"].includes(factorType);
      const x = categorical ? index : factorType === "rate" ? raw * 100 : raw;
      return {
        x: factorType === "scheme" ? index : x,
        label: formatFactorValue(factorType, variant.value),
        base: Number(variant.effect.base),
        stress: Number(variant.effect.stress),
        dnpv: Number(variant.effect.dnpv),
      };
    });
    return ["scheme", "day_count", "payment_frequency"].includes(factorType) ? rows : rows.sort((a, b) => a.x - b.x);
  }, [shownExperiment]);
  const experimentIsNumeric = !["scheme", "day_count", "payment_frequency"].includes(shownExperiment?.factor || "");
  const experimentDomain =
    experimentChartData.length === 1
      ? (() => {
          const x = experimentChartData[0].x;
          const step =
            shownExperiment?.factor === "amount"
              ? 0.05
              : shownExperiment?.factor === "rate"
                ? 1
                : Math.max(1, Math.abs(x) * 0.05);
          return [
            Math.max(0, x - step),
            shownExperiment?.factor === "rate"
              ? Math.min(100, x + step)
              : x + step,
          ];
        })()
      : ["dataMin", "dataMax"];
  async function reload() {
    try {
      const [d, c, h, healthResult] = await Promise.all([
        api<Deal[]>("/deals"),
        api<CurveProfile[]>("/curve-profiles"),
        api<HistoryItem[]>("/experiments"),
        api<any>("/health"),
      ]);
      setDeals(d);
      setCurves(c);
      setHistory(h);
      setHealth(
        healthResult.status === "ok" && healthResult.engine_ready
          ? "Движок готов"
          : "Движок не готов",
      );
      setSelected((s) =>
        s.length
          ? s.filter((id) => d.some((x) => x.id === id))
          : d
              .filter((x) => initialIds.includes(x.id) && x.eligible)
              .map((x) => x.id),
      );
      setProfile((p) => p || c.find((x) => x.id === "repo-up100-v1")?.id || c[0]?.id || "");
      setTarget((t) => t || (d.some((x) => x.id === "28" && x.eligible) ? "28" : d.find((x) => x.eligible)?.id || ""));
    } catch (e) {
      reportError(e);
      setHealth("Нет связи с API");
    }
  }
  useEffect(() => {
    reload();
  }, []);
  useEffect(() => {
    if (selected.length && !selected.includes(target)) setTarget(selected[0]);
    else if (!selected.length && target) setTarget("");
  }, [selected, target]);
  useEffect(() => {
    if (current?.available_factors?.length && !current.available_factors.includes(factor)) {
      setFactor(current.available_factors[0]);
      setError("Выбранный фактор недоступен для этой сделки. Установлен первый доступный фактор.");
    } else if (factor === "scheme" && current && !current.scheme_editable) {
      setFactor(current.available_factors.find((f) => f !== "scheme") || "amount");
      setError("Для этой сделки нельзя менять схему. Установлен доступный фактор.");
    }
  }, [factor, target, current]);
  useEffect(() => {
    if (factor === "amount") setValues("0.8; 0.9; 1; 1.1; 1.2");
    if (factor === "rate" && current) {
      const basePercent = fractionToPercent(current.interest_rate);
      setValues(
        [-2, -1, 0, 1, 2]
          .filter(
            (points) =>
              Number(basePercent) + points >= 0 &&
              Number(basePercent) + points <= 100,
          )
          .map((points) =>
            fractionToPercent(addPercentagePoints(basePercent, points)),
          )
          .join("; "),
      );
    }
    if (factor === "term") setValues("12; 24; 36; 60");
    if (factor === "scheme") setValues("ANN; RGM; SIMPLE");
    if (factor === "day_count") setValues("ACT365; ACTACT");
    if (factor === "payment_day") setValues("10; 20; 25");
    if (factor === "payment_frequency") setValues("1M; 3M; 6M");
    if (factor === "accrued_interest") setValues("0; 1000; 5000");
  }, [factor, target]);
  function markStale() {
    if (valuation) setStale(true);
    if (experiment && !detail) setExperimentStale(true);
  }
  async function calculate() {
    if (!selected.length || busy) return;
    setBusy(true);
    setError("");
    setTechnicalError("");
    try {
      const v = await api<Valuation>("/valuations", {
        method: "POST",
        body: JSON.stringify({
          deal_ids: selected,
          profile_id: profile,
          act_act_as_365: actActAs365,
        }),
      });
      setValuation(v);
      setStale(false);
      setTab("valuation");
    } catch (e) {
      reportError(e);
    } finally {
      setBusy(false);
    }
  }
  async function createDeal(e: React.FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!form.name.trim()) errs.name = "Введите название";
    if (
      !Number(form.current_principal_amt) ||
      Number(form.current_principal_amt) <= 0
    )
      errs.current_principal_amt = "Сумма должна быть больше нуля";
    if (
      Number(form.interest_rate) < 0 ||
      !Number.isFinite(Number(form.interest_rate))
    )
      errs.interest_rate = "Введите ставку в процентах";
    try {
      percentToFraction(form.interest_rate);
    } catch (error) {
      errs.interest_rate = (error as Error).message;
    }
    if (form.maturity_dttm <= "2022-08-31" || form.maturity_dttm > "2052-08-31")
      errs.maturity_dttm =
        "Дата должна быть после 31.08.2022 и не далее 30 лет";
    setFormErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setError("");
    setTechnicalError("");
    try {
      const d = await api<Deal>("/deals", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          current_principal_amt: String(form.current_principal_amt),
          interest_rate: percentToFraction(form.interest_rate),
          day_count: "ACT365", payment_day: "31", payment_frequency: "1M",
        }),
      });
      setDeals((s) => [...s, d]);
      if (selected.length < 10) {
        setSelected((s) => (s.length < 10 ? [...s, d.id] : s));
        setTarget(d.id);
      } else
        setError(
          "Сделка сохранена в каталоге. Чтобы добавить её, удалите одну из выбранных сделок.",
        );
      setCreateOpen(false);
      markStale();
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        setFormErrors((current) => ({ ...current, ...err.fieldErrors }));
        setError("Проверьте поля, выделенные в форме сделки.");
        setTechnicalError(err.technical || "");
      } else reportError(err);
    } finally {
      setBusy(false);
    }
  }
  async function runExperiment() {
    if (experimentMode === "discount") {
      const parsed = discountValues.split(/[;\s]+/).map((x) => x.trim()).filter(Boolean);
      const nums = parsed.map((x) => Number(x.replace(",", ".")));
      if (!selected.length || !parsed.length || parsed.length > 21 || nums.some((x) => !Number.isFinite(x) || Math.abs(x) > 500)) {
        setExperimentError("Введите от 1 до 21 значений в диапазоне от −500 до +500 б.п."); return;
      }
      setBusy(true); setExperimentError(""); setError(""); setTechnicalError(""); setDiscountResult(null); setExperiment(null); setDetail(null);
      try {
        const result = await api<DiscountExperiment>("/discount-experiments", { method: "POST", body: JSON.stringify({ deal_ids: selected, profile_id: profile, act_act_as_365: actActAs365, factor: discountFactor, values: nums.map(String) }) });
        setDiscountResult(result); setExperimentStale(false); setTab("experiment");
      } catch (e) { reportError(e); }
      finally { setBusy(false); }
      return;
    }
    if (!target || !selected.includes(target) || busy) return;
    const parsed = values
      .split(/[;\s]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    if (!parsed.length || parsed.length > 21) {
      setExperimentError("Укажите от 1 до 21 значений.");
      return;
    }
    if (factor === "rate") {
      try {
        parsed.forEach(percentToFraction);
      } catch (error) {
        setExperimentError((error as Error).message);
        return;
      }
    }
    setBusy(true);
    setError("");
    setTechnicalError("");
    setExperimentError("");
    try {
      const ex = await api<Experiment>("/experiments", {
        method: "POST",
        body: JSON.stringify({
          deal_ids: selected,
          profile_id: profile,
          act_act_as_365: actActAs365,
          target_deal_id: target,
          factor,
          values: factor === "rate" ? parsed.map(percentToFraction) : parsed.map((x) => factor === "amount" ? x.replace(",", ".") : x),
        }),
      });
      setExperiment(ex);
      setExperimentStale(false);
      setDetail(null);
      setHistory(await api<HistoryItem[]>("/experiments"));
      setTab("experiment");
    } catch (e) {
      if (e instanceof ApiError && e.fieldErrors.values) {
        setExperimentError(e.fieldErrors.values);
        setTechnicalError(e.technical || "");
        setError("");
      } else reportError(e);
    } finally {
      setBusy(false);
    }
  }
  async function runInteraction() {
    if (!target || !selected.includes(target) || busy || factorA === factorB || !pairFactors.includes(factorA) || !pairFactors.includes(factorB)) return;
    setInteraction(null);
    setExperiment(null);
    setDetail(null);
    setBusy(true); setError(""); setExperimentError("");
    try {
      const factorValue = (key: string, value: string) => key === "rate" ? percentToFraction(value) : value.trim().replace(",", ".");
      const result = await api<InteractionResult>("/interactions", {
        method: "POST",
        body: JSON.stringify({ deal_ids: selected, profile_id: profile, act_act_as_365: actActAs365,
          target_deal_id: target, factor_a: factorA, value_a: factorValue(factorA, valueA),
          factor_b: factorB, value_b: factorValue(factorB, valueB),
          base_value_a: baseValueA.trim() ? factorValue(factorA, baseValueA) : undefined,
          base_value_b: baseValueB.trim() ? factorValue(factorB, baseValueB) : undefined }),
      });
      setInteraction(result); setExperimentStale(false);
    } catch (e) { reportError(e); }
    finally { setBusy(false); }
  }
  async function openHistory(id: string) {
    setBusy(true);
    try {
      const e = await api<Experiment>(`/experiments/${id}`);
      setDetail(e);
      setExperiment(e);
      setExperimentStale(false);
    } catch (e) {
      reportError(e);
    } finally {
      setBusy(false);
    }
  }
  function download(id: string) {
    const a = document.createElement("a");
    a.href = `/api/experiments/${id}/export.csv`;
    a.download = `factor-lab-${id}.csv`;
    a.click();
  }
  const valuesLabel = experimentMode === "discount" ? "Сдвиг кривой, б.п. (от −500 до +500)" :
    factor === "amount"
      ? "Множитель (например 1.1 = +10%)"
      : factor === "rate"
        ? "Годовая договорная ставка, % (например 8.5)"
        : factor === "term"
          ? "Срок от даты оценки, месяцев"
          : factor === "scheme" ? "Схема" : factor === "day_count" ? "База дней: ACT365 или ACTACT" : factor === "payment_frequency" ? "Частота платежей: 1M, 3M или 6M" : "День месяца: целое число 1–31";
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brandmark">F</div>
          <div>
            <b>Factor Lab</b>
            <small>учебный стенд · команда 3</small>
          </div>
        </div>
        <div className="status">
          <i className={health === "Движок готов" ? "online" : ""} />
          {health}
          <span>·</span> Оценка на 31.08.2022
        </div>
      </header>
      <main>
        <section className="intro">
          <div>
            <div className="eyebrow">
              ЭКОНОМИЧЕСКИЙ КАПИТАЛ · ИССЛЕДОВАТЕЛЬСКИЙ СТЕНД
            </div>
            <h1>Оценка и факторный эксперимент</h1>
            <p>
              Сравните NPV портфеля в двух сценариях и проверьте влияние одного
              параметра сделки.
            </p>
          </div>
          <div className="intro-note">
            <span>RUB</span>
            <span>FIXED</span>
            <span>Дата оценки · 31.08.2022</span>
          </div>
        </section>
        <nav className="tabs" aria-label="Разделы">
          {[
            ["portfolio", "Портфель"],
            ["valuation", "Оценка"],
            ["experiment", "Эксперимент"],
          ].map(([id, label], i) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id as any)}
            >
              <span className="tabnum">0{i + 1}</span>
              {label}
              {id === "portfolio" && selected.length > 0 && (
                <em>{selected.length}</em>
              )}
            </button>
          ))}
        </nav>
        {error && (
          <ErrorBox>
            {error}
            {technicalError && (
              <details>
                <summary>Техническая информация</summary>
                <pre>{technicalError}</pre>
              </details>
            )}
          </ErrorBox>
        )}
        {tab === "portfolio" && (
          <>
            <div className="section-heading">
              <div>
                <span className="eyebrow">ШАГ 1</span>
                <h2>Соберите портфель</h2>
                <p>
                  До 10 сделок. В каталог загружено {deals.length || 55}{" "}
                  договоров.
                </p>
              </div>
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setCreateOpen((x) => !x)}
              >
                {createOpen ? "Закрыть форму" : "+ Создать свою сделку"}
              </button>
            </div>
            {createOpen && (
              <form className="create-card" onSubmit={createDeal} noValidate>
                <div className="form-title">
                  <div>
                    <h3>Новый учебный договор</h3>
                    <p>
                      Понятный шаблон с ежемесячной выплатой процентов и
                      ACT/365.
                    </p>
                  </div>
                  <span className="tag">RUB · FIXED</span>
                </div>
                <div className="form-grid">
                  {[
                    ["name", "Название", "text"],
                    ["current_principal_amt", "Остаток долга, ₽", "number"],
                    ["interest_rate", "Договорная ставка, % годовых", "number"],
                    ["maturity_dttm", "Дата окончания", "date"],
                  ].map(([key, label, type]) => (
                    <label className="field" key={key}>
                      {label}
                      <input
                        disabled={busy}
                        type={type}
                        value={(form as any)[key]}
                        onChange={(e) => {
                          setForm({ ...form, [key]: e.target.value });
                          setFormErrors((current) => ({
                            ...current,
                            [key]: "",
                          }));
                        }}
                        min={type === "number" ? 0 : undefined}
                      />
                      {formErrors[key] && (
                        <small className="field-error">{formErrors[key]}</small>
                      )}
                    </label>
                  ))}
                  <label className="field">
                    Схема
                    <select
                      disabled={busy}
                      value={form.discriminator}
                      onChange={(e) =>
                        setForm({ ...form, discriminator: e.target.value })
                      }
                    >
                      <option value="ANN">
                        Аннуитет — регулярный общий платёж
                      </option>
                      <option value="RGM">Равное погашение тела</option>
                      <option value="SIMPLE">Тело в конце срока</option>
                    </select>
                  </label>
                </div>
                <div className="form-foot">
                  <span>
                    Дата оценки 31.08.2022 · RUB · проценты ежемесячно · ACT/365
                  </span>
                  <button
                    className="button primary"
                    disabled={busy || selected.length >= 10}
                  >
                    {selected.length >= 10
                      ? "Удалите сделку, чтобы добавить новую"
                      : busy
                        ? "Сохраняем…"
                        : "Добавить сделку"}
                  </button>
                </div>
              </form>
            )}
            <div className="selected-deals" aria-label="Состав портфеля">
              <div className="selected-head">
                <b>Состав портфеля</b>
                <span>{selected.length}/10 сделок</span>
              </div>
              {selected.length ? (
                selected.map((id) => {
                  const d = deals.find((x) => x.id === id);
                  return (
                    d && (
                      <div className="selected-chip" key={id}>
                        <span>
                          <b>{d.name}</b>
                          <small>
                            {displayDealId(d.id)} ·{" "}
                            {money(d.current_principal_amt)} ₽ ·{" "}
                            {pct(d.interest_rate)}
                          </small>
                        </span>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() => {
                            const next = selected.filter((x) => x !== id);
                            setSelected(next);
                            if (target === id) setTarget(next[0] || "");
                            markStale();
                          }}
                        >
                          Удалить
                        </button>
                      </div>
                    )
                  );
                })
              ) : (
                <div className="empty small">
                  <p>
                    Выберите подходящие сделки ниже или создайте свою. Портфель
                    содержит от 1 до 10 договоров.
                  </p>
                </div>
              )}
            </div>
            <div className="card catalog">
              <div className="card-head">
                <div>
                  <h3>Доступные сделки</h3>
                  <span>
                    {eligible.length} подходят для обычного эксперимента ·{" "}
                    {deals.length - eligible.length} с ограничениями
                  </span>
                </div>
                <label className="available-toggle"><input type="checkbox" checked={showAvailableOnly} onChange={(e) => setShowAvailableOnly(e.target.checked)} /> Только доступные</label>
                <label className="sr-only" htmlFor="deal-search">
                  Поиск по названию или ID
                </label>
                <input
                  id="deal-search"
                  className="search"
                  placeholder="Поиск по названию или ID"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="table-scroll catalog-scroll">
                <table>
                  <thead>
                    <tr>
                      <th></th>
                      <th>Сделка</th>
                      <th>Остаток</th>
                      <th>Ставка</th>
                      <th>Срок</th>
                      <th>Схема</th>
                      <th>Доступность</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deals
                      .filter((d) => !showAvailableOnly || d.eligible)
                      .filter((d) =>
                        `${d.id} ${d.name}`
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((d) => (
                        <tr
                          key={d.id}
                          className={
                            selected.includes(d.id) ? "selected-row" : ""
                          }
                        >
                          <td>
                            <input
                              aria-label={`Выбрать ${d.name}`}
                              type="checkbox"
                              checked={selected.includes(d.id)}
                              disabled={
                                busy ||
                                !d.eligible ||
                                (!selected.includes(d.id) &&
                                  selected.length >= 10)
                              }
                              onChange={(e) => {
                                setSelected((s) =>
                                  e.target.checked
                                    ? [...s, d.id]
                                    : s.filter((x) => x !== d.id),
                                );
                                markStale();
                              }}
                            />
                          </td>
                          <td>
                            <b>{d.name}</b>
                            <small className="subline">
                              {displayDealId(d.id)}
                            </small>
                          </td>
                          <td>{money(d.current_principal_amt)} ₽</td>
                          <td>{pct(d.interest_rate)}</td>
                          <td>{d.maturity_dttm}</td>
                          <td>
                            {schemeLabel[d.discriminator] || d.discriminator}
                          </td>
                          <td>
                            {d.eligible ? (
                              <span className="pill ok">Доступна</span>
                            ) : (
                              <details className="restriction-details">
                                <summary>Ограничения</summary>
                                {d.restrictions.length ? (
                                  <ul>
                                    {d.restrictions.map((reason, index) => (
                                      <li key={index}>{reason}</li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p>
                                    Сделка не подходит для стандартного
                                    эксперимента.
                                  </p>
                                )}
                              </details>
                            )}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <div className="card-foot">
                <span>
                  Выбрано <b>{selected.length}/10</b>
                </span>
                <button
                  className="button primary"
                  disabled={!selected.length || busy}
                  onClick={calculate}
                >
                  {busy ? "Рассчитываем…" : "Рассчитать портфель"}{" "}
                  <span aria-hidden="true">→</span>
                </button>
              </div>
            </div>
          </>
        )}
        {tab === "valuation" && (
          <>
            <div className="section-heading">
              <div>
                <span className="eyebrow">ШАГ 2</span>
                <h2>Оценка портфеля</h2>
                <p>
                  NPV — сумма приведённых оставшихся поступлений; dNPV = NPV
                  STRESS − NPV BASE.
                </p>
              </div>
              <div className="curve-control">
                <label htmlFor="curve">Профиль кривых</label>
                <select
                  id="curve"
                  disabled={busy}
                  value={profile}
                  onChange={(e) => {
                    const next = e.target.value;
                    setError("");
                    setTechnicalError("");
                    setProfile(next);
                    if (experiment && !detail) setExperimentStale(true);
                    if (valuation) {
                      setStale(true);
                      setBusy(true);
                      setError("");
                      api<Valuation>("/valuations", {
                        method: "POST",
                        body: JSON.stringify({
                          deal_ids: selected,
                          profile_id: next,
                          act_act_as_365: actActAs365,
                        }),
                      })
                        .then((v) => {
                          setValuation(v);
                          setStale(false);
                        })
                        .catch((err) => reportError(err))
                        .finally(() => setBusy(false));
                    }
                  }}
                >
                  {curves.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {(c as any).synthetic ? " · учебный профиль" : ""}
                    </option>
                  ))}
                </select>
                <button
                  className="button primary"
                  disabled={busy || !selected.length}
                  onClick={calculate}
                >
                  {busy ? "Рассчитываем…" : "Пересчитать"}
                </button>
              </div>
            </div>
            <details className="details technical-settings">
              <summary>Технические настройки начисления</summary>
              <label className="setting-check">
                <input
                  type="checkbox"
                  checked={actActAs365}
                  disabled={busy}
                  onChange={(e) => {
                    setActActAs365(e.target.checked);
                    markStale();
                  }}
                />
                ACT/ACT как ACT/365 — совместимость с Excel
              </label>
              <p>
                Обычный режим сохраняет исходное соглашение day count каждой
                сделки. Изменение настройки помечает показанные результаты
                устаревшими; новый расчёт запускается кнопкой.
              </p>
            </details>
            {stale && (
              <div className="stale">
                <b>Условия изменились.</b> Показан сохранённый расчёт для
                прежнего состава портфеля или кривой. Пересчитайте портфель,
                чтобы обновить оценку.
              </div>
            )}
            {valuation ? (
              <>
                <div className="snapshot-line">
                  <span>
                    Снимок ·{" "}
                    {new Date(valuation.created_at).toLocaleString("ru-RU")} ·{" "}
                    {valuation.snapshot.length} сделок
                  </span>
                  <span>
                    Кривая: <b>{valuation.profile.name}</b>
                  </span>
                </div>
                <div className="metrics-grid">
                  <Metric
                    label="Базовая оценка · NPV BASE"
                    value={valuation.portfolio.base}
                    help="Сумма приведённых поступлений по BASE."
                  />
                  <Metric
                    label="Стрессовая оценка · NPV STRESS"
                    value={valuation.portfolio.stress}
                    help="Сумма приведённых поступлений по стрессовой кривой."
                  />
                  <Metric
                    label="dNPV · STRESS − BASE"
                    value={valuation.portfolio.dnpv}
                    help="Разница стрессовой и базовой оценок на одну дату."
                  />
                </div>
                <div className="card">
                      <div className="card-head">
                    <div>
                      <h3>Оценки по сделкам</h3>
                      <span>
                        Потоки остаются одинаковыми при смене кривой; меняются
                        DF.
                      </span>
                    </div>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Сделка</th>
                          <th>NPV BASE</th>
                          <th>NPV STRESS</th>
                          <th>dNPV</th>
                          <th>Платежи</th>
                        </tr>
                      </thead>
                      <tbody>
                    {valuation.deals.map((r) => {
                          const d = valuation.snapshot.find(
                            (x) => x.id === r.deal_id,
                          );
                          return (
                            <React.Fragment key={r.deal_id}>
                              <tr>
                                <td>
                                  <b>{d?.name || r.deal_id}</b>
                                  <small className="subline">
                                    {displayDealId(r.deal_id)}
                                  </small>
                                </td>
                                <td>{money(r.metrics.base)} ₽</td>
                                <td>{money(r.metrics.stress)} ₽</td>
                                <td>{money(r.metrics.dnpv)} ₽</td>
                                <td>
                                  <button
                                    className="text-button"
                                    onClick={() =>
                                      setExpanded(
                                        expanded === r.deal_id
                                          ? null
                                          : r.deal_id,
                                      )
                                    }
                                  >
                                    {expanded === r.deal_id
                                      ? "Скрыть"
                                      : "Открыть"}{" "}
                                    {r.flows.length} платежей
                                  </button>
                                </td>
                              </tr>
                              {expanded === r.deal_id && (
                                <tr>
                                  <td colSpan={5}>
                                    <FlowTable flows={r.flows} />
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className="card">
                  <div className="card-head"><div><h3>Вклад сделок в dNPV портфеля</h3><span>Вклад каждой сделки — её dNPV; сумма строк совпадает с портфельным dNPV.</span></div></div>
                  <div className="table-scroll"><table><thead><tr><th>Сделка</th><th>Вклад в dNPV</th><th>Доля от суммы абсолютных вкладов</th></tr></thead><tbody>
                    {(() => { const total = valuation.deals.reduce((s, x) => s + Math.abs(Number(x.metrics.dnpv)), 0); return <>{valuation.deals.map((r) => { const d = valuation.snapshot.find((x) => x.id === r.deal_id); return <tr key={r.deal_id}><td>{d?.name || r.deal_id}</td><td>{money(r.metrics.dnpv)} ₽</td><td>{total ? `${decimal(String(Math.abs(Number(r.metrics.dnpv)) / total * 100), 1)}%` : "—"}</td></tr>; })}<tr><th>Портфель</th><th>{money(valuation.portfolio.dnpv)} ₽</th><th>{total ? "100%" : "—"}</th></tr></>; })()}
                  </tbody></table></div>
                </div>
                <details className="details">
                  <summary>Профиль кривых и точки дисконтирования</summary>
                  <p className="hint">
                    Сохранённый режим ACT/ACT как ACT/365:{" "}
                    {valuation.act_act_as_365 ? "да" : "нет"}.{" "}
                    {valuation.profile.name}
                    {(valuation.profile as any).synthetic
                      ? " · синтетический учебный профиль"
                      : ""}
                    . Соглашение{" "}
                    {String(
                      (valuation.profile as any).compounding || "не указано",
                    )}
                    . Значения zero rate указаны в процентах годовых, DF —
                    коэффициенты дисконта на срок узла.
                    {(valuation.profile as any).flat_extrapolation_after_days &&
                      ` После ${(valuation.profile as any).flat_extrapolation_after_days} дней используется ставка последнего узла.`}
                  </p>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Сценарий</th>
                          <th>Срок, дней</th>
                          <th>Zero rate</th>
                          <th>DF на сроке узла</th>
                        </tr>
                      </thead>
                      <tbody>
                        {valuation.profile.nodes.map((n, i) => (
                          <tr key={i}>
                            <td>
                              {n.scenario === "BASE" ? "BASE" : n.scenario}
                            </td>
                            <td>{n.tenor_days}</td>
                            <td>{money(n.zero_rate_percent)}%</td>
                            <td>{decimal(n.df, 10)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
                <details className="details">
                  <summary>Проверки и версия движка</summary>
                  <p>{valuation.checks.join(" · ")}</p>
                  <small>
                    Версия {valuation.engine.version} · commit{" "}
                    {valuation.engine.commit} · SHA-256{" "}
                    {valuation.engine.sha256}
                  </small>
                </details>
                <button
                  className="button primary"
                  onClick={() => setTab("experiment")}
                >
                  Перейти к эксперименту →
                </button>
              </>
            ) : (
              <div className="empty">
                <div className="empty-icon">◎</div>
                <h3>Расчёт ещё не запущен</h3>
                <p>
                  Соберите портфель на первом шаге и рассчитайте его по
                  выбранной кривой.
                </p>
                <button
                  className="button secondary"
                  onClick={() => setTab("portfolio")}
                >
                  Выбрать сделки
                </button>
              </div>
            )}
          </>
        )}
        {tab === "experiment" && (
          <>
            <div className="section-heading">
              <div>
                <span className="eyebrow">ШАГ 3</span>
                <h2>Факторные эксперименты</h2>
                <p>
                  Варианты сравниваются с исходными сохранёнными условиями.
                  Остальные сделки и профиль фиксированы.
                </p>
              </div>
              <span className="tag">Не является полным ЭК</span>
            </div>
            <div className="mode-switch" role="group" aria-label="Режим эксперимента">
              <button className={experimentMode === "single" ? "active" : ""} onClick={() => { setExperimentMode("single"); setInteraction(null); }}>Один фактор</button>
              <button className={experimentMode === "two" ? "active" : ""} onClick={() => { setExperimentMode("two"); setExperiment(null); setDetail(null); }}>Два фактора</button>
              <button className={experimentMode === "discount" ? "active" : ""} onClick={() => { setExperimentMode("discount"); setInteraction(null); setDetail(null); setExperiment(null); setDiscountResult(null); setDiscountFactor("parallel_shift"); }}>Сдвиги кривой</button>
            </div>
            {experimentMode === "two" && <div className="card interaction-form">
              <div className="form-title"><div><h3>Сравнение четырёх состояний</h3><p>Исходные условия, изменение A, изменение B и оба изменения вместе. Учебные кривые ниже — демонстрационные.</p></div><button className="button secondary" disabled={busy} onClick={() => { setTarget("28"); setSelected(["28"]); markStale(); setProfile("repo-up100-v1"); setActActAs365(false); setFactorA("term"); setValueA("36"); setBaseValueA("12"); setFactorB("scheme"); setValueB("SIMPLE"); setBaseValueB("ANN"); setError(""); setInteraction(null); }}>Загрузить пример · портфель из ID 28</button></div>
              <label className="field">Сделка<select disabled={busy} value={target} onChange={e => { setTarget(e.target.value); setBaseValueA(""); setBaseValueB(""); setInteraction(null); }}>{selected.map(id => {const d=deals.find(x=>x.id===id); return d && <option key={id} value={id}>{d.name} · ID {id}</option>;})}</select></label>
              <div className="interaction-fields">
                {[{f:factorA,v:valueA,s:setFactorA,x:setValueA,b:baseValueA,z:setBaseValueA,label:"Фактор A"},{f:factorB,v:valueB,s:setFactorB,x:setValueB,b:baseValueB,z:setBaseValueB,label:"Фактор B"}].map((item,i) => <div className="interaction-field" key={i}><label className="field">{item.label}<select disabled={busy} value={pairFactors.includes(item.f) ? item.f : ""} onChange={e=>{item.s(e.target.value);item.z("");item.x("");setInteraction(null);}}><option value="" disabled>Выберите фактор</option>{pairFactors.map(f=><option key={f} value={f}>{factorNames[f] || f}</option>)}</select></label><label className="field">Исходное значение<input disabled={busy} placeholder="Из условий сделки" value={item.b} onChange={e=>{item.z(e.target.value);setInteraction(null);}}/></label><label className="field">Новое значение<input disabled={busy} value={item.v} onChange={e=>{item.x(e.target.value);setInteraction(null);}} placeholder={item.f === "scheme" ? "SIMPLE" : "например 36"}/></label></div>)}
              </div>
              <p className="hint">Кривые для расчёта: {activeCurve?.name}. Пустое исходное значение берётся из договора. В примере сравниваются 12 и 36 месяцев, аннуитет и возврат тела в конце срока.</p>
              <button className="button primary" disabled={busy || !pairFactors.includes(factorA) || !pairFactors.includes(factorB) || factorA === factorB || !valueA.trim() || !valueB.trim() || !selected.includes(target)} onClick={runInteraction}>{busy ? "Считаем четыре состояния…" : "Рассчитать взаимодействие →"}</button>
              {factorA === factorB && <small className="field-error">Выберите разные факторы.</small>}
              {interaction && <div className="interaction-result"><h3>Четыре оценки, ₽</h3><div className="interaction-states">{([["original","Исходные условия"],["a",`${factorNames[factorA]}: ${formatFactorValue(factorA,valueA)}`],["b",`${factorNames[factorB]}: ${formatFactorValue(factorB,valueB)}`],["both","Оба изменения"]] as const).map(([key,label])=><div key={key}><span>{label}</span><b>NPV BASE {money(interaction.states[key].portfolio.base)}</b><b>NPV STRESS {money(interaction.states[key].portfolio.stress)}</b><b>dNPV {money(interaction.states[key].portfolio.dnpv)}</b></div>)}</div><div className="interaction-effects"><Metric label="Эффект A · dNPV" value={interaction.effects.a.dnpv}/><Metric label="Эффект B · dNPV" value={interaction.effects.b.dnpv}/><Metric label="Вместе · dNPV" value={interaction.effects.both.dnpv}/><Metric label="Взаимодействие · dNPV" value={interaction.effects.interaction.dnpv}/></div><p className="hint">Взаимодействие = эффект совместного изменения минус сумма отдельных эффектов: EAB − EA − EB. Значение показывает разницу dNPV для этих условий и само по себе не говорит о причине.</p></div>}
            </div>}
            {experimentMode !== "two" && <div className="experiment-grid">
              <div className="card experiment-form">
                <div className="form-title">
                  <div>
                    <h3>Настройка серии</h3><p>Профиль для нового запуска: {activeCurve?.name}</p>
                    <p>
                      От 1 до 21 вариантов, каждый рассчитывается от исходного
                      состояния.
                    </p>
                  </div>
                </div>
                {experimentMode === "discount" ? <>
                  <label className="field">Изменение кривой<select value={discountFactor} disabled={busy} onChange={e=>{setDiscountFactor(e.target.value);setDiscountResult(null);setExperimentStale(true);}}>
                    <option value="parallel_shift">Параллельно обе кривые</option><option value="stress_shift">Только стрессовая кривая</option><option value="long_end_shift">Длинный конец обеих кривых</option>
                  </select></label>
                  <p className="hint">Фиксированная договорная ставка и платежи не меняются. {discountFactor === "parallel_shift" ? "BASE и STRESS сдвигаются на одинаковое число б.п. по всем срокам." : discountFactor === "stress_shift" ? "Сдвигается только STRESS; BASE остаётся прежней." : "Обе кривые плавно сдвигаются от 1 до 10 лет, затем сдвиг сохраняется."} Кривые демонстрационные: {activeCurve?.name}.</p>
                </> : <label className="field">
                  Сделка для изменения
                  <select
                    disabled={busy}
                    value={target}
                    onChange={(e) => {
                      setTarget(e.target.value);
                      if (experiment && !detail) setExperimentStale(true);
                    }}
                  >
                    {selected.map((id) => {
                      const d = deals.find((x) => x.id === id);
                      return (
                        d && (
                          <option key={id} value={id}>
                            {d.name} · {displayDealId(id)}
                          </option>
                        )
                      );
                    })}
                  </select>
                </label>}
                {experimentMode !== "discount" && <label className="field">
                  Фактор
                  <select
                    disabled={busy}
                    value={factor}
                    onChange={(e) => {
                      setFactor(e.target.value);
                      if (experiment && !detail) setExperimentStale(true);
                    }}
                  >
                    {(current?.available_factors || []).map((f) => <option key={f} value={f} disabled={f === "scheme" && !current?.scheme_editable}>{factorNames[f] || f}{f === "scheme" && !current?.scheme_editable ? " (недоступна)" : ""}</option>)}
                  </select>
                </label>}
                <label className="field">
                  {valuesLabel}
                  <textarea
                    disabled={busy}
                    rows={3}
                    value={experimentMode === "discount" ? discountValues : values}
                    onChange={(e) => {
                      if (experimentMode === "discount") setDiscountValues(e.target.value); else setValues(e.target.value);
                      setExperimentError("");
                      if (experiment && !detail) setExperimentStale(true);
                      if (discountResult) setExperimentStale(true);
                    }}
                    aria-describedby="values-help"
                  />
                  {experimentError && (
                    <small className="field-error" role="alert">
                      {experimentError}
                    </small>
                  )}
                  {experimentError && technicalError && (
                    <details className="field-technical">
                      <summary>Техническая информация</summary>
                      <pre>{technicalError}</pre>
                    </details>
                  )}
                  <small id="values-help">
                    {experimentMode === "discount" ? "Разделяйте значения точкой с запятой или пробелом. Допустимо от −500 до +500 базисных пунктов; 100 б.п. = 1 процентный пункт." : <>Разделяйте варианты точкой с запятой или пробелом. В дробных числах можно использовать запятую или точку.{" "}
                    {factor === "amount"
                      ? "Множитель 1.1 увеличивает сумму на 10%. Новый остаток округляется до копеек."
                      : factor === "rate"
                        ? "Введите процент, например 8.5 означает 8,5% годовых."
                        : factor === "term"
                          ? "Целое количество месяцев после даты оценки, от 1 до 360."
                          : factor === "scheme" ? "Варианты ANN, RGM, SIMPLE." : factor === "day_count" ? "Варианты ACT365 и ACTACT." : factor === "payment_frequency" ? "Варианты 1M, 3M и 6M." : factor === "accrued_interest" ? "Начисленные проценты в рублях; доступность зависит от сделки." : "Целые дни месяца от 1 до 31."}</>}
                  </small>
                </label>
                {experimentMode !== "discount" && <div className="preset-row">
                  <span>Примеры:</span>
                  {(factor === "amount"
                    ? ["0.8; 0.9; 1; 1.1; 1.2"]
                    : factor === "rate"
                      ? ["10; 11; 12; 13; 14"]
                      : factor === "term"
                        ? ["12; 24; 36; 60"]
                        : factor === "scheme" ? ["ANN; RGM; SIMPLE"] : factor === "day_count" ? ["ACT365; ACTACT"] : factor === "payment_frequency" ? ["1M; 3M; 6M"] : ["1; 15; 28"]
                  ).map((v, i) => (
                    <button
                      key={i}
                      disabled={busy}
                      onClick={() => {
                        setValues(v);
                        if (experiment && !detail) setExperimentStale(true);
                      }}
                    >
                      {i === 0 ? "Типовой набор" : `Набор ${i + 1}`}
                    </button>
                  ))}
                </div>}
                <button
                  className="button primary full"
                  disabled={busy || (experimentMode === "discount" ? !selected.length : !selected.includes(target))}
                  onClick={runExperiment}
                >
                  {busy ? "Расчёт серии…" : experimentMode === "discount" ? "Рассчитать сдвиги" : "Рассчитать варианты"}{" "}
                  <span>→</span>
                </button>
                {!selected.length && (
                  <p className="hint">Сначала добавьте сделки в портфель.</p>
                )}
              </div>
              <div className="card curve-card">
                <div className="card-head">
                  <div>
                    <h3>{shownExperiment ? "Кривые показанного результата" : "Профиль для нового запуска"}</h3>
                    <span>
                      {displayedCurve?.name || "Выберите портфель и профиль"}
                    </span>
                  </div>
                </div>
                {displayedCurve ? (
                  <ResponsiveContainer minWidth={0} width="100%" height={245}>
                    <LineChart data={curveChartData} margin={{top:12,right:30,bottom:12,left:10}}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        type="number"
                        dataKey="tenor_days"
                        domain={["dataMin", "dataMax"]}
                        tickFormatter={(x) => `${Math.round(x / 365)} г.`}
                      />
                      <YAxis tickFormatter={(x) => `${x}%`} />
                      <Tooltip position={{x:8,y:8}} wrapperStyle={{maxWidth:"calc(100% - 16px)",whiteSpace:"normal",pointerEvents:"none"}} contentStyle={{whiteSpace:"normal"}} allowEscapeViewBox={{ x: false, y: true }}
                        formatter={(v: any, n: any) => [
                          `${Number(v).toFixed(3)}%`,
                          n === "base" ? "BASE" : n,
                        ]}
                        labelFormatter={(x) => `Срок: ${x} дней`}
                      />
                      <Line
                        type="linear"
                        dataKey="base"
                        name="BASE"
                        stroke="#17816c"
                        strokeWidth={2.5}
                        dot={false}
                      />
                      <Legend />
                      <Line
                        type="linear"
                        dataKey="stress"
                        name="STRESS"
                        stroke="#9a6034"
                        strokeDasharray="5 4"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="empty small">Выберите профиль кривой.</div>
                )}
                <details>
                  <summary>Узлы BASE и STRESS</summary>
                  <div className="mini-nodes">
                    {displayedCurve?.nodes.map((n, i) => (
                      <span key={i}>
                        {n.scenario} · {n.tenor_days} д. · {n.zero_rate_percent}
                        % · DF {decimal(n.df, 10)}
                      </span>
                    ))}
                  </div>
                </details>
              </div>
            </div>}
            {experimentMode === "discount" && discountResult && <section className="discount-results">
              {experimentStale && <div className="stale"><b>Настройки изменились.</b> Ниже остаётся предыдущий расчёт; запустите его снова.</div>}
              <div className="results-title"><div><span className="eyebrow">ЭКСПЕРИМЕНТ ДИСКОНТИРОВАНИЯ</span><h2>{factorNames[discountFactor]} · {discountResult.original.profile.name}</h2><p>Учебные кривые BASE и STRESS. Договорные ставки FIXED и платежи неизменны. Исходные значения: NPV BASE {money(discountResult.original.portfolio.base)} ₽ · NPV STRESS {money(discountResult.original.portfolio.stress)} ₽ · dNPV {money(discountResult.original.portfolio.dnpv)} ₽.</p></div></div>
              <div className="card"><div className="card-head"><div><h3>Результат сдвига, ₽</h3><span>100 б.п. = 1 процентный пункт. Эффект = изменённое значение минус исходное.</span></div></div><div className="table-scroll"><table><thead><tr><th>Сдвиг</th><th>NPV BASE</th><th>NPV STRESS</th><th>dNPV</th><th>Эффект BASE</th><th>Эффект STRESS</th><th>Эффект dNPV</th><th>Платежи / DF</th></tr></thead><tbody>{discountResult.variants.map((v,i)=><React.Fragment key={i}><tr><td><b>{formatFactorValue(discountFactor,v.value)}</b></td><td>{money(v.valuation.portfolio.base)}</td><td>{money(v.valuation.portfolio.stress)}</td><td>{money(v.valuation.portfolio.dnpv)}</td><td>{money(v.effect.base)}</td><td>{money(v.effect.stress)}</td><td>{money(v.effect.dnpv)}</td><td><button className="text-button" onClick={()=>setDiscountExpanded(discountExpanded===i?null:i)}>{discountExpanded===i?"Скрыть":"Открыть"}</button></td></tr>{discountExpanded===i && <tr><td colSpan={8}><h4>Исходные потоки</h4>{discountResult.original.deals.map(d=><div key={d.deal_id}><h4>{discountResult.original.snapshot.find(s=>s.id===d.deal_id)?.name || `ID ${d.deal_id}`}</h4><FlowTable flows={d.flows}/></div>)}<h4>Потоки после изменения кривой · {formatFactorValue(discountFactor,v.value)}</h4>{v.valuation.deals.map(d=><div key={d.deal_id}><h4>{v.valuation.snapshot.find(s=>s.id===d.deal_id)?.name || `ID ${d.deal_id}`}</h4><FlowTable flows={d.flows}/></div>)}</td></tr>}</React.Fragment>)}</tbody></table></div></div>
            </section>}
            {(experiment || detail) && (
              <>
                {experimentStale && (
                  <div className="stale">
                    <b>Настройки эксперимента изменились.</b> Ниже показан
                    завершённый результат предыдущего запуска; запустите серию
                    снова, чтобы обновить его.
                  </div>
                )}
                <div className="results-title">
                  <div>
                    <span className="eyebrow">
                      {detail
                        ? "СОХРАНЁННЫЙ ЗАПУСК"
                        : "ЗАВЕРШЁННЫЙ ЭКСПЕРИМЕНТ"}
                    </span>
                    <h2>
                      {(detail || experiment)!.original.snapshot.find(
                        (d) => d.id === (detail || experiment)!.target_deal_id,
                      )?.name || "Сделка"}{" "}
                      · {factorNames[(detail || experiment)!.factor]}
                    </h2>
                    <p>
                      Исходные условия сохранены отдельно от каждого изменённого
                      варианта. Профиль:{" "}
                      <b>{(detail || experiment)!.original.profile.name}</b> ·
                      расчёт{" "}
                      {new Date(
                        (detail || experiment)!.original.created_at,
                      ).toLocaleString("ru-RU")}
                      .
                    </p>
                  </div>
                  <button
                    className="button secondary"
                    onClick={() => download((detail || experiment)!.id)}
                  >
                    ↓ Скачать CSV
                  </button>
                </div>
                <details className="details">
                  <summary>
                    Сохранённый профиль, режим и исходные условия
                  </summary>
                  <p>
                    Профиль:{" "}
                    <b>{(detail || experiment)!.original.profile.name}</b> ·{" "}
                    {((detail || experiment)!.original.profile as any).synthetic
                      ? "синтетический учебный профиль"
                      : "профиль кривой"}{" "}
                    · соглашение:{" "}
                    {String(
                      (detail || experiment)!.original.profile.compounding ||
                        "не указано",
                    )}{" "}
                    · day count:{" "}
                    {(detail || experiment)!.original.snapshot.find(
                      (d) => d.id === (detail || experiment)!.target_deal_id,
                    )?.day_convention_cd || "по исходным данным"}{" "}
                    · ACT/ACT как ACT/365:{" "}
                    {(detail || experiment)!.original.act_act_as_365
                      ? "да"
                      : "нет"}
                  </p>
                  <p>
                    {snapshotFields(
                      (detail || experiment)!.original.snapshot.find(
                        (d) => d.id === (detail || experiment)!.target_deal_id,
                      ),
                      (detail || experiment)!.factor,
                    )}
                  </p>
                  <ul>
                    {(detail || experiment)!.original.snapshot.map((d) => (
                      <li key={d.id}>
                        {d.name}:{" "}
                        {snapshotFields(d, (detail || experiment)!.factor)} ·{" "}
                        {d.day_convention_cd}
                      </li>
                    ))}
                  </ul>
                  <p>
                    Движок {(detail || experiment)!.original.engine.version} ·
                    commit {(detail || experiment)!.original.engine.commit} ·
                    SHA-256 {(detail || experiment)!.original.engine.sha256}
                  </p>
                  <p>
                    {((detail || experiment)!.original.checks || []).join(
                      " · ",
                    )}
                  </p>
                </details>
                <div className="effect-grid">
                  <Metric
                    label="Эффект dNPV портфеля · диапазон"
                    value={range(
                      (detail || experiment)!.variants.map((v) =>
                        Number(v.effect.dnpv),
                      ),
                    )}
                  />
                  <Metric
                    label="Исходный dNPV портфеля"
                    value={(detail || experiment)!.original.portfolio.dnpv}
                  />
                  <Metric
                    label="Изменяемая сделка"
                    value={
                      (detail || experiment)!.original.snapshot.find(
                        (d) => d.id === (detail || experiment)!.target_deal_id,
                      )?.name || "Сделка"
                    }
                  />
                </div>
                <div className="card chart-card">
                  <div className="card-head">
                    <div>
                      <h3>Результаты вариантов</h3>
                      <span>
                        Все варианты построены от исходных условий, без
                        накопления изменений.
                      </span>
                    </div>
                    <label className="field inline">
                      Метрика
                      <select
                        value={metric}
                        onChange={(e) => setMetric(e.target.value)}
                      >
                        <option value="base">Эффект NPV BASE</option>
                        <option value="stress">Эффект NPV STRESS</option>
                        <option value="dnpv">Эффект dNPV</option>
                      </select>
                    </label>
                  </div>
                  <ResponsiveContainer minWidth={0} width="100%" height={280}>
                    <AreaChart data={experimentChartData} margin={{top:16,right:38,bottom:24,left:8}}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        type={experimentIsNumeric ? "number" : "category"}
                        dataKey={experimentIsNumeric ? "x" : "label"}
                        domain={
                          experimentIsNumeric ? experimentDomain : undefined
                        }
                        tickFormatter={(x) =>
                          chartFactorTick(shownExperiment!.factor, x)
                        }
                      />
                      <YAxis width={92} tickFormatter={(x) => { const n=Number(x); return n === 0 ? "0" : `${n<0 ? "−" : ""}${money(Math.abs(n)/1000)} тыс.`; }} /><ReferenceLine y={0} stroke="#475c54" strokeWidth={1.5} />
                      <Tooltip position={{x:8,y:8}} wrapperStyle={{maxWidth:"calc(100% - 16px)",whiteSpace:"normal",pointerEvents:"none"}} contentStyle={{whiteSpace:"normal"}} allowEscapeViewBox={{ x: false, y: true }}
                        formatter={(v: any) => `${money(v)} ₽`}
                        labelFormatter={(_, payload) =>
                          `Фактор: ${payload?.[0]?.payload?.label ?? _}`
                        }
                      />
                      <Area
                        type="linear"
                        dot={
                          shownExperiment!.variants.length === 1
                            ? { r: 4, fill: "#17816c" }
                            : false
                        }
                        dataKey={metric}
                        name={
                          metric === "base"
                            ? "Эффект NPV BASE"
                            : metric === "stress"
                              ? "Эффект NPV STRESS"
                              : "Эффект dNPV"
                        }
                        stroke="#17816c"
                        fill="#d7eee8"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                  <div className="legend"><p>Ось Y — изменение относительно исходного портфеля, ₽. Ноль — отсутствие изменения.</p><p>Ось X — {{amount:"множитель остатка",rate:"% годовых",term:"месяцы от даты оценки",scheme:"схема"}[shownExperiment!.factor]}</p>
                    Эффект портфеля · BASE1−BASE0 / STRESS1−STRESS0 /
                    dNPV1−dNPV0
                  </div>
                </div>
                <div className="card"><div className="card-head"><div><h3>Результат эксперимента, ₽</h3><p>Исходный портфель: NPV BASE {money(shownExperiment!.original.portfolio.base)} · dNPV {money(shownExperiment!.original.portfolio.dnpv)}</p></div></div><div className="table-scroll"><table><thead><tr><th>Фактор</th><th>NPV BASE после</th><th>dNPV после</th><th>Эффект dNPV</th></tr></thead><tbody>{shownExperiment!.variants.map((v,i)=><tr key={i}><td>{formatFactorValue(shownExperiment!.factor,v.value)}</td><td>{money(v.valuation.portfolio.base)}</td><td>{money(v.valuation.portfolio.dnpv)}</td><td>{money(v.effect.dnpv)}</td></tr>)}</tbody></table></div></div>
                <details className="details"><summary>Полная проверочная таблица и потоки</summary>
                <div className="card">
                  <div className="card-head">
                    <div>
                      <h3>Четыре оценки и эффект</h3>
                      <span>
                        Исходное состояние и вариант 1 оцениваются по обоим
                        сценариям. Проценты — к исходной величине портфеля.
                      </span>
                    </div>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Значение фактора</th>
                          <th>BASE 0</th>
                          <th>STRESS 0</th>
                          <th>BASE 1</th>
                          <th>STRESS 1</th>
                          <th>Δ NPV BASE</th>
                          <th>Δ NPV STRESS</th>
                          <th>Δ dNPV</th>
                          <th>Потоки</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(detail || experiment)!.variants.map((v, i) => (
                          <React.Fragment key={i}>
                            <tr>
                              <td>
                                <b>
                                  {formatFactorValue(
                                    (detail || experiment)!.factor,
                                    v.value,
                                  )}
                                </b>
                              </td>
                              <td>
                                {money(
                                  (detail || experiment)!.original.portfolio
                                    .base,
                                )}
                              </td>
                              <td>
                                {money(
                                  (detail || experiment)!.original.portfolio
                                    .stress,
                                )}
                              </td>
                              <td>{money(v.valuation.portfolio.base)}</td>
                              <td>{money(v.valuation.portfolio.stress)}</td>
                              <td>{money(v.effect.base)}</td>
                              <td>{money(v.effect.stress)}</td>
                              <td>{money(v.effect.dnpv)}</td>
                              <td>
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    setExpanded(
                                      expanded === `variant-${i}`
                                        ? null
                                        : `variant-${i}`,
                                    )
                                  }
                                >
                                  {expanded === `variant-${i}`
                                    ? "Скрыть потоки"
                                    : "Открыть потоки"}
                                </button>
                              </td>
                            </tr>
                            {expanded === `variant-${i}` && (
                              <tr>
                                <td colSpan={9}>
                                  <h4>
                                    Исходные условия ·{" "}
                                    {
                                      (detail ||
                                        experiment)!.original.snapshot.find(
                                        (d) =>
                                          d.id ===
                                          (detail || experiment)!
                                            .target_deal_id,
                                      )?.name
                                    }
                                  </h4>
                                  <p className="hint">
                                    {snapshotFields(
                                      (detail ||
                                        experiment)!.original.snapshot.find(
                                        (d) =>
                                          d.id ===
                                          (detail || experiment)!
                                            .target_deal_id,
                                      ),
                                      (detail || experiment)!.factor,
                                    )}
                                  </p>
                                  <FlowTable
                                    flows={
                                      (detail ||
                                        experiment)!.original.deals.find(
                                        (x) =>
                                          x.deal_id ===
                                          (detail || experiment)!
                                            .target_deal_id,
                                      )?.flows || []
                                    }
                                  />
                                  <h4>
                                    Изменённые условия ·{" "}
                                    {formatFactorValue(
                                      (detail || experiment)!.factor,
                                      v.value,
                                    )}
                                  </h4>
                                  <p className="hint">
                                    {snapshotFields(
                                      v.valuation.snapshot.find(
                                        (d) =>
                                          d.id ===
                                          (detail || experiment)!
                                            .target_deal_id,
                                      ),
                                      (detail || experiment)!.factor,
                                    )}
                                  </p>
                                  <FlowTable
                                    flows={
                                      v.valuation.deals.find(
                                        (x) =>
                                          x.deal_id ===
                                          (detail || experiment)!
                                            .target_deal_id,
                                      )?.flows || []
                                    }
                                  />
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <h4 className="table-section-title">
                    Целевая сделка · четыре оценки и эффект
                  </h4>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Значение фактора</th>
                          <th>BASE 0</th>
                          <th>STRESS 0</th>
                          <th>BASE 1</th>
                          <th>STRESS 1</th>
                          <th>Δ NPV BASE</th>
                          <th>Δ NPV STRESS</th>
                          <th>Δ dNPV</th>
                          <th>BASE, % портфеля</th>
                          <th>STRESS, % портфеля</th>
                          <th>dNPV, % портфеля</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(detail || experiment)!.variants.map((v, i) => (
                          <tr key={i}>
                            <td>
                              <b>
                                {formatFactorValue(
                                  (detail || experiment)!.factor,
                                  v.value,
                                )}
                              </b>
                            </td>
                            <td>
                              {money(
                                (detail || experiment)!.original.deals.find(
                                  (x) =>
                                    x.deal_id ===
                                    (detail || experiment)!.target_deal_id,
                                )?.metrics.base || "0",
                              )}
                            </td>
                            <td>
                              {money(
                                (detail || experiment)!.original.deals.find(
                                  (x) =>
                                    x.deal_id ===
                                    (detail || experiment)!.target_deal_id,
                                )?.metrics.stress || "0",
                              )}
                            </td>
                            <td>
                              {money(
                                v.valuation.deals.find(
                                  (x) =>
                                    x.deal_id ===
                                    (detail || experiment)!.target_deal_id,
                                )?.metrics.base || "0",
                              )}
                            </td>
                            <td>
                              {money(
                                v.valuation.deals.find(
                                  (x) =>
                                    x.deal_id ===
                                    (detail || experiment)!.target_deal_id,
                                )?.metrics.stress || "0",
                              )}
                            </td>
                            <td>{money(v.target_effect.base)}</td>
                            <td>{money(v.target_effect.stress)}</td>
                            <td>{money(v.target_effect.dnpv)}</td>
                            <td>
                              {v.percent_effect.base === null
                                ? "—"
                                : `${money(v.percent_effect.base)}%`}
                            </td>
                            <td>
                              {v.percent_effect.stress === null
                                ? "—"
                                : `${money(v.percent_effect.stress)}%`}
                            </td>
                            <td>
                              {v.percent_effect.dnpv === null
                                ? "—"
                                : `${money(v.percent_effect.dnpv)}%`}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div></details>
              </>
            )}
            {experimentMode !== "discount" && <div className="history">
              <div className="section-heading compact">
                <div>
                  <span className="eyebrow">АРХИВ</span>
                  <h2>История запусков</h2>
                </div>
              </div>
              {history.length ? (
                <div className="history-list">
                  {history.map((h) => (
                    <div className="history-item" key={h.id}>
                      <button disabled={busy} onClick={() => openHistory(h.id)}>
                        <b>{h.target_name}</b>
                        <span>
                          {factorNames[h.factor] || h.factor} ·{" "}
                          {h.values.length} вариантов · {h.profile_name}
                        </span>
                        <small>
                          {new Date(h.created_at).toLocaleString("ru-RU")}
                        </small>
                      </button>
                      <button
                        className="icon-button"
                        aria-label="Скачать CSV"
                        onClick={() => download(h.id)}
                      >
                        ↓
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty small">
                  <h3>Сохранённых запусков пока нет</h3>
                  <p>
                    Завершённые серии появятся здесь. Их входы и результаты
                    останутся неизменными.
                  </p>
                </div>
              )}
            </div>}
          </>
        )}
        <footer>
          <span>Учебная оценка на дату 31.08.2022 · RUB</span>
          <span>
            Расчёт выполняет cfcalc · объяснение результатов — вручную
          </span>
        </footer>
      </main>
    </div>
  );
}
function chartFactorTick(factor: string, value: string | number) {
  if (factor === "amount") return `${decimal(String(value), 3)}×`;
  if (factor === "rate") return `${decimal(String(value), 3)}%`;
  if (factor === "term") return `${value} мес.`;
  return String(value);
}
function snapshotFields(d: Deal | undefined, factor: string) {
  if (!d) return "Снимок сделки недоступен.";
  return `Остаток ${money(d.current_principal_amt)} ₽ · ставка ${fractionToPercent(d.interest_rate)}% · срок до ${d.maturity_dttm} · схема ${schemeLabel[d.discriminator] || d.discriminator} · ${factorNames[factor] || factor}`;
}
function range(a: number[]) {
  return a.length
    ? `${money(Math.min(...a))} … ${money(Math.max(...a))} ₽`
    : "—";
}
