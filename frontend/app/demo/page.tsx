import { EmergencyStopButton, EnableDemoForm, ResetEmergencyForm, ResolveEventForm, SimpleAction } from "@/components/DemoControls";
import { Badge, Card, EmptyState, ErrorBox, Kv, PageHeader, Table, Td, TextLink, statusTone } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, price, signClass, utc } from "@/lib/format";

export const dynamic = "force-dynamic";

type DemoStatus = {
  execution_mode: string;
  state: { enabled?: boolean; report_id?: number; strategy?: string; params_hash?: string; symbol?: string;
    timeframe?: string; login?: number; server?: string; enabled_at?: string; last_cycle?: Record<string, unknown>;
    positions_error?: string };
  emergency_stop: { active?: boolean; reason?: string; actor?: string; at?: string };
  requirements: { requirement: string; passed: boolean; detail: string }[];
  passed_reports: { id: number; symbol: string; timeframe: string }[];
  confirmation_text: string;
  positions: { ticket: number; symbol: string; type: number; volume: number; price_open: number; sl: number; tp: number; profit: number; swap: number }[];
  orders: { id: number; created_at: string; kind: string; symbol: string; side: string; volume: number; stop_loss: number | null;
    take_profit: number | null; requested_price: number | null; fill_price: number | null; slippage_points: number | null;
    status: string; reason: string | null }[];
  reconciliation: { id: number; created_at: string; kind: string; details: Record<string, unknown>; resolved: boolean; resolution: string | null }[];
  limits: Record<string, number>;
};

