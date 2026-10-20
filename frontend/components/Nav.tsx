"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS: { title: string; items: { href: string; label: string }[] }[] = [
  { title: "Monitor", items: [
    { href: "/", label: "Overview" },
    { href: "/health", label: "System health" },
  ] },
  { title: "Markets & data", items: [
    { href: "/markets", label: "Markets" },
    { href: "/data", label: "Historical data" },
    { href: "/news", label: "News calendar" },
  ] },
  { title: "Research", items: [
    { href: "/strategies", label: "Strategies" },
    { href: "/backtests", label: "Backtests" },
    { href: "/validation", label: "Validation" },
    { href: "/research", label: "Research studies" },
  ] },
  { title: "Execution", items: [
    { href: "/demo", label: "Demo automation" },
  ] },
  { title: "System", items: [
    { href: "/settings", label: "Settings & audit" },
  ] },
];

export function Nav() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {SECTIONS.map((s) => (
        <div key={s.title}>
          <div className="px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-faint">{s.title}</div>
          <ul>
            {s.items.map((i) => (
              <li key={i.href}>
                <Link
                  href={i.href}
                  aria-current={active(i.href) ? "page" : undefined}
                  className={`block rounded-md px-3 py-1.5 text-sm ${active(i.href) ? "bg-panel-2 text-fg" : "text-dim hover:bg-panel-2/60 hover:text-fg"}`}
                >
                  {i.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
