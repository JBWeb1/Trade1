import { MonteCarloCard } from "@/components/MonteCarlo";
import { Badge, Card, ErrorBox, Kv, PageHeader, Stat, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, pct, signClass, utc } from "@/lib/format";
import type { MonteCarlo, ResearchDetail, SegmentSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

type Row = SegmentSummary & { symbol: string; category?: string; error?: string };

function MultiMarket({ r }: { r: Record<string, unknown> }) {
  const rows = (r.symbols as Row[]) ?? [];
  const verdict = String(r.verdict);
  return (
    <>
      <div className="mb-4 flex items-center gap-3 rounded-lg border border-line bg-panel px-4 py-3">
        <Badge tone={verdict === "CONSISTENT" ? "good" : verdict === "INCONSISTENT" ? "bad" : "warn"}>{verdict}</Badge>
        <span className="text-sm">{String(r.reason)}</span>
      </div>
      <Card title="Per symbol (identical parameters, nothing tuned per symbol)">
        <Table head={["Symbol", "Category", "Trades", "Win rate", "Expectancy R", "PF", "Net %", "Max DD %"]} dense>
          {rows.map((x) => x.error ? (
            <tr key={x.symbol}><Td>{x.symbol}</Td><Td className="text-neg" >{x.error}</Td><Td>—</Td><Td>—</Td><Td>—</Td><Td>—</Td><Td>—</Td><Td>—</Td></tr>
          ) : (
            <tr key={x.symbol}>
              <Td>{x.symbol}</Td><Td className="capitalize text-dim">{x.category ?? "—"}</Td><Td>{x.closed_trades}</Td>
              <Td>{pct(x.win_rate, 1, true)}</Td>
              <Td className={signClass(x.expectancy_r)}>{num(x.expectancy_r, 3)}</Td>
              <Td>{num(x.profit_factor)}</Td>
              <Td className={signClass(x.net_return_pct)}>{num(x.net_return_pct)}</Td>
              <Td>{num(x.max_drawdown_pct)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

function Portfolio({ r }: { r: Record<string, unknown> }) {
  const legs = (r.legs as { leg: string; error?: string; trades_standalone?: number; expectancy_r_standalone?: number | null }[]) ?? [];
  const byLeg = (r.by_leg as Record<string, { trades: number; net: number }>) ?? {};
  const corr = (r.leg_correlation_daily_r as Record<string, Record<string, number | null>>) ?? {};
  const corrLegs = Object.keys(corr);
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Trades taken" value={`${r.accepted} / ${r.signals}`} hint="the rest blocked by limits" />
        <Stat label="Win rate" value={pct(r.win_rate as number | null, 1, true)} />
        <Stat label="Expectancy" value={`${num(r.expectancy_r as number | null, 3)} R`} tone={signClass(r.expectancy_r as number)} />
        <Stat label="Profit factor" value={num(r.profit_factor as number | null)} />
        <Stat label="Net return" value={pct(r.net_return_pct as number, 2)} tone={signClass(r.net_return_pct as number)} />
        <Stat label="Max drawdown" value={pct(r.max_drawdown_pct as number, 2)} />
      </div>
      <div className="mt-4 grid gap-4">
        <Card title="Legs">
          <Table head={["Leg", "Standalone trades", "Standalone R", "Taken in portfolio", "Net in portfolio"]} dense>
            {legs.map((l) => (
              <tr key={l.leg}>
                <Td className="font-mono text-xs">{l.leg}</Td>
                {l.error ? <Td className="text-neg">{l.error}</Td> : <Td>{l.trades_standalone}</Td>}
                <Td className={signClass(l.expectancy_r_standalone)}>{num(l.expectancy_r_standalone ?? null, 3)}</Td>
                <Td>{byLeg[l.leg]?.trades ?? 0}</Td>
                <Td className={signClass(byLeg[l.leg]?.net)}>{num(byLeg[l.leg]?.net ?? null)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Blocked by combined limits">
          <Kv rows={Object.entries((r.rejected as Record<string, number>) ?? {}).map(([k, v]) => [k, v])} />
          {!Object.keys((r.rejected as object) ?? {}).length && <p className="text-sm text-dim">Nothing was blocked.</p>}
          <p className="mt-3 text-xs text-faint">{String(r.method)}</p>
        </Card>
      </div>
      {corrLegs.length > 1 && (
        <Card title="Correlation of daily results between legs (high = same bet twice)" className="mt-4">
          <Table head={["", ...corrLegs.map((l) => l.split(":")[0])]} dense>
            {corrLegs.map((a) => (
              <tr key={a}>
                <Td className="font-mono text-xs">{a}</Td>
                {corrLegs.map((b) => {
                  const v = corr[a][b];
                  return <Td key={b} className={a !== b && v !== null && Math.abs(v) > 0.6 ? "text-warn" : ""}>{num(v, 2)}</Td>;
                })}
              </tr>
            ))}
          </Table>
        </Card>
      )}
      <div className="mt-4"><MonteCarloCard mc={r.monte_carlo as MonteCarlo | null} title="Monte Carlo on the portfolio's trades" /></div>
    </>
  );
}

function TickCheck({ r, params }: { r: Record<string, unknown>; params: Record<string, unknown> }) {
  const c = (r.counts as Record<string, number>) ?? {};
  const ex = (r.examples as { trade_id: number; entry_time: string; side: string; simulated: string; ticks_say: string | null; ambiguous_bar: boolean }[]) ?? [];
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Trades checked" value={c.checked} hint={String(r.coverage)} />
        <Stat label="Confirmed by ticks" value={c.confirmed} tone="text-pos" />
        <Stat label="Would have hit target" value={c.flipped_to_target} />
        <Stat label="Would have hit stop" value={c.flipped_to_stop} />
        <Stat label="No tick data" value={c.no_ticks_available} />
        <Stat label="R change if ticks are right" value={num(r.r_change_if_ticks_are_right as number, 2)} tone={signClass(r.r_change_if_ticks_are_right as number)} />
      </div>
      <p className="mt-3 text-sm text-dim">From <TextLink href={`/backtests/${params.backtest_run_id}`}>backtest #{String(params.backtest_run_id)}</TextLink>. {String(r.note)}</p>
      {ex.length > 0 && (
        <Card title="Trades where ticks disagree with the bar simulation" className="mt-4">
          <Table head={["Trade", "Entry (UTC)", "Side", "Simulated", "Ticks say", "Ambiguous bar"]} dense>
            {ex.map((e) => (
              <tr key={e.trade_id}><Td>{e.trade_id}</Td><Td>{utc(e.entry_time)}</Td><Td>{e.side}</Td>
                <Td>{e.simulated}</Td><Td>{e.ticks_say ?? "neither level touched"}</Td><Td>{e.ambiguous_bar ? "yes" : "no"}</Td></tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  );
}

export default async function ResearchDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) return <ErrorBox error="Invalid id" />;
  const res = await tryApi<ResearchDetail>(`/api/research/${id}`);
  if (res.error) return (<><PageHeader title={`Study #${id}`} /><ErrorBox error={res.error} /></>);
  const d = res.data!;
  const title = { multi_market: "Multi-market test", portfolio: "Portfolio backtest", tick_check: "Tick-data check" }[d.kind] ?? d.kind;
  return (
    <>
      <PageHeader title={`${title} #${d.id}`} subtitle={utc(d.created_at)} actions={<Badge tone={statusTone(d.status)}>{d.status}</Badge>} />
      {d.kind !== "tick_check" && (
        <div className="mb-4 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          HISTORICAL SIMULATION — bar-based backtests on MT5 bid bars. Not demo or live results, not a forecast.
        </div>
      )}
      {d.status !== "completed" ? <ErrorBox error={d.error ?? "study did not complete"} /> :
        d.kind === "multi_market" ? <MultiMarket r={d.result} /> :
        d.kind === "portfolio" ? <Portfolio r={d.result} /> :
        <TickCheck r={d.result} params={d.params} />}
      <Card title="Inputs (for reproducibility)" className="mt-4">
        <pre className="overflow-x-auto text-xs text-faint">{JSON.stringify(d.params, null, 2)}</pre>
      </Card>
    </>
  );
}
