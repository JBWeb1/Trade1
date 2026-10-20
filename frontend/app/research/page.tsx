import { MultiMarketForm, PortfolioForm } from "@/components/Actions";
import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { Coverage, ResearchSummary, StrategyInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

const KIND: Record<string, string> = { multi_market: "Multi-market", portfolio: "Portfolio", tick_check: "Tick check" };

export default async function ResearchPage() {
  const [list, strategies, cov] = await Promise.all([
    tryApi<{ items: ResearchSummary[] }>("/api/research?limit=200"),
    tryApi<StrategyInfo>("/api/strategies"),
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
  ]);
  const symbols = [...new Set((cov.data?.items ?? []).map((c) => c.symbol))];
  const names = (strategies.data?.available ?? []).map((s) => s.name);

  return (
    <>
      <PageHeader title="Research studies" subtitle="Tests that stop you trusting a strategy that only looks good on one market, one cost assumption or one bar model." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Multi-market test: same strategy and parameters on several symbols">
          <MultiMarketForm symbols={symbols} strategies={names} />
        </Card>
        <Card title="Portfolio backtest: several legs on one account, combined limits">
          <PortfolioForm symbols={symbols} strategies={names} />
        </Card>
      </div>
      <p className="mt-2 text-xs text-faint">Tick checks are started from a backtest&apos;s page (“Check with tick data”). They need MT5 running.</p>

      <Card title="All studies" className="mt-4">
        {list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? (
          <EmptyState title="No studies yet" />
        ) : (
          <Table head={["#", "Created (UTC)", "Type", "Status", "Summary"]} dense>
            {list.data.items.map((r) => (
              <tr key={r.id}>
                <Td><TextLink href={`/research/${r.id}`}>{r.id}</TextLink></Td>
                <Td className="text-dim">{utc(r.created_at)}</Td>
                <Td>{KIND[r.kind] ?? r.kind}</Td>
                <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                <Td className="max-w-xl truncate text-dim">{r.summary ?? r.error ?? "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
