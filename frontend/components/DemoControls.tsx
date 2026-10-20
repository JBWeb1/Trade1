"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { proxyPost } from "@/lib/client";

const input = "w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm focus:border-accent focus:outline-none";

function useAction() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(path: string, body?: unknown) {
    setBusy(true); setError(null);
    try {
      await proxyPost(path, body);
      router.refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

export function EmergencyStopButton() {
  const { busy, error, run } = useAction();
  return (
    <div>
      <button disabled={busy}
        className="rounded-md bg-neg px-4 py-2 text-sm font-semibold text-white hover:bg-neg/90 disabled:opacity-50"
        onClick={() => {
          if (window.confirm("EMERGENCY STOP: disable demo trading and cancel Trade1's pending demo orders now? Open positions keep their stop-loss and take-profit at the broker.")) {
            void run("system/emergency-stop", { reason: "dashboard emergency stop button" });
          }
        }}>{busy ? "Stopping…" : "EMERGENCY STOP"}</button>
      {error && <p role="alert" className="mt-2 text-xs text-neg">{error}</p>}
    </div>
  );
}

export function EnableDemoForm({ reports, confirmation }: {
  reports: { id: number; symbol: string; timeframe: string }[]; confirmation: string;
}) {
  const { busy, error, run } = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    await run("demo/enable", { report_id: Number(f.get("report_id")), confirmation: String(f.get("confirmation") ?? "") });
  }
  if (!reports.length) return <p className="text-sm text-dim">No PASSED validation report (with first-use out-of-sample data) exists, so demo trading cannot be enabled.</p>;
  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-dim">Validated strategy to forward-test</span>
        <select name="report_id" className={input}>
          {reports.map((r) => <option key={r.id} value={r.id}>Validation #{r.id}: {r.symbol} {r.timeframe}</option>)}
        </select>
      </label>
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-dim">Type exactly: <span className="font-mono text-fg">{confirmation}</span></span>
        <input name="confirmation" autoComplete="off" className={`${input} font-mono`} />
      </label>
      <button disabled={busy} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
        {busy ? "Checking every requirement…" : "Enable demo trading"}
      </button>
      {error && <pre role="alert" className="whitespace-pre-wrap text-xs text-neg">{error}</pre>}
    </form>
  );
}

export function SimpleAction({ path, label, body, confirmText }: { path: string; label: string; body?: unknown; confirmText?: string }) {
  const { busy, error, run } = useAction();
  return (
    <div>
      <button disabled={busy} className="rounded-md border border-line bg-panel-2 px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        onClick={() => { if (!confirmText || window.confirm(confirmText)) void run(path, body); }}>{busy ? "Working…" : label}</button>
      {error && <p role="alert" className="mt-1 text-xs text-neg">{error}</p>}
    </div>
  );
}

export function ResetEmergencyForm() {
  const { busy, error, run } = useAction();
  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
      e.preventDefault();
      void run("system/emergency-reset", { confirmation: String(new FormData(e.currentTarget).get("c") ?? "") });
    }}>
      <label className="text-sm"><span className="mb-1 block text-xs text-dim">Type RESET EMERGENCY STOP</span>
        <input name="c" autoComplete="off" className={`${input} w-64 font-mono`} /></label>
      <button disabled={busy} className="rounded-md border border-line px-3 py-1.5 text-sm">Reset</button>
      {error && <p role="alert" className="w-full text-xs text-neg">{error}</p>}
    </form>
  );
}

export function ResolveEventForm({ id }: { id: number }) {
  const { busy, error, run } = useAction();
  return (
    <form className="flex gap-2" onSubmit={(e) => {
      e.preventDefault();
      void run(`demo/reconciliation/${id}/resolve`, { resolution: String(new FormData(e.currentTarget).get("r") ?? "") });
    }}>
      <input name="r" required minLength={10} placeholder="What you checked in MT5 and did (10+ chars)"
        aria-label="Resolution" className={`${input} min-w-64`} />
      <button disabled={busy} className="rounded-md border border-line px-2 py-1 text-xs">Resolve</button>
      {error && <span role="alert" className="text-xs text-neg">{error}</span>}
    </form>
  );
}