export default async function DemoPage() {
  const res = await tryApi<DemoStatus>("/api/demo/status");
  if (res.error) return (<><PageHeader title="Demo automation" /><ErrorBox error={res.error} /></>);
  const d = res.data!;
  const enabled = !!d.state.enabled;
  const open = d.reconciliation.filter((e) => !e.resolved);

  return (
    <>
      <PageHeader title="Demo automation"
        subtitle="Forward testing on your Exness DEMO account only. Live trading does not exist in this release."
        actions={<EmergencyStopButton />} />

      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-panel px-4 py-3 text-sm">
        {d.emergency_stop.active ? <Badge tone="bad">EMERGENCY STOP ACTIVE</Badge>
          : enabled ? <Badge tone="good">DEMO TRADING ON</Badge> : <Badge tone="neutral">DEMO TRADING OFF</Badge>}
        <span className="text-dim">Execution mode on the server: <span className="text-fg">{d.execution_mode}</span></span>
        {open.length > 0 && <Badge tone="bad">{open.length} reconciliation issue(s): new entries blocked</Badge>}
        {enabled && <span className="text-dim">Trading <span className="font-mono text-fg">{d.state.strategy}</span> [{d.state.params_hash}] on {d.state.symbol} {d.state.timeframe} ·
          account {d.state.login}@{d.state.server} · since {utc(d.state.enabled_at)} · from <TextLink href={`/validation/${d.state.report_id}`}>validation #{d.state.report_id}</TextLink></span>}
      </div>

      {d.emergency_stop.active && (
        <Card title="Emergency stop" className="mb-4">
          <p className="mb-3 text-sm">Engaged by {d.emergency_stop.actor} at {utc(d.emergency_stop.at)}: {d.emergency_stop.reason}</p>
          <ResetEmergencyForm />
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Requirements (all must pass)">
          <ul className="space-y-1.5 text-sm">
            {d.requirements.map((r) => (
              <li key={r.requirement} className="flex flex-wrap gap-x-2">
                <span className={r.passed ? "text-pos" : "text-neg"} aria-label={r.passed ? "passed" : "failed"}>{r.passed ? "✓" : "✗"}</span>
                <span>{r.requirement}</span>{r.detail && <span className="text-xs text-dim">— {r.detail}</span>}
              </li>
            ))}
          </ul>
        </Card>
        <Card title={enabled ? "Demo trading is on" : "Enable demo trading"}>
          {enabled ? (
            <div className="space-y-3 text-sm">
              <p className="text-dim">The runner (<code>scripts\run_demo.bat</code>) acts on each newly closed bar. Every order re-checks the account, limits and quote first.</p>
              {d.state.last_cycle && <p className="text-xs text-faint">Last cycle: {JSON.stringify(d.state.last_cycle)}</p>}
              <div className="flex gap-2">
                <SimpleAction path="demo/disable" label="Disable demo trading" confirmText="Stop opening new demo trades? Open positions stay with their stop-loss and take-profit." />
                <SimpleAction path="demo/reconcile" label="Reconcile with MT5 now" />
              </div>
            </div>
          ) : <EnableDemoForm reports={d.passed_reports} confirmation={d.confirmation_text} />}
          <div className="mt-4 border-t border-line pt-3">
            <Kv rows={Object.entries(d.limits).map(([k, v]) => [k.replace("demo_", "").replace(/_/g, " "), String(v)])} />
            <p className="mt-2 text-xs text-faint">Limits are set in backend\.env (engineering defaults, not proven optimal values).</p>
          </div>
        </Card>
      </div>

      <Card title="Open demo positions (read from MT5, Trade1's magic number only)" className="mt-4">
        {d.state.positions_error ? <ErrorBox error={d.state.positions_error} /> : !d.positions.length ? <EmptyState title="No open demo positions" /> : (
          <Table head={["Ticket", "Symbol", "Side", "Lots", "Open", "Stop", "Target", "Profit", "Swap"]} dense>
            {d.positions.map((p) => (
              <tr key={p.ticket}><Td>{p.ticket}</Td><Td>{p.symbol}</Td><Td>{p.type === 0 ? "buy" : "sell"}</Td><Td>{p.volume}</Td>
                <Td>{price(p.price_open)}</Td><Td>{price(p.sl)}</Td><Td>{price(p.tp)}</Td>
                <Td className={signClass(p.profit)}>{num(p.profit)}</Td><Td>{num(p.swap)}</Td></tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Reconciliation events" className="mt-4">
        {!d.reconciliation.length ? <EmptyState title="No reconciliation events" /> : (
          <Table head={["#", "Time (UTC)", "Kind", "Details", "Status"]} dense>
            {d.reconciliation.map((e) => (
              <tr key={e.id}>
                <Td>{e.id}</Td><Td className="text-dim">{utc(e.created_at)}</Td><Td className="font-mono text-xs">{e.kind}</Td>
                <Td className="max-w-md whitespace-normal text-xs text-dim">{JSON.stringify(e.details)}</Td>
                <Td>{e.resolved ? <span className="text-xs text-pos">resolved: {e.resolution}</span> : <ResolveEventForm id={e.id} />}</Td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-2 text-xs text-faint">An unresolved event means Trade1 is not sure what the broker holds. New entries stay blocked until you check MT5 and resolve it (or “Reconcile with MT5 now” settles it automatically).</p>
      </Card>

      <Card title="Order log (latest 50)" className="mt-4">
        {!d.orders.length ? <EmptyState title="No demo orders yet" /> : (
          <div className="max-h-[32rem] overflow-y-auto">
            <Table head={["#", "Time (UTC)", "Kind", "Symbol", "Side", "Lots", "Requested", "Filled", "Slippage", "Stop", "Target", "Status", "Reason"]} dense>
              {d.orders.map((o) => (
                <tr key={o.id}>
                  <Td>{o.id}</Td><Td className="text-dim">{utc(o.created_at)}</Td><Td>{o.kind}</Td><Td>{o.symbol}</Td><Td>{o.side}</Td>
                  <Td>{o.volume}</Td><Td>{price(o.requested_price)}</Td><Td>{price(o.fill_price)}</Td><Td>{num(o.slippage_points, 1)}</Td>
                  <Td>{price(o.stop_loss)}</Td><Td>{price(o.take_profit)}</Td>
                  <Td><Badge tone={statusTone(o.status === "filled" ? "ok" : o.status === "rejected" ? "warning" : o.status === "uncertain" ? "failed" : o.status)}>{o.status}</Badge></Td>
                  <Td className="max-w-md truncate text-xs text-dim">{o.reason ?? "—"}</Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </>
  );
}
