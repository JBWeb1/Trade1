import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { ValidationSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ValidationList() {
  const res = await tryApi<{ items: ValidationSummary[] }>("/api/validation?limit=200");
  return (
    <>
      <PageHeader title="Validation" subtitle="Out-of-sample, walk-forward and sensitivity tests. Thresholds are engineering defaults; PASSED is not a profit guarantee." />
      <Card>
        {res.error ? <ErrorBox error={res.error} /> : !res.data?.items.length ? (
          <EmptyState title="No validation reports yet">Open a completed backtest and click “Validate this strategy”.</EmptyState>
        ) : (
          <Table head={["#", "Created (UTC)", "Symbol", "TF", "Period", "Verdict", "OOS re-use", "Backtest", "First failed check"]} dense>
            {res.data.items.map((r) => (
              <tr key={r.id}>
                <Td><TextLink href={`/validation/${r.id}`}>{r.id}</TextLink></Td>
                <Td className="text-dim">{utc(r.created_at)}</Td>
                <Td>{r.symbol}</Td><Td>{r.timeframe}</Td>
                <Td className="text-dim">{utc(r.start, false)} → {utc(r.end, false)}</Td>
                <Td><Badge tone={statusTone(r.verdict ?? r.status)}>{r.verdict ?? r.status}</Badge></Td>
                <Td>{r.oos_prior_uses ? <Badge tone="warn">{r.oos_prior_uses} previous</Badge> : <span className="text-dim">first use</span>}</Td>
                <Td>{r.backtest_run_id ? <TextLink href={`/backtests/${r.backtest_run_id}`}>#{r.backtest_run_id}</TextLink> : "—"}</Td>
                <Td className="max-w-sm truncate text-dim">{r.reasons[0] ?? r.error ?? "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
