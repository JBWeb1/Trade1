import { NextRequest, NextResponse } from "next/server";

import { BackendError, backendFetch } from "@/lib/backend";

/**
 * Browser -> dashboard server -> backend. The token is added here, server-side.
 * Only reviewed routes are forwarded. There is no order route to forward.
 * Requests must come from the dashboard itself (same origin + custom header),
 * so another website open in the browser cannot trigger actions.
 */
const ALLOWED_POST: RegExp[] = [
  /^markets\/sync$/,
  /^market-data\/ingest$/,
  /^backtests$/,
  /^validation\/\d+$/,
  /^strategies$/,
  /^news\/refresh$/,
  /^research\/multi-market$/,
  /^research\/portfolio$/,
  /^research\/tick-check\/\d+$/,
  /^demo\/enable$/,
  /^demo\/disable$/,
  /^demo\/reconcile$/,
  /^demo\/reconciliation\/\d+\/resolve$/,
  /^system\/emergency-stop$/,
  /^system\/emergency-reset$/,
];
const ALLOWED_GET_PREFIXES = [
  "health", "mt5/status", "markets", "market-data", "backtests", "validation", "strategies",
  "news/events", "news/blackout", "diagnostics", "research", "audit", "settings", "demo/status",
];

function sameOrigin(req: NextRequest): boolean {
  if (req.headers.get("x-trade1-client") !== "dashboard") return false;
  const origin = req.headers.get("origin");
  if (!origin) return true; // same-origin GET requests may omit Origin
  const host = req.headers.get("host");
  try {
    return !!host && new URL(origin).host === host;
  } catch {
    return false;
  }
}

async function forward(req: NextRequest, parts: string[], method: "GET" | "POST") {
  const sub = parts.join("/");
  if (!/^[\w\-/.]+$/.test(sub) || sub.includes("..")) {
    return NextResponse.json({ detail: "invalid path" }, { status: 400 });
  }
  const allowed = method === "GET"
    ? ALLOWED_GET_PREFIXES.some((p) => sub === p || sub.startsWith(`${p}/`))
    : ALLOWED_POST.some((r) => r.test(sub));
  if (!allowed) return NextResponse.json({ detail: "route not allowed" }, { status: 403 });
  if (!sameOrigin(req)) return NextResponse.json({ detail: "cross-origin request refused" }, { status: 403 });

  try {
    const res = await backendFetch(`/api/${sub}${req.nextUrl.search}`, {
      method,
      headers: method === "POST" ? { "Content-Type": "application/json" } : undefined,
      body: method === "POST" ? await req.text() : undefined,
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch (e) {
    const status = e instanceof BackendError ? e.status : 502;
    return NextResponse.json({ detail: e instanceof Error ? e.message : "backend error" }, { status });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path, "GET");
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path, "POST");
}
