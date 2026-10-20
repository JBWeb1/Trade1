import Link from "next/link";

import { CandleChart, type Candle } from "@/components/charts/CandleChart";
import { Badge, Card, EmptyState, ErrorBox, Kv, PageHeader } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { num, utc } from "@/lib/format";
import type { Coverage } from "@/lib/types";

export const dynamic = "force-dynamic";

type Spec = Record<string, unknown> & { issues: string[] };

export default async function MarketDetail({ params, searchParams }: {
  params: Promise<{ symbol: string }>; searchParams: Promise<{ tf?: string }>;
}) {
  const symbol = decodeURIComponent((await params).symbol);
  const { tf: tfParam } = await searchParams;
  const [detail, cov] = await Promise.all([
    tryApi<{ items: { server: string; spec: Spec; issues: string[]; metadata_ok: boolean }[] }>(`/api/markets/${encodeURIComponent(symbol)}`),
    tryApi<{ items: Coverage[] }>("/api/market-data/coverage"),
  ]);
  if (detail.error) return (<><PageHeader title={symbol} /><ErrorBox error={detail.error} /></>);

  const item = detail.data!.items[0];
  const s = item.spec;
  const stored = (cov.data?.items ?? []).filter((c) => c.symbol === symbol);
  const tf = stored.find((c) => c.timeframe === tfParam)?.timeframe ?? stored[0]?.timeframe;
  const bars = tf ? await tryApi<{ bars: Candle[]; count: number }>(
    `/api/market-data?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}&limit=2000`) : null;

  const v = (k: string) => (s[k] === null || s[k] === undefined || s[k] === "" ? "—" : String(s[k]));
  return (
    <>
      <PageHeader title={symbol} subtitle={<>{v("description")} · <span className="capitalize">{v("category")}</span> · {item.server}</>}
        actions={item.metadata_ok ? <Badge tone="good">Metadata OK</Badge> : <Badge tone="warn">Flagged</Badge>} />

      <Card title="Price chart (stored MT5 bid bars, UTC)" className="mb-4"
        right={<div className="flex gap-1">{stored.map((c) => (
          <Link key={c.timeframe} href={`?tf=${c.timeframe}`}
            className={`rounded px-2 py-0.5 text-xs ${c.timeframe === tf ? "bg-panel-2 text-fg" : "text-dim"}`}>{c.timeframe}</Link>
        ))}</div>}>
        {!tf ? (
          <EmptyState title="No history stored for this symbol">Download it on the Historical data page.</EmptyState>
        ) : bars?.error ? <ErrorBox error={bars.error} /> : (
          <>
            <CandleChart candles={bars?.data?.bars ?? []} />
            <p className="mt-2 text-xs text-faint">Showing the latest {bars?.data?.count} {tf} bars. Gaps (weekends, session breaks) are real: no bars are invented.</p>
          </>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Contract specification">
          <Kv rows={[
            ["Digits / point", `${v("digits")} / ${v("point")}`],
            ["Tick size / tick value", `${v("tick_size")} / ${v("tick_value")} ${v("account_currency")}`],
            ["Contract size", v("contract_size")],
            ["Volume min / max / step", `${v("volume_min")} / ${v("volume_max")} / ${v("volume_step")}`],
            ["Currencies (base / profit / margin)", `${v("currency_base")} / ${v("currency_profit")} / ${v("currency_margin")}`],
            ["Account currency", v("account_currency")],
            ["Needs conversion", s.needs_conversion ? "yes (profit currency differs)" : "no"],
            ["Calculation mode", v("calc_mode")],
          ]} />
        </Card>
        <Card title="Trading conditions">
          <Kv rows={[
            ["Trade mode", v("trade_mode")],
            ["Execution mode", v("execution_mode")],
            ["Filling modes", (s.filling_modes as string[] | undefined)?.join(", ") || "—"],
            ["Order types", (s.order_modes as string[] | undefined)?.join(", ") || "—"],
            ["Stops / freeze level (points)", `${v("stops_level")} / ${v("freeze_level")}`],
            ["Spread (points, at discovery)", `${v("spread_points")}${s.spread_floating ? " floating" : ""}`],
            ["Swap long / short", `${v("swap_long")} / ${v("swap_short")} (${v("swap_mode")})`],
            ["Margin initial / maintenance", `${num(s.margin_initial as number)} / ${num(s.margin_maintenance as number)}`],
            ["Commission", "not provided by MT5 — set it in the backtest"],
            ["Session hours", "not provided by the MT5 Python API — inferred from bar gaps"],
          ]} />
        </Card>
      </div>

      {item.issues.length > 0 && (
        <Card title="Why this symbol is flagged" className="mt-4">
          <ul className="list-inside list-disc text-sm text-warn">{item.issues.map((i) => <li key={i}>{i}</li>)}</ul>
        </Card>
      )}
      {stored.length > 0 && (
        <p className="mt-3 text-xs text-faint">Stored: {stored.map((c) => `${c.timeframe} ${c.bars.toLocaleString()} bars (${utc(c.first, false)} → ${utc(c.last, false)})`).join(" · ")}</p>
      )}
    </>
  );
}
