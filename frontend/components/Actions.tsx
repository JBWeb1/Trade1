"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { proxyPost } from "@/lib/client";

const input = "w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm focus:border-accent focus:outline-none";
const button = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50";
export const secondaryButton = "rounded-md border border-line bg-panel-2 px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50";

const TIMEFRAMES = ["M1", "M15", "H1", "H4"];

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs text-dim">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

function Message({ error, ok }: { error?: string | null; ok?: ReactNode }) {
  if (error) return <p role="alert" className="mt-3 text-sm text-neg">{error}</p>;
  if (ok) return <p role="status" className="mt-3 text-sm text-pos">{ok}</p>;
  return null;
}

/** Button that POSTs to an allowed proxy route, then refreshes the page. */
/** `navigatePrefix`: after success, go to `${navigatePrefix}${result.id}` instead of refreshing. */
export function ActionButton({ path, label, busyLabel, body, confirmText, navigatePrefix }: {
  path: string; label: string; busyLabel?: string; body?: unknown; confirmText?: string; navigatePrefix?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div>
      <button className={secondaryButton} disabled={busy} onClick={async () => {
        if (confirmText && !window.confirm(confirmText)) return;
        setBusy(true); setError(null);
        try {
          const res = await proxyPost<Record<string, unknown>>(path, body);
          if (navigatePrefix) router.push(`${navigatePrefix}${String(res.id)}`); else router.refresh();
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        } finally {
          setBusy(false);
        }
      }}>{busy ? busyLabel ?? "Working…" : label}</button>
      {error && <p role="alert" className="mt-2 max-w-md text-xs text-neg">{error}</p>}
    </div>
  );
}

