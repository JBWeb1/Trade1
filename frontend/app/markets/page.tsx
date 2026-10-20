import Link from "next/link";

import { ActionButton } from "@/components/Actions";
import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td, TextLink } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import type { Instrument } from "@/lib/types";

export const dynamic = "force-dynamic";

const CATEGORIES = ["forex", "metals", "energy", "indices", "stocks", "crypto", "commodities", "other"];

export default async function Markets({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const qs = new URLSearchParams({ limit: "5000" });
  if (sp.category && CATEGORIES.includes(sp.category)) qs.set("category", sp.category);
  if (sp.search) qs.set("search", sp.search.slice(0, 64));
  if (sp.ok === "true" || sp.ok === "false") qs.set("metadata_ok", sp.ok);
  const res = await tryApi<{ items: Instrument[] }>(`/api/markets?${qs}`);
  const all = await tryApi<{ items: Instrument[] }>("/api/markets?limit=5000");
  const counts: Record<string, number> = {};
  for (const i of all.data?.items ?? []) counts[i.category] = (counts[i.category] ?? 0) + 1;
  const items = res.data?.items ?? [];

  const link = (p: Record<string, string | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries({ category: sp.category, search: sp.search, ok: sp.ok, ...p })) if (v) q.set(k, v);
    return `/markets?${q}`;
  };

  return (
    <>
      <PageHeader title="Markets" subtitle="Instruments your Exness MT5 account actually offers, with contract specifications."
        actions={<ActionButton path="markets/sync" label="Re-discover symbols" busyLabel="Discovering…" />} />

      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link href={link({ category: undefined })} className={`rounded-md border px-2.5 py-1 ${!sp.category ? "border-accent text-fg" : "border-line text-dim"}`}>
          All <span className="text-faint">{all.data?.items.length ?? 0}</span>
        </Link>
        {CATEGORIES.filter((c) => counts[c]).map((c) => (
          <Link key={c} href={link({ category: c })} className={`rounded-md border px-2.5 py-1 capitalize ${sp.category === c ? "border-accent text-fg" : "border-line text-dim"}`}>
            {c} <span className="text-faint">{counts[c]}</span>
          </Link>
        ))}
        <form className="ml-auto flex gap-2" action="/markets">
          {sp.category && <input type="hidden" name="category" value={sp.category} />}
          <label className="sr-only" htmlFor="search">Search symbol</label>
          <input id="search" name="search" defaultValue={sp.search} placeholder="Search symbol" maxLength={64}
            className="rounded-md border border-line bg-bg px-2.5 py-1 text-sm" />
          <select name="ok" defaultValue={sp.ok ?? ""} aria-label="Metadata status" className="rounded-md border border-line bg-bg px-2 py-1 text-sm">
            <option value="">any status</option><option value="true">metadata OK</option><option value="false">flagged</option>
          </select>
          <button className="rounded-md border border-line px-2.5 py-1 text-sm">Filter</button>
        </form>
      </div>

      <Card>
        {res.error ? <ErrorBox error={res.error} /> : items.length === 0 ? (
          <EmptyState title={all.data?.items.length ? "No instrument matches these filters" : "No instruments discovered yet"}>
            {all.data?.items.length ? "Change the filters above." : "With MT5 open and logged in, click “Re-discover symbols”."}
          </EmptyState>
        ) : (
          <Table head={["Symbol", "Category", "Description", "Status", "Issues"]} dense>
            {items.map((i) => (
              <tr key={`${i.server}-${i.symbol}`}>
                <Td><TextLink href={`/markets/${encodeURIComponent(i.symbol)}`}>{i.symbol}</TextLink></Td>
                <Td className="capitalize">{i.category}{i.subcategory ? <span className="text-faint"> · {i.subcategory}</span> : null}</Td>
                <Td className="max-w-xs truncate text-dim">{i.description ?? "—"}</Td>
                <Td>{i.metadata_ok ? <Badge tone="good">OK</Badge> : <Badge tone="warn">Flagged</Badge>}</Td>
                <Td className="max-w-md truncate text-dim">{i.issues.join("; ") || "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <p className="mt-3 text-xs text-faint">Flagged instruments have missing or contradictory specifications. They can be backtested where the data allows, but demo orders on them will be blocked.</p>
    </>
  );
}
