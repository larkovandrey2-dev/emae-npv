import * as React from "react";
import { money } from "./api";
import type { Flow } from "./api";

export function ErrorBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="error" role="alert">
      {children}
    </div>
  );
}
export function Metric({
  label,
  value,
  help,
}: {
  label: string;
  value: string;
  help?: string;
}) {
  return (
    <div className="metric">
      <span>
        {label}
        <span className="help" title={help}>
          ⓘ
        </span>
      </span>
      <strong>
        {Number.isFinite(Number(value)) ? `${money(value)} ₽` : value}
      </strong>
    </div>
  );
}
function decimal(value: string, digits = 2) {
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
  }).format(Number(value));
}

export function FlowTable({ flows }: { flows: Flow[] }) {
  return (
    <>
      <p className="flow-units">
        Суммы и приведённые значения — ₽ · DF — безразмерный коэффициент
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {[
                "Дата",
                "Погашение тела",
                "Проценты",
                "Платёж",
                "Остаток",
                "DF BASE",
                "DF STRESS",
                "PV BASE",
                "PV STRESS",
              ].map((x) => (
                <th key={x}>{x}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {flows.map((f, i) => (
              <tr key={i}>
                {[
                  f.date,
                  f.principal,
                  f.interest,
                  f.total,
                  f.remaining_principal,
                  f.df_base,
                  f.df_stress,
                  f.pv_base,
                  f.pv_stress,
                ].map((v, j) => (
                  <td key={j}>
                    {j === 0
                      ? v
                      : j === 5 || j === 6
                        ? decimal(v, 10)
                        : money(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