export function IngestForm({ symbols }: { symbols: string[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setError(null); setOk(null);
    try {
      const r = await proxyPost<{ status: string; bars_inserted: number; bars_already_present: number; error: string | null }>(
        "market-data/ingest", {
          symbol: f.get("symbol"), timeframe: f.get("timeframe"),
          start: `${f.get("start")}T00:00:00Z`, end: `${f.get("end")}T00:00:00Z`,
        });
      if (r.status === "failed") setError(r.error ?? "download failed - see the run list");
      else setOk(`${r.status.toUpperCase()}: ${r.bars_inserted} new bars, ${r.bars_already_present} already stored`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-5 sm:items-end">
      <Field label="Symbol">
        <input name="symbol" list="symbols" required maxLength={64} className={input} placeholder="XAUUSDm" />
        <datalist id="symbols">{symbols.map((s) => <option key={s} value={s} />)}</datalist>
      </Field>
      <Field label="Timeframe">
        <select name="timeframe" className={input} defaultValue="H1">{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select>
      </Field>
      <Field label="Start (UTC)"><input name="start" type="date" required className={input} defaultValue="2024-01-01" /></Field>
      <Field label="End (UTC)"><input name="end" type="date" required className={input} defaultValue={today} /></Field>
      <button className={button} disabled={busy}>{busy ? "Downloading…" : "Download history"}</button>
      <div className="sm:col-span-5"><Message error={error} ok={ok} /></div>
    </form>
  );
}

export function BacktestForm({ symbols, strategies }: { symbols: string[]; strategies: string[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    let params: Record<string, unknown> = {};
    const raw = String(f.get("params") ?? "").trim();
    if (raw) {
      try { params = JSON.parse(raw); } catch { setError("Parameters must be valid JSON, e.g. {\"fast_ema\": 20}"); return; }
    }
    setBusy(true); setError(null);
    try {
      const r = await proxyPost<{ id: number; status: string; error: string | null }>("backtests", {
        symbol: f.get("symbol"), timeframe: f.get("timeframe"), htf: f.get("htf") || null,
        start: `${f.get("start")}T00:00:00Z`, end: `${f.get("end")}T00:00:00Z`,
        strategy: f.get("strategy"), params,
        config: {
          starting_balance: Number(f.get("balance")), leverage: Number(f.get("leverage")),
          costs: { commission_per_lot_per_side: Number(f.get("commission")), slippage_points: Number(f.get("slippage")),
            min_spread_points: Number(f.get("min_spread")) },
          risk: { risk_per_trade_pct: Number(f.get("risk")), max_daily_loss_pct: Number(f.get("daily_loss")) },
        },
      });
      router.push(`/backtests/${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-4">
      <Field label="Symbol">
        <input name="symbol" list="bt-symbols" required maxLength={64} className={input} placeholder="XAUUSDm" />
        <datalist id="bt-symbols">{symbols.map((s) => <option key={s} value={s} />)}</datalist>
      </Field>
      <Field label="Strategy">
        <select name="strategy" className={input}>{strategies.map((s) => <option key={s}>{s}</option>)}</select>
      </Field>
      <Field label="Timeframe">
        <select name="timeframe" className={input} defaultValue="H1">{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select>
      </Field>
      <Field label="Higher timeframe filter" hint="trend_continuation needs one">
        <select name="htf" className={input} defaultValue="H4"><option value="">none</option>{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select>
      </Field>
      <Field label="Start (UTC)"><input name="start" type="date" required className={input} defaultValue="2024-01-01" /></Field>
      <Field label="End (UTC)"><input name="end" type="date" required className={input} defaultValue={today} /></Field>
      <Field label="Starting balance"><input name="balance" type="number" min="1" step="any" required className={input} defaultValue={10000} /></Field>
      <Field label="Leverage"><input name="leverage" type="number" min="1" max="5000" required className={input} defaultValue={100} /></Field>
      <Field label="Risk per trade %" hint="engineering default 0.25"><input name="risk" type="number" min="0.01" max="5" step="0.01" required className={input} defaultValue={0.25} /></Field>
      <Field label="Daily loss limit %"><input name="daily_loss" type="number" min="0.1" max="100" step="0.1" required className={input} defaultValue={1} /></Field>
      <Field label="Commission / lot / side" hint="0 for Exness Standard"><input name="commission" type="number" min="0" step="any" required className={input} defaultValue={0} /></Field>
      <Field label="Slippage (points)"><input name="slippage" type="number" min="0" step="any" required className={input} defaultValue={0} /></Field>
      <Field label="Minimum spread (points)" hint="floor under recorded bar spreads"><input name="min_spread" type="number" min="0" step="any" required className={input} defaultValue={0} /></Field>
      <div className="sm:col-span-3">
        <Field label="Strategy parameters (JSON, optional)">
          <input name="params" className={`${input} font-mono`} placeholder='{"fast_ema": 20, "regime_filter": "trending"}' />
        </Field>
      </div>
      <div className="sm:col-span-4 flex items-center gap-3">
        <button className={button} disabled={busy}>{busy ? "Running backtest…" : "Run backtest"}</button>
        <span className="text-xs text-faint">Uses stored history only. Download it on the Historical data page first.</span>
      </div>
      <div className="sm:col-span-4"><Message error={error} /></div>
    </form>
  );
}

export function MultiMarketForm({ symbols, strategies }: { symbols: string[]; strategies: string[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const picked = f.getAll("symbols").map(String);
    if (picked.length < 2) { setError("Pick at least two symbols."); return; }
    setBusy(true); setError(null);
    try {
      const r = await proxyPost<{ id: number }>("research/multi-market", {
        symbols: picked, timeframe: f.get("timeframe"), htf: f.get("htf") || null, strategy: f.get("strategy"),
        start: `${f.get("start")}T00:00:00Z`, end: `${f.get("end")}T00:00:00Z`,
        config: { costs: { commission_per_lot_per_side: Number(f.get("commission")) } },
      });
      router.push(`/research/${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-4">
      <div className="sm:col-span-4">
        <span className="mb-1 block text-xs text-dim">Symbols (with stored history)</span>
        <div className="flex flex-wrap gap-2">
          {symbols.length === 0 && <span className="text-sm text-faint">No stored history yet.</span>}
          {symbols.map((s) => (
            <label key={s} className="flex items-center gap-1.5 rounded border border-line px-2 py-1 text-sm">
              <input type="checkbox" name="symbols" value={s} /> {s}
            </label>
          ))}
        </div>
      </div>
      <Field label="Strategy"><select name="strategy" className={input}>{strategies.map((s) => <option key={s}>{s}</option>)}</select></Field>
      <Field label="Timeframe"><select name="timeframe" className={input} defaultValue="H1">{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select></Field>
      <Field label="Higher timeframe"><select name="htf" className={input} defaultValue="H4"><option value="">none</option>{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}</select></Field>
      <Field label="Commission / lot / side"><input name="commission" type="number" min="0" step="any" defaultValue={0} className={input} /></Field>
      <Field label="Start (UTC)"><input name="start" type="date" required className={input} defaultValue="2024-01-01" /></Field>
      <Field label="End (UTC)"><input name="end" type="date" required className={input} defaultValue={today} /></Field>
      <div className="flex items-end sm:col-span-2"><button className={button} disabled={busy}>{busy ? "Running on every symbol…" : "Run multi-market test"}</button></div>
      <div className="sm:col-span-4"><Message error={error} /></div>
    </form>
  );
}

type Leg = { symbol: string; timeframe: string; htf: string; strategy: string };

export function PortfolioForm({ symbols, strategies }: { symbols: string[]; strategies: string[] }) {
  const router = useRouter();
  const [legs, setLegs] = useState<Leg[]>([{ symbol: symbols[0] ?? "", timeframe: "H1", htf: "", strategy: strategies[0] ?? "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);
  const update = (k: number, patch: Partial<Leg>) => setLegs(legs.map((l, j) => (j === k ? { ...l, ...patch } : l)));

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setError(null);
    try {
      const r = await proxyPost<{ id: number }>("research/portfolio", {
        legs: legs.map((l) => ({ symbol: l.symbol, timeframe: l.timeframe, htf: l.htf || null, strategy: l.strategy })),
        start: `${f.get("start")}T00:00:00Z`, end: `${f.get("end")}T00:00:00Z`,
        config: { starting_balance: Number(f.get("balance")), risk: { risk_per_trade_pct: Number(f.get("risk")) } },
        limits: {
          max_open_positions: Number(f.get("max_positions")), max_total_open_risk_pct: Number(f.get("max_risk")),
          max_currency_exposure: Number(f.get("max_ccy")), max_daily_loss_pct: Number(f.get("daily_loss")),
        },
      });
      router.push(`/research/${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="space-y-2">
        {legs.map((l, k) => (
          <div key={k} className="grid gap-2 sm:grid-cols-[1fr_1fr_6rem_6rem_auto]">
            <input aria-label={`Leg ${k + 1} symbol`} list="pf-symbols" value={l.symbol} required maxLength={64}
              onChange={(e) => update(k, { symbol: e.target.value })} className={input} placeholder="Symbol" />
            <select aria-label={`Leg ${k + 1} strategy`} value={l.strategy} onChange={(e) => update(k, { strategy: e.target.value })} className={input}>
              {strategies.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select aria-label={`Leg ${k + 1} timeframe`} value={l.timeframe} onChange={(e) => update(k, { timeframe: e.target.value })} className={input}>
              {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
            </select>
            <select aria-label={`Leg ${k + 1} higher timeframe`} value={l.htf} onChange={(e) => update(k, { htf: e.target.value })} className={input}>
              <option value="">no HTF</option>{TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
            </select>
            <button type="button" className={secondaryButton} disabled={legs.length === 1}
              onClick={() => setLegs(legs.filter((_, j) => j !== k))}>Remove</button>
          </div>
        ))}
        <datalist id="pf-symbols">{symbols.map((s) => <option key={s} value={s} />)}</datalist>
        <button type="button" className={secondaryButton} disabled={legs.length >= 10}
          onClick={() => setLegs([...legs, { symbol: "", timeframe: "H1", htf: "", strategy: strategies[0] ?? "" }])}>Add leg</button>
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Start (UTC)"><input name="start" type="date" required className={input} defaultValue="2024-01-01" /></Field>
        <Field label="End (UTC)"><input name="end" type="date" required className={input} defaultValue={today} /></Field>
        <Field label="Starting balance"><input name="balance" type="number" min="1" step="any" defaultValue={10000} className={input} /></Field>
        <Field label="Risk per trade %"><input name="risk" type="number" min="0.01" max="5" step="0.01" defaultValue={0.25} className={input} /></Field>
        <Field label="Max open positions"><input name="max_positions" type="number" min="1" max="50" defaultValue={3} className={input} /></Field>
        <Field label="Max total open risk %"><input name="max_risk" type="number" min="0.1" max="20" step="0.05" defaultValue={1} className={input} /></Field>
        <Field label="Max same-currency bets" hint="correlation limit"><input name="max_ccy" type="number" min="1" max="10" defaultValue={2} className={input} /></Field>
        <Field label="Daily loss limit %"><input name="daily_loss" type="number" min="0.1" max="100" step="0.1" defaultValue={1} className={input} /></Field>
      </div>
      <button className={button} disabled={busy}>{busy ? "Running portfolio…" : "Run portfolio backtest"}</button>
      <Message error={error} />
    </form>
  );
}
