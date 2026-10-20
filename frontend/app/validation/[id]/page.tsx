import { Badge, Card, ErrorBox, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, pct, signClass, utc } from "@/lib/format";
import type { SegmentSummary, ValidationDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

const VERDICT_TEXT: Record<string, string> = {
  PASSED: "Survived every screening check on this data. Next step: 2–3 months of demo forward testing, compared against this backtest.",
  FAILED: "A performance or robustness check failed. This is the system doing its job: do not trade this strategy as configured.",
  INCONCLUSIVE: "Not enough evidence either way (sample size or re-used out-of-sample data). Do not treat it as validated.",
};

function SegRow({ name, s }: { name: string; s: SegmentSummary }) {
  return (
    <tr>
      <Td className="capitalize">{name.replace(/_/g, " ")}</Td>
      <Td className="text-dim">{utc(s.first_bar_close ?? null, false)} → {utc(s.last_bar_close ?? null, false)}</Td>
      <Td>{s.closed_trades}</Td><Td>{pct(s.win_rate, 1, true)}</Td>
      <Td className={signClass(s.expectancy_r)}>{num(s.expectancy_r, 3)}</Td>
      <Td>{num(s.profit_factor)}</Td>
      <Td className={signClass(s.net_return_pct)}>{num(s.net_return_pct)}</Td>
      <Td>{num(s.max_drawdown_pct)}</Td>
    </tr>
  );
}

export default async function ValidationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) return <ErrorBox error="Invalid report id" />;
  const res = await tryApi<ValidationDetail>(`/api/validation/${id}`);
  if (res.error) return (<><PageHeader title={`Validation #${id}`} /><ErrorBox error={res.error} /></>);
  const v = res.data!;
  if (v.status !== "completed") return (<><PageHeader title={`Validation #${id}`} /><ErrorBox error={v.error ?? "validation did not complete"} /></>);
  const r = v.report;
  const verdict = v.verdict ?? "INCONCLUSIVE";

  return (
    <>
      <PageHeader title={`Validation #${v.id} · ${v.symbol} ${v.timeframe}${v.htf ? ` + ${v.htf}` : ""}`}
        subtitle={<>{r.strategy ? `${r.strategy.name} v${r.strategy.version} [${r.strategy.params_hash}]` : ""} · {utc(v.start, false)} → {utc(v.end, false)}
          {v.backtest_run_id ? <> · from <TextLink href={`/backtests/${v.backtest_run_id}`}>backtest #{v.backtest_run_id}</TextLink></> : null}</>} />

      <div className={`mb-4 rounded-lg border px-4 py-3 ${verdict === "PASSED" ? "border-pos/40 bg-pos/10" : verdict === "FAILED" ? "border-neg/40 bg-neg/10" : "border-warn/40 bg-warn/10"}`}>
        <div className="flex items-center gap-3"><Badge tone={statusTone(verdict)}>{verdict}</Badge>
          <span className="text-sm">{VERDICT_TEXT[verdict]}</span></div>
        <p className="mt-2 text-xs text-dim">{r.disclaimer}</p>
      </div>

      <Card title="Checks" className="mb-4">
        <ul className="space-y-1.5 text-sm">
          {r.checks.map((c) => (
            <li key={c.check} className="flex flex-wrap gap-x-2">
              <span className={c.passed ? "text-pos" : "text-neg"} aria-label={c.passed ? "passed" : "failed"}>{c.passed ? "✓" : "✗"}</span>
              <span className="sm:min-w-56">{c.check}</span>
              <span className="text-dim">{c.detail}</span>
              <span className="ml-auto text-xs uppercase text-faint">{c.kind}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Chronological segments (each starts with a fresh balance)" className="mb-4">
        <Table head={["Segment", "Period", "Trades", "Win rate", "Expectancy R", "PF", "Net %", "Max DD %"]} dense>
          <SegRow name="development" s={r.segments.development} />
          <SegRow name="validation" s={r.segments.validation} />
          <SegRow name="out_of_sample (untouched)" s={r.segments.out_of_sample} />
        </Table>
        <p className="mt-2 text-xs text-faint">Out-of-sample t-statistic of R-multiples: {num(r.oos_t_stat, 2)} (roughly: above 2 suggests the result is unlikely to be pure luck).
          {v.oos_prior_uses > 0 && <span className="text-warn"> This out-of-sample window had been used {v.oos_prior_uses} time(s) before.</span>}</p>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={`Walk-forward — ${r.walk_forward.oos_trades} OOS trades, ${num(r.walk_forward.oos_expectancy_r, 3)} R, ${pct(r.walk_forward.positive_fraction, 0, true)} of folds positive`}>
          <Table head={["Fold", "Tested on", "Chosen params", "IS exp R", "OOS trades", "OOS exp R"]} dense>
            {r.walk_forward.folds.map((f) => (
              <tr key={f.fold}>
                <Td>{f.fold}</Td>
                <Td className="text-dim">{utc(f.out_of_sample[0], false)} → {utc(f.out_of_sample[1], false)}</Td>
                <Td className="font-mono text-xs">{Object.entries(f.chosen_params).map(([k, x]) => `${k}=${x}`).join(", ") || "base"}</Td>
                <Td>{num(f.in_sample_expectancy_r, 3)}</Td>
                <Td>{f.oos.closed_trades}</Td>
                <Td className={signClass(f.oos.expectancy_r)}>{num(f.oos.expectancy_r, 3)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title={`Parameter sensitivity (±${Math.round(r.sensitivity.step * 100)}%) — ${pct(r.sensitivity.positive_fraction, 0, true)} positive`}>
          <Table head={["Parameter", "Value", "Trades", "Expectancy R", "PF"]} dense>
            {r.sensitivity.runs.map((s, i) => (
              <tr key={i}>
                <Td className="font-mono text-xs">{s.param}</Td><Td>{String(s.value)}</Td><Td>{s.closed_trades}</Td>
                <Td className={signClass(s.expectancy_r)}>{num(s.expectancy_r, 3)}</Td><Td>{num(s.profit_factor)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <p className="mt-4 text-xs text-faint">Splits: development {utc(r.splits.start, false)} → {utc(r.splits.development_end, false)}, validation → {utc(r.splits.validation_end, false)}, out-of-sample → {utc(r.splits.end, false)}. Walk-forward and sensitivity never use the out-of-sample block.</p>
    </>
  );
}
