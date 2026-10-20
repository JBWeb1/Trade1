export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1, isFraction = false): string {
  if (v === null || v === undefined) return "—";
  return `${num(isFraction ? v * 100 : v, digits)}%`;
}

export function money(v: number | null | undefined, ccy = ""): string {
  if (v === null || v === undefined) return "—";
  const s = num(v, 2);
  return ccy ? `${s} ${ccy}` : s;
}

/** UTC timestamp, minute precision: 2025-03-07 13:30 UTC */
export function utc(v: string | null | undefined, withTime = true): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  const iso = d.toISOString();
  return withTime ? `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC` : iso.slice(0, 10);
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-fg";
  return v > 0 ? "text-pos" : "text-neg";
}

/** Price without floating-point noise: up to 6 decimals, trailing zeros removed. */
export function price(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return String(Number(v.toFixed(6)));
}
