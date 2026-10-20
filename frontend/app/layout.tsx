import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "Trade1 Trading AI",
  description: "Research, backtesting and validation for an Exness MT5 demo account",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <div className="flex min-h-screen flex-col md:flex-row">
          <aside className="border-b border-line bg-panel md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:overflow-y-auto md:border-b-0 md:border-r">
            <div className="flex items-center gap-2 px-4 py-4">
              <div aria-hidden className="h-6 w-6 rounded bg-accent/20 text-center text-sm font-bold leading-6 text-accent">T1</div>
              <div>
                <div className="text-sm font-semibold">Trade1 Trading AI</div>
                <div className="text-[11px] text-faint">Demo-only · no live trading</div>
              </div>
            </div>
            <div className="px-2 pb-4"><Nav /></div>
          </aside>
          <main className="min-w-0 flex-1 px-4 py-6 md:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
