import "server-only";

import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Server-side access to the Trade1 FastAPI backend.
 * The bearer token is read from TRADE1_API_TOKEN, or from ../backend/.env,
 * and is only ever used on the server. It is never sent to the browser.
 */
const BASE_URL = process.env.TRADE1_API_URL ?? "http://127.0.0.1:8000";

let cachedToken: string | null | undefined;

function readToken(): string | null {
  if (cachedToken !== undefined) return cachedToken;
  let token = process.env.TRADE1_API_TOKEN ?? null;
  if (!token) {
    try {
      const envFile = readFileSync(path.join(process.cwd(), "..", "backend", ".env"), "utf8");
      const line = envFile.split(/\r?\n/).find((l) => l.startsWith("TRADE1_API_TOKEN="));
      token = line ? line.slice("TRADE1_API_TOKEN=".length).trim() || null : null;
    } catch {
      token = null;
    }
  }
  cachedToken = token;
  return token;
}

export class BackendError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function backendFetch(apiPath: string, init: RequestInit = {}): Promise<Response> {
  const token = readToken();
  if (!token) {
    throw new BackendError("No API token: set TRADE1_API_TOKEN in backend/.env (setup_windows.bat does this).", 503);
  }
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  try {
    return await fetch(`${BASE_URL}${apiPath}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new BackendError(`Cannot reach the Trade1 backend at ${BASE_URL}. Is it running (start_trade1.bat)?`, 503);
  }
}

/** GET JSON or throw BackendError with the backend's message. */
export async function api<T>(apiPath: string): Promise<T> {
  const res = await backendFetch(apiPath);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new BackendError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

/** Like api(), but returns {data} or {error} so a page can render partial results. */
export async function tryApi<T>(apiPath: string): Promise<{ data?: T; error?: string }> {
  try {
    return { data: await api<T>(apiPath) };
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}
