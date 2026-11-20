export type Scheme = "ANN" | "RGM" | "SIMPLE";
export type Deal = {
  id: string;
  name: string;
  current_principal_amt: string;
  interest_rate: string;
  maturity_dttm: string;
  discriminator: Scheme;
  report_dttm: string;
  currency_id: string;
  day_convention_cd: string;
  eligible: boolean;
  restrictions: string[];
  scheme_editable: boolean;
  available_factors: string[];
  day_count?: "ACT365" | "ACTACT";
  payment_day?: string;
  payment_frequency?: "1M" | "3M" | "6M";
  fields: Record<string, string>;
};
export type Node = {
  scenario: "BASE" | "STRESS_UP_200BP" | "STRESS_UP_100BP";
  tenor_days: number;
  zero_rate_percent: string;
  df: string;
};
export type CurveProfile = {
  id: string;
  name: string;
  [key: string]: unknown;
  nodes: Node[];
};
export type Metrics = { base: string; stress: string; dnpv: string };
export type Flow = {
  date: string;
  principal: string;
  interest: string;
  total: string;
  remaining_principal: string;
  df_base: string;
  df_stress: string;
  pv_base: string;
  pv_stress: string;
};
export type DealResult = { deal_id: string; metrics: Metrics; flows: Flow[] };
export type Valuation = {
  id: string;
  created_at: string;
  profile: CurveProfile;
  act_act_as_365: boolean;
  snapshot: Deal[];
  portfolio: Metrics;
  deals: DealResult[];
  engine: { commit: string; sha256: string; version: string };
  checks: string[];
};
export type Variant = {
  value: string;
  valuation: Valuation;
  effect: Metrics;
  target_effect: Metrics;
  percent_effect: {
    base: string | null;
    stress: string | null;
    dnpv: string | null;
  };
};
export type Experiment = {
  id: string;
  created_at: string;
  status: "complete";
  target_deal_id: string;
  factor: string;
  values: string[];
  original: Valuation;
  variants: Variant[];
};
export type DiscountExperiment = {
  original: Valuation;
  variants: { value: string; valuation: Valuation; effect: Metrics }[];
};
export type HistoryItem = {
  id: string;
  created_at: string;
  status: string;
  target_deal_id: string;
  target_name: string;
  factor: string;
  values: string[];
  profile_name: string;
};
export type InteractionResult = {
  states: { original: Valuation; a: Valuation; b: Valuation; both: Valuation };
  effects: { a: Metrics; b: Metrics; both: Metrics; interaction: Metrics };
};
const fieldNames: Record<string, string> = {
  current_principal_amt: "Остаток долга",
  interest_rate: "Договорная ставка",
  maturity_dttm: "Дата окончания",
  discriminator: "Схема",
  name: "Название",
  deal_ids: "Портфель",
  profile_id: "Профиль кривых",
  target_deal_id: "Сделка",
  factor: "Фактор",
  values: "Значения",
  factor_a: "Фактор A",
  factor_b: "Фактор B",
  value_a: "Новое значение фактора A",
  value_b: "Новое значение фактора B",
  base_value_a: "Исходное значение фактора A",
  base_value_b: "Исходное значение фактора B",
};

export class ApiError extends Error {
  fieldErrors: Record<string, string>;
  technical?: string;
  constructor(
    message: string,
    fieldErrors: Record<string, string> = {},
    technical?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.fieldErrors = fieldErrors;
    this.technical = technical;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch("/api" + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!r.ok) {
    let message = `Ошибка запроса (${r.status})`;
    const fieldErrors: Record<string, string> = {};
    let technical: string | undefined;
    try {
      const x = await r.json();
      if (Array.isArray(x.detail)) {
        for (const item of x.detail) {
          const parts = Array.isArray(item.loc) ? item.loc.map(String) : [];
          const loc =
            [...parts].reverse().find((part) => part in fieldNames) ||
            parts.at(-1) ||
            "";
          const label = fieldNames[loc] || loc;
          if (loc) fieldErrors[loc] = item.msg;
          const entry = label ? `${label}: ${item.msg}` : item.msg;
          message =
            message === `Ошибка запроса (${r.status})`
              ? entry
              : `${message}. ${entry}`;
        }
      } else if (typeof x.detail === "string") message = x.detail;
      if (typeof x.technical === "string") technical = x.technical;
    } catch {}
    throw new ApiError(message, fieldErrors, technical);
  }
  return r.json();
}
export const money = (v: string | number) => {
  const numeric = Number(v);
  const display = Math.round(numeric * 100) === 0 ? 0 : numeric;
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(
    display,
  );
};
export const pct = (v: string | number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(
    Number(v) * 100,
  ) + "%";
