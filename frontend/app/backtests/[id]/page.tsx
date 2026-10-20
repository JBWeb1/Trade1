import { ActionButton } from "@/components/Actions";
import { DrawdownChart, EquityChart } from "@/components/charts/EquityChart";
import { Badge, Card, EmptyState, ErrorBox, Kv, PageHeader, SimulationLabel, Stat, Table, Td, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { money, num, pct, price, signClass, utc } from "@/lib/format";
import type { BacktestDetail, Breakdown, Metrics, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

function BreakdownTable({ data, label, missing }: { data?: Breakdown; label: string; missing?: string }) {
  if (data === undefined && missing) return <p className="text-sm text-dim">{missing}</p>;
  const rows = Object.entries(data ?? {});
  if (!rows.length) return <p className="text-sm text-dim">No trades.</p>;
  return (
    <Table head={[label, "Trades", "Net", "Expectancy"]} dense>
      {rows.map(([k, v]) => (
        <tr key={k}>
          <Td className="capitalize">{k.replace("_", " ")}</Td><Td>{v.trades}</Td>
          <Td className={signClass(v.net)}>{num(v.net)}</Td>
          <Td className={signClass(v.expectancy ?? v.net / v.trades)}>{num(v.expectancy ?? v.net / v.trades)}</Td>
        </tr>
      ))}
    </Table>
  );
}

export default async function BacktestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) return <ErrorBox error="Invalid backtest id" />;
  const [run, metrics, trades] = await Promise.all([
    tryApi<BacktestDetail>(`/api/backtests/${id}`),
    tryApi<Metrics>(`/api/backtests/${id}/metrics`),
    tryApi<{ items: Trade[] }>(`/api/backtests/${id}/trades?limit=5000`),
  ]);
  if (run.error) return (<><PageHeader title={`Backtest #${id}`} /><ErrorBox error={run.error} /></>);
  const r = run.data!;
  const m = metrics.data;
  const ccy = r.config.account_currency;

  const header = (
    <PageHeader title={`Backtest #${r.id} · ${r.symbol} ${r.timeframe}${r.htf ? ` + ${r.htf} filter` : ""}`}
      subtitle={<>{utc(r.start, false)} → {utc(r.end, false)} · {m?.strategy ? `${m.strategy.name} v${m.strategy.version} [${m.strategy.params_hash}]` : `strategy version ${r.strategy_version_id}`}</>}
      actions={<>
        <Badge tone={statusTone(r.status)}>{r.status}</Badge>
        {r.status === "completed" && (
          <ActionButton path={`validation/${r.id}`} label="Validate this strategy" busyLabel="Validating (can take minutes)…"
            confirmText="Validation uses this period's final 20% as untouched out-of-sample data. Re-validating the same period with different settings is recorded as re-use. Continue?"
            navigatePrefix="/validation/" />
        )}
      </>} />
  );
  if (r.status !== "completed") return (<>{header}<ErrorBox error={r.error ?? "Backtest did not complete"} /></>);
  if (!m) return (<>{header}<ErrorBox error={metrics.error ?? "no metrics"} /></>);

  return (
    <>
      {header}
      <SimulationLabel />
      {m.sample_warning && <div className="mb-4 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">⚠ {m.sample_warning}</div>}

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Closed trades" value={m.closed_trades} hint={`${pct(m.exposure_pct)} time in market`} />
        <Stat label="Win rate" value={pct(m.win_rate, 1, true)} hint={`avg win ${num(m.average_win)} / loss ${num(m.average_loss)}`} />
        <Stat label="Expectancy / trade" value={money(m.expectancy_per_trade, ccy)} tone={signClass(m.expectancy_per_trade)} hint={`${num(m.expectancy_r, 3)} R after costs`} />
        <Stat label="Profit factor" value={num(m.profit_factor)} />
        <Stat label="Net result" value={pct(m.net_return_pct, 2)} tone={signClass(m.net_return_pct)} hint={money(m.net_profit, ccy)} />
        <Stat label="Max drawdown" value={pct(m.max_drawdown_pct, 2)} hint={`${num(m.max_drawdown_duration_days ?? null, 1)} days longest`} />
      </div>

      <Card title={`Equity curve (${ccy}) — equity marks open positions at each bar close`} className="mt-4">
        {r.equity_curve.length ? <EquityChart points={r.equity_curve} currency={ccy} /> : <EmptyState title="No equity data" />}
      </Card>
      <Card title="Drawdown from running peak" className="mt-4">
        {r.equity_curve.length ? <DrawdownChart points={r.equity_curve} /> : <EmptyState title="No equity data" />}
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Risk and ratios">
          <Kv rows={[
            ["Sharpe (daily, annualised, rf=0)", num(m.sharpe_daily_annualised ?? null, 2)],
            ["Longest losing streak", m.longest_losing_streak],
            ["Avg drawdown duration", `${num(m.avg_drawdown_duration_days ?? null, 1)} days`],
            ["Turnover", `${num(m.turnover_lots)} lots`],
            ["Starting balance", money(r.config.starting_balance, ccy)],
            ["Leverage", `1:${r.config.leverage}`],
            ["Risk per trade", `${String((r.config.risk as Record<string, unknown>).risk_per_trade_pct)}%`],
          ]} />
        </Card>
        <Card title="Costs paid (account currency)">
          <Kv rows={Object.entries(m.costs).map(([k, v]) => [k.replace(/_/g, " "), num(v)])} />
        </Card>
        <Card title="Exits and rejected signals">
          <Kv rows={[...Object.entries(m.exit_reasons).map(([k, v]) => [`exit: ${k}`, v] as [string, number]),
            ...Object.entries(m.rejections).map(([k, v]) => [`rejected: ${k}`, v] as [string, number])]} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="By market regime"><BreakdownTable data={m.by_regime} label="Regime" missing="Not recorded: this run was made before regime tracking existed. Re-run the backtest to see it." /></Card>
        <Card title="By side"><BreakdownTable data={m.by_side} label="Side" /></Card>
        <Card title="By month"><div className="max-h-72 overflow-y-auto"><BreakdownTable data={m.by_month} label="Month" /></div></Card>
      </div>

      <Card title={`Trade journal (${trades.data?.items.length ?? 0})`} className="mt-4">
        {trades.error ? <ErrorBox error={trades.error} /> : !trades.data?.items.length ? <EmptyState title="No trades" /> : (
          <div className="max-h-[32rem] overflow-y-auto">
            <Table head={["Entry (UTC)", "Side", "Lots", "Entry", "Stop", "Target", "Exit", "Exit reason", "Bars", "Regime", "Costs", "Net", "Why"]} dense>
              {trades.data.items.map((t) => (
                <tr key={t.id}>
                  <Td>{utc(t.entry_time)}</Td>
                  <Td><Badge tone={t.side === "buy" ? "info" : "neutral"}>{t.side}</Badge></Td>
                  <Td>{t.volume}</Td><Td>{price(t.entry_price)}</Td><Td>{price(t.stop_loss)}</Td><Td>{price(t.take_profit)}</Td>
                  <Td>{price(t.exit_price)}</Td>
                  <Td>{t.exit_reason}{t.ambiguous_bar ? <span title="Stop and target inside the same bar: stop assumed first" className="text-warn"> *</span> : null}</Td>
                  <Td>{t.bars_held}</Td><Td className="text-dim">{t.regime ?? "—"}</Td>
                  <Td className="text-dim">{num(t.spread_cost + t.commission + t.slippage_cost + t.swap)}</Td>
                  <Td className={signClass(t.net_pnl)}>{num(t.net_pnl)}</Td>
                  <Td className="max-w-xs truncate text-xs text-dim" >{t.rationale}</Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
        <p className="mt-2 text-xs text-faint">* stop and target both inside one bar; the stop is assumed hit first (conservative).</p>
      </Card>

      <Card title="Simulation notes and reproducibility" className="mt-4">
        <ul className="list-inside list-disc space-y-1 text-sm text-dim">{m.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        <pre className="mt-3 overflow-x-auto rounded bg-bg p-3 text-xs text-faint">{JSON.stringify({ strategy: m.strategy, data: r.data_fingerprint, costs: r.config.costs }, null, 2)}</pre>
      </Card>
    </>
  );
}
