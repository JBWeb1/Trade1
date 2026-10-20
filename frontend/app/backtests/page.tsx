import { BacktestForm } from "@/components/Actions";
import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, pct, signClass, utc } from "@/lib/format";
import type { BacktestSummary, Coverage, StrategyInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Backtests() {
  const [runs, strategies, cov] = await Promise.all([
    tryApi<{ items: BacktestSummary[] }>("/api/backtests?limit=200"),
    tryApi<StrategyInfo>("/api/strategies"),
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
  ]);
  const symbols = [...new Set((cov.data?.items ?? []).map((c) => c.symbol))];

  return (
    <>
      <PageHeader title="Backtests" subtitle="Historical, bar-based simulations with spread, commission, slippage and swap. In-sample unless validated." />

      <Card title="New backtest" className="mb-4">
        {strategies.error ? <ErrorBox error={strategies.error} /> : (
          <BacktestForm symbols={symbols} strategies={(strategies.data?.available ?? []).map((s) => s.name)} />
        )}
      </Card>

      <Card title="All runs">
        {runs.error ? <ErrorBox error={runs.error} /> : !runs.data?.items.length ? (
          <EmptyState title="No backtests yet">Run one with the form above.</EmptyState>
        ) : (
          <Table head={["#", "Symbol", "TF", "Period", "Status", "Trades", "Win rate", "Expectancy", "PF", "Net %", "Max DD %", "Sample"]} dense>
            {runs.data.items.map((b) => (
              <tr key={b.id}>
                <Td><TextLink href={`/backtests/${b.id}`}>{b.id}</TextLink></Td>
                <Td>{b.symbol}</Td><Td>{b.timeframe}{b.htf ? <span className="text-faint"> +{b.htf}</span> : null}</Td>
                <Td className="text-dim">{utc(b.start, false)} → {utc(b.end, false)}</Td>
                <Td><Badge tone={statusTone(b.status)}>{b.status}</Badge></Td>
                <Td>{b.headline.closed_trades ?? "—"}</Td>
                <Td>{pct(b.headline.win_rate, 1, true)}</Td>
                <Td className={signClass(b.headline.expectancy_per_trade)}>{num(b.headline.expectancy_per_trade)}</Td>
                <Td>{num(b.headline.profit_factor)}</Td>
                <Td className={signClass(b.headline.net_return_pct)}>{num(b.headline.net_return_pct)}</Td>
                <Td>{num(b.headline.max_drawdown_pct)}</Td>
                <Td>{b.status === "failed" ? <span className="text-neg">{b.error}</span>
                  : b.headline.sample_warning ? <Badge tone="warn">small sample</Badge> : <Badge tone="good">100+ trades</Badge>}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
