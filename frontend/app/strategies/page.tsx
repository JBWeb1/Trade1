import { Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { StrategyInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Strategies() {
  const res = await tryApi<StrategyInfo>("/api/strategies");
  if (res.error) return (<><PageHeader title="Strategies" /><ErrorBox error={res.error} /></>);
  const { available, versions } = res.data!;
  return (
    <>
      <PageHeader title="Strategy library" subtitle="Rule-based research candidates. Each is a hypothesis to test, not a strategy known to be profitable." />
      <div className="grid gap-4 xl:grid-cols-3">
        {available.map((s) => (
          <Card key={s.name} title={<span className="font-mono text-fg">{s.name} <span className="text-faint">v{s.version}</span></span>}>
            <p className="mb-3 text-sm text-dim">{s.description}</p>
            <Table head={["Parameter", "Default", "Range"]} dense>
              {Object.entries(s.params_schema.properties).map(([k, p]) => (
                <tr key={k}>
                  <Td className="font-mono text-xs">{k}</Td><Td>{String(p.default ?? "—")}</Td>
                  <Td className="text-dim">{p.minimum !== undefined || p.maximum !== undefined ? `${p.minimum ?? ""} … ${p.maximum ?? ""}` : p.type ?? ""}</Td>
                </tr>
              ))}
            </Table>
          </Card>
        ))}
      </div>

      <Card title="Frozen parameter versions (immutable, created by backtests)" className="mt-4">
        {!versions.length ? <EmptyState title="No versions yet">A version is stored the first time a parameter set is backtested.</EmptyState> : (
          <Table head={["#", "Strategy", "Version", "Params hash", "Parameters", "Created (UTC)"]} dense>
            {versions.map((v) => (
              <tr key={v.id}>
                <Td>{v.id}</Td><Td className="font-mono">{v.name}</Td><Td>{v.version}</Td>
                <Td className="font-mono text-xs">{v.params_hash}</Td>
                <Td className="max-w-lg truncate font-mono text-xs text-dim">{JSON.stringify(v.params)}</Td>
                <Td className="text-dim">{utc(v.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <p className="mt-3 text-xs text-faint">Run a strategy on the <TextLink href="/backtests">Backtests</TextLink> page.</p>
    </>
  );
}
