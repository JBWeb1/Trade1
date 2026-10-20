import { NextRequest, NextResponse } from "next/server";

import { BackendError, backendFetch } from "@/lib/backend";

/** Read-only download of a backtest's trade journal (CSV or JSON), fetched server-side. */
export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const format = req.nextUrl.searchParams.get("format") === "json" ? "json" : "csv";
  if (!/^\d+$/.test(id)) return NextResponse.json({ detail: "invalid id" }, { status: 400 });
  try {
    const res = await backendFetch(`/api/backtests/${id}/journal?format=${format}`);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
        "Content-Disposition": `attachment; filename="trade1_backtest_${id}_journal.${format}"`,
      },
    });
  } catch (e) {
    return NextResponse.json({ detail: e instanceof Error ? e.message : "error" },
      { status: e instanceof BackendError ? e.status : 502 });
  }
}
