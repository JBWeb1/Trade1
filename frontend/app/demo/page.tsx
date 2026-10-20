import { Badge, Card, PageHeader } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import type { BacktestSummary, Coverage, Health, Instrument, Mt5Status, ValidationSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

type Gate = { label: string; ok: boolean | null; detail: string };

export default async function DemoPage() {
  const [health, mt5, cov, vals, markets, bts] = await Promise.all([
    tryApi<Health>("/api/health"),
    tryApi<Mt5Status>("/api/mt5/status"),
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
    tryApi<{ items: ValidationSummary[] }>("/api/validation?limit=500"),
    tryApi<{ items: Instrument[] }>("/api/markets?limit=5000"),
    tryApi<{ items: BacktestSummary[] }>("/api/backtests?limit=500"),
  ]);
  const completed = (bts.data?.items ?? []).filter((b) => b.status === "completed").length;
  const passed = (vals.data?.items ?? []).filter((v) => v.verdict === "PASSED" && v.oos_prior_uses === 0);
  const passedSymbols = new Set(passed.map((p) => p.symbol));
  const specOk = (markets.data?.items ?? []).some((m) => passedSymbols.has(m.symbol) && m.metadata_ok);

  const gates: Gate[] = [
    { label: "Historical data stored and quality-checked", ok: (cov.data?.items.length ?? 0) > 0, detail: `${cov.data?.items.length ?? 0} series stored` },
    { label: "A backtest with realistic costs completed", ok: completed > 0, detail: `${completed} completed backtest(s)` },
    { label: "A strategy PASSED validation (first use of its out-of-sample data)", ok: passed.length > 0, detail: passed.length ? `${passed.length} report(s): ${[...passedSymbols].join(", ")}` : "none yet" },
    { label: "Instrument specification valid for that symbol", ok: passed.length ? specOk : null, detail: passed.length ? "" : "needs a passed strategy first" },
    { label: "MT5 connection healthy", ok: !!mt5.data?.connected, detail: mt5.data?.connected ? "connected" : mt5.data?.error ?? mt5.error ?? "not connected" },
    { label: "Terminal account positively verified as DEMO", ok: !!mt5.data?.verification.verified_demo, detail: mt5.data?.verification.reasons[0] ?? mt5.data?.verification.server ?? "" },
    { label: "Demo execution gateway implemented (Phase E)", ok: false, detail: "not built in this release" },
    { label: "No unresolved order reconciliation issues", ok: null, detail: "applies once demo execution exists" },
    { label: "User explicitly enabled demo mode", ok: null, detail: `execution mode: ${health.data?.execution_mode ?? "unknown"}` },
  ];

  return (
    <>
      <PageHeader title="Demo automation" subtitle="Automated demo trading stays disabled until every requirement below passes. Live trading does not exist in this release." />
      <div className="mb-4 rounded-lg border border-line bg-panel px-4 py-3 text-sm">
        <Badge tone="bad">LOCKED</Badge>
        <span className="ml-3 text-dim">No order can be sent from this application. The backend has no order route, and its tests prove it.</span>
      </div>
      <Card title="Demo trading gate">
        <ul className="space-y-2 text-sm">
          {gates.map((g) => (
            <li key={g.label} className="flex flex-wrap items-center gap-2">
              <span className={g.ok ? "text-pos" : g.ok === null ? "text-faint" : "text-neg"} aria-label={g.ok ? "passed" : g.ok === null ? "not applicable yet" : "failed"}>
                {g.ok ? "✓" : g.ok === null ? "○" : "✗"}
              </span>
              <span>{g.label}</span>
              {g.detail && <span className="text-xs text-dim">— {g.detail}</span>}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Open demo positions and pending orders" className="mt-4">
        <p className="text-sm text-dim">Nothing to show: demo execution has not been built. When it is, this page will list positions and orders read back from MT5 (not from the app&apos;s own records), with reconciliation status and the emergency stop.</p>
      </Card>
    </>
  );
}
