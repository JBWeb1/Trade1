import { Badge, Card, EmptyState, ErrorBox, PageHeader, Stat, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, pct, signClass, utc } from "@/lib/format";
import type { BacktestSummary, Coverage, Health, Mt5Status, ValidationSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

function freshness(cov: Coverage[]) {
  if (!cov.length) return { label: "No data", tone: "neutral" as const };
  const newest = Math.max(...cov.map((c) => new Date(c.last).getTime()));
  const hours = (Date.now() - newest) / 3.6e6;
  return { label: `${utc(new Date(newest).toISOString())}`, tone: hours < 72 ? "good" as const : "warn" as const, hours };
}

export default async function Overview() {
  const [health, mt5, cov, bts, vals] = await Promise.all([
    tryApi<Health>("/api/health"),
    tryApi<Mt5Status>("/api/mt5/status"),
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
    tryApi<{ items: BacktestSummary[] }>("/api/backtests?limit=8"),
    tryApi<{ items: ValidationSummary[] }>("/api/validation?limit=8"),
  ]);

  if (health.error) {
    return (
      <>
        <PageHeader title="Overview" />
        <ErrorBox error={health.error}>
          <p className="mt-2 text-dim">Start the backend with <code>scripts\start_trade1.bat</code>, then reload this page.</p>
        </ErrorBox>
      </>
    );
  }

  const v = mt5.data?.verification;
  const coverage = cov.data?.items ?? [];
  const fresh = freshness(coverage);
  const backtests = bts.data?.items ?? [];
  const reports = vals.data?.items ?? [];
  const passed = reports.filter((r) => r.verdict === "PASSED").length;
  const latest = backtests.find((b) => b.status === "completed");

  return (
    <>
      <PageHeader title="Overview" subtitle="Everything shown here comes from stored calculations. Nothing is simulated for display." />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="MT5 connection"
          value={mt5.data?.connected ? <Badge tone="good">Connected</Badge> : <Badge tone="bad">Not connected</Badge>}
          hint={mt5.data?.connected ? `ping ${num(mt5.data.ping_ms ?? null, 0)} ms · build ${mt5.data.build}` : mt5.data?.error ?? mt5.error} />
        <Stat label="Verified account mode"
          value={v?.verified_demo ? <Badge tone="good">DEMO verified</Badge> : <Badge tone="bad">{v?.account_mode ?? "UNKNOWN"} — not verified</Badge>}
          hint={v?.server ?? v?.reasons?.[0]} />
        <Stat label="Execution mode" value={health.data?.execution_mode} hint="Live trading: not implemented" />
        <Stat label="Newest stored bar" value={<Badge tone={fresh.tone}>{fresh.label}</Badge>}
          hint={`${coverage.length} symbol/timeframe series stored`} />
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Backtests run" value={backtests.length ? `${backtests.length}${backtests.length === 8 ? "+" : ""}` : "0"} />
        <Stat label="Latest backtest: trades" value={latest?.headline.closed_trades ?? "—"}
          hint={latest ? `#${latest.id} ${latest.symbol} ${latest.timeframe}` : "no completed backtest yet"} />
        <Stat label="Latest backtest: net return" value={pct(latest?.headline.net_return_pct ?? null, 2)}
          tone={signClass(latest?.headline.net_return_pct)} hint="after costs, historical simulation" />
        <Stat label="Strategies PASSED validation" value={`${passed} of ${reports.length}`}
          hint="demo automation stays locked until one passes" />
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <Card title="Latest backtests" right={<TextLink href="/backtests">All backtests</TextLink>}>
          {bts.error ? <ErrorBox error={bts.error} /> : backtests.length === 0 ? (
            <EmptyState title="No backtests yet">Download history on the Historical data page, then run one on the Backtests page (or run scripts\run_backtest.bat).</EmptyState>
          ) : (
            <Table head={["#", "Symbol", "TF", "Status", "Trades", "Win rate", "PF", "Net %", "Max DD %"]} dense>
              {backtests.map((b) => (
                <tr key={b.id}>
                  <Td><TextLink href={`/backtests/${b.id}`}>{b.id}</TextLink></Td>
                  <Td>{b.symbol}</Td><Td>{b.timeframe}</Td>
                  <Td><Badge tone={statusTone(b.status)}>{b.status}</Badge></Td>
                  <Td>{b.headline.closed_trades ?? "—"}</Td>
                  <Td>{pct(b.headline.win_rate, 1, true)}</Td>
                  <Td>{num(b.headline.profit_factor)}</Td>
                  <Td className={signClass(b.headline.net_return_pct)}>{num(b.headline.net_return_pct)}</Td>
                  <Td>{num(b.headline.max_drawdown_pct)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>

        <Card title="Latest validation verdicts" right={<TextLink href="/validation">All reports</TextLink>}>
          {vals.error ? <ErrorBox error={vals.error} /> : reports.length === 0 ? (
            <EmptyState title="No validation reports yet">Validate a completed backtest from its page, or run scripts\run_backtest.bat.</EmptyState>
          ) : (
            <Table head={["#", "Symbol", "TF", "Verdict", "Main reason"]} dense>
              {reports.map((r) => (
                <tr key={r.id}>
                  <Td><TextLink href={`/validation/${r.id}`}>{r.id}</TextLink></Td>
                  <Td>{r.symbol}</Td><Td>{r.timeframe}</Td>
                  <Td><Badge tone={statusTone(r.verdict ?? r.status)}>{r.verdict ?? r.status}</Badge></Td>
                  <Td className="max-w-xs truncate text-dim">{r.reasons[0] ?? r.error ?? "all checks passed"}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="Demo positions and daily P/L">
          <EmptyState title="Demo execution is not built yet">
            No demo orders exist. The demo order module (Phase E) unlocks only after a strategy passes validation on real data.
          </EmptyState>
        </Card>
        <Card title="Account checks">
          {v && (v.reasons.length || v.warnings.length) ? (
            <ul className="space-y-1 text-sm">
              {v.reasons.map((r) => <li key={r} className="text-neg">✗ {r}</li>)}
              {v.warnings.map((w) => <li key={w} className="text-warn">! {w}</li>)}
            </ul>
          ) : v?.verified_demo ? (
            <p className="text-sm text-pos">✓ The terminal reports a DEMO account on a connected trade server.</p>
          ) : <p className="text-sm text-dim">No account information. See System health.</p>}
        </Card>
      </div>
    </>
  );
}
