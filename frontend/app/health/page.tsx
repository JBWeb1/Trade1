import { Badge, Card, EmptyState, ErrorBox, Kv, PageHeader, Table, Td } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import type { Diagnostics, Health } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function HealthPage() {
  const [health, diag] = await Promise.all([tryApi<Health>("/api/health"), tryApi<Diagnostics>("/api/diagnostics")]);
  return (
    <>
      <PageHeader title="System health" subtitle="Connection, account verification, server clock and data problems, explained." />
      {health.error ? <ErrorBox error={health.error} /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Backend">
            <Kv rows={[
              ["API", <Badge key="a" tone={health.data!.status === "ok" ? "good" : "warn"}>{health.data!.status}</Badge>],
              ["Database", health.data!.database ? <Badge key="d" tone="good">reachable</Badge> : <Badge key="d" tone="bad">unreachable</Badge>],
              ["Version", health.data!.version],
              ["Execution mode", health.data!.execution_mode],
              ["Live trading", health.data!.live_trading],
            ]} />
          </Card>
          <Card title="MT5 terminal and account">
            {diag.error ? <ErrorBox error={diag.error} /> : (
              <Kv rows={[
                ["Connected", diag.data!.connection.connected ? <Badge key="c" tone="good">yes</Badge> : <Badge key="c" tone="bad">no</Badge>],
                ["Account mode", diag.data!.verification.verified_demo ? <Badge key="m" tone="good">DEMO verified</Badge> : <Badge key="m" tone="bad">{diag.data!.verification.account_mode} — not verified</Badge>],
                ["Server", diag.data!.verification.server ?? "—"],
                ["Account currency", diag.data!.verification.currency ?? "—"],
                ["Server clock offset", `estimated ${diag.data!.estimated_server_utc_offset_hours ?? "unknown (markets closed?)"} h, configured ${diag.data!.configured_server_utc_offset_hours} h`],
              ]} />
            )}
          </Card>
        </div>
      )}

      {diag.data && (
        <>
          <Card title="Problems" className="mt-4">
            {diag.data.problems.length === 0 ? <p className="text-sm text-pos">✓ No problems detected.</p> : (
              <ul className="space-y-1.5 text-sm">{diag.data.problems.map((p) => <li key={p} className="text-warn">• {p}</li>)}</ul>
            )}
          </Card>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card title="Flagged instruments (demo orders will be blocked)">
              {diag.data.flagged_instruments.length === 0 ? <EmptyState title="None flagged" /> : (
                <Table head={["Symbol", "Issues"]} dense>
                  {diag.data.flagged_instruments.map((f) => (
                    <tr key={f.symbol}><Td>{f.symbol}</Td><Td className="whitespace-normal text-xs text-dim">{f.issues.join("; ")}</Td></tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Recent problem downloads">
              {diag.data.recent_problem_ingestions.length === 0 ? <EmptyState title="No failed or warning downloads" /> : (
                <Table head={["#", "Symbol", "TF", "Status", "Details"]} dense>
                  {diag.data.recent_problem_ingestions.map((r) => (
                    <tr key={r.id}><Td>{r.id}</Td><Td>{r.symbol}</Td><Td>{r.timeframe}</Td>
                      <Td><Badge tone={r.status === "failed" ? "bad" : "warn"}>{r.status}</Badge></Td>
                      <Td className="whitespace-normal text-xs text-dim">{r.error ?? r.notes.join("; ")}</Td></tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
