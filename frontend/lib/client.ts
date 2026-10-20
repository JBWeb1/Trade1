"use client";

/** Browser-side call to the dashboard's own proxy (which adds the API token server-side). */
export async function proxyPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/proxy/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Trade1-Client": "dashboard" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail as
      string | { message?: string; requirements?: { requirement: string; passed: boolean; detail: string }[] } | undefined;
    if (detail && typeof detail === "object" && detail.requirements) {
      const failed = detail.requirements.filter((r) => !r.passed).map((r) => `✗ ${r.requirement}${r.detail ? ` — ${r.detail}` : ""}`);
      throw new Error([detail.message ?? "refused", ...failed].join("\n"));
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? data));
  }
  return data as T;
}
