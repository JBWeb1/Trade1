import Link from "next/link";

import { Card, EmptyState, ErrorBox, Kv, PageHeader, Table, Td } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

export const dynamic = "force-dynamic";

type SettingsView = {
  execution_mode: string; environment: string; database: string; api_token: string;
  mt5: Record<string, string | number>; news: Record<string, unknown>; how_to_change: string;
};

const FILTERS = ["", "backtest", "validation", "research", "market_data", "instruments", "news"];

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ action?: string }> }) {
  const { action } = await searchParams;
  const filter = FILTERS.includes(action ?? "") ? action ?? "" : "";
  const [st, audit] = await Promise.all([
    tryApi<SettingsView>("/api/settings"),
    tryApi<{ items: AuditEntry[] }>(`/api/audit?limit=300${filter ? `&action=${filter}` : ""}`),
  ]);
  return (
    <>
      <PageHeader title="Settings & audit history" subtitle="Effective configuration (secrets shown only as set / not set) and the record of what the system did." />
      {st.error ? <ErrorBox error={st.error} /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Application">
            <Kv rows={[["Execution mode", st.data!.execution_mode], ["Live trading", "not implemented in this release"],
              ["Environment", st.data!.environment], ["Database", st.data!.database], ["API token", st.data!.api_token]]} />
          </Card>
          <Card title="MetaTrader 5">
            <Kv rows={Object.entries(st.data!.mt5).map(([k, v]) => [k.replace(/_/g, " "), String(v)])} />
          </Card>
          <Card title="News calendar">
            <Kv rows={Object.entries(st.data!.news).map(([k, v]) => [k.replace(/_/g, " "), Array.isArray(v) ? v.join(", ") : String(v)])} />
          </Card>
        </div>
      )}
      {st.data && <p className="mt-2 text-xs text-faint">To change settings, {st.data.how_to_change}. Credentials are never editable from the browser.</p>}

      <Card title="Audit log" className="mt-4" right={
        <div className="flex flex-wrap gap-1 text-xs">
          {FILTERS.map((f) => (
            <Link key={f || "all"} href={f ? `/settings?action=${f}` : "/settings"}
              className={`rounded px-2 py-0.5 ${filter === f ? "bg-panel-2 text-fg" : "text-dim"}`}>{f || "all"}</Link>
          ))}
        </div>}>
        {audit.error ? <ErrorBox error={audit.error} /> : !audit.data?.items.length ? <EmptyState title="No audit entries" /> : (
          <div className="max-h-[36rem] overflow-y-auto">
            <Table head={["Time (UTC)", "Actor", "Action", "Details"]} dense>
              {audit.data.items.map((a) => (
                <tr key={a.id}>
                  <Td className="text-dim">{utc(a.ts)}</Td><Td>{a.actor}</Td><Td className="font-mono text-xs">{a.action}</Td>
                  <Td className="max-w-2xl truncate font-mono text-xs text-dim">{JSON.stringify(a.details)}</Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </>
  );
}
