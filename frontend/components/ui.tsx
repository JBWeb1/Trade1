import Link from "next/link";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-dim">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, children, right, className = "" }: { title?: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-lg border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-medium text-dim">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-faint">{label}</div>
      <div className={`tabular mt-1 text-lg font-semibold ${tone ?? ""}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-dim">{hint}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  good: "border-pos/40 bg-pos/10 text-pos",
  bad: "border-neg/40 bg-neg/10 text-neg",
  warn: "border-warn/40 bg-warn/10 text-warn",
  neutral: "border-line bg-panel-2 text-dim",
  info: "border-accent/40 bg-accent/10 text-accent",
};

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof BADGE; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${BADGE[tone]}`}>{children}</span>;
}

export function statusTone(status: string | null | undefined): keyof typeof BADGE {
  switch (status) {
    case "ok": case "completed": case "PASSED": return "good";
    case "failed": case "FAILED": return "bad";
    case "warning": case "INCONCLUSIVE": case "running": return "warn";
    default: return "neutral";
  }
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="mx-auto mt-2 max-w-xl text-sm text-dim">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error, children }: { error: string; children?: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-neg/40 bg-neg/10 px-4 py-3 text-sm">
      <div className="font-medium text-neg">Could not load data</div>
      <div className="mt-1 text-fg">{error}</div>
      {children}
    </div>
  );
}

export function Table({ head, children, dense = false }: { head: ReactNode[]; children: ReactNode; dense?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="tabular w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-faint">
          <tr>{head.map((h, i) => <th key={i} className="whitespace-nowrap border-b border-line px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className={dense ? "[&_td]:py-1" : "[&_td]:py-2"}>{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`whitespace-nowrap border-b border-line/60 px-3 ${className}`}>{children}</td>;
}

export function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className="text-accent hover:underline">{children}</Link>;
}

export function Kv({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(140px,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-dim">{k}</dt>
          <dd className="tabular break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SimulationLabel() {
  return (
    <div className="mb-4 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
      HISTORICAL BACKTEST — bar-based simulation on MT5 bid bars. Not tick-accurate, not demo or live results,
      and not a forecast of future performance.
    </div>
  );
}
