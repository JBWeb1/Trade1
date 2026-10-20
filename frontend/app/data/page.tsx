import { IngestForm } from "@/components/Actions";
import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { Coverage, IngestionRun, Instrument } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DataPage() {
  const [cov, runs, markets] = await Promise.all([
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
    tryApi<{ items: IngestionRun[] }>("/api/market-data/runs?limit=100"),
    tryApi<{ items: Instrument[] }>("/api/markets?limit=5000"),
  ]);
  const symbols = (markets.data?.items ?? []).map((m) => m.symbol);

  return (
    <>
      <PageHeader title="Historical data" subtitle="Bars downloaded from your MT5 terminal, quality-checked and stored in UTC. Nothing is invented when MT5 returns no data." />

      <Card title="Download history" className="mb-4">
        <IngestForm symbols={symbols} />
        <p className="mt-3 text-xs text-faint">Limits per request: M1 120 days, M15 ~3 years, H1 ~10 years, H4 ~20 years. MT5 may serve less history than requested; the run below says exactly what arrived.</p>
      </Card>

      <Card title="Stored coverage" className="mb-4">
        {cov.error ? <ErrorBox error={cov.error} /> : !cov.data?.items.length ? (
          <EmptyState title="No bars stored yet">Use the form above with MT5 open, or run scripts\run_checks.bat.</EmptyState>
        ) : (
          <Table head={["Symbol", "Timeframe", "Bars", "First bar (UTC)", "Last bar (UTC)"]} dense>
            {cov.data.items.map((c) => (
              <tr key={`${c.symbol}-${c.timeframe}`}>
                <Td><TextLink href={`/markets/${encodeURIComponent(c.symbol)}?tf=${c.timeframe}`}>{c.symbol}</TextLink></Td>
                <Td>{c.timeframe}</Td><Td>{c.bars.toLocaleString()}</Td><Td>{utc(c.first)}</Td><Td>{utc(c.last)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Download runs and data quality">
        {runs.error ? <ErrorBox error={runs.error} /> : !runs.data?.items.length ? (
          <EmptyState title="No downloads yet" />
        ) : (
          <Table head={["#", "Symbol", "TF", "Status", "Received", "Usable", "New", "Already stored", "Range served", "Quality notes"]} dense>
            {runs.data.items.map((r) => (
              <tr key={r.id}>
                <Td>{r.id}</Td><Td>{r.symbol}</Td><Td>{r.timeframe}</Td>
                <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                <Td>{r.bars_received.toLocaleString()}</Td><Td>{r.bars_valid.toLocaleString()}</Td>
                <Td>{r.bars_inserted.toLocaleString()}</Td><Td>{r.bars_already_present.toLocaleString()}</Td>
                <Td className="text-dim">{r.first_bar ? `${utc(r.first_bar, false)} → ${utc(r.last_bar, false)}` : "—"}</Td>
                <Td className="max-w-md whitespace-normal text-xs text-dim">
                  {r.error ?? ([
                    r.quality.gaps_unexpected ? `${r.quality.gaps_unexpected} unexpected gaps` : null,
                    r.quality.duplicates_removed ? `${r.quality.duplicates_removed} duplicates removed` : null,
                    r.quality.invalid_rows ? `${r.quality.invalid_rows} invalid bars dropped` : null,
                    r.quality.stale ? "data stale" : null,
                    ...(r.quality.notes ?? []),
                  ].filter(Boolean).join("; ") || "no issues")}
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
