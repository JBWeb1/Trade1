"use client";

import { CandlestickSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type Candle = { time: string; open: number; high: number; low: number; close: number };

/** Candles of stored MT5 bid bars. Times are bar OPEN times in UTC. */
export function CandleChart({ candles }: { candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#121722" }, textColor: "#8a96aa", fontSize: 11 },
      grid: { vertLines: { color: "#1b2333" }, horzLines: { color: "#1b2333" } },
      rightPriceScale: { borderColor: "#232c3d" },
      timeScale: { borderColor: "#232c3d", timeVisible: true, secondsVisible: false },
      localization: { timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#3ecf8e", downColor: "#f2616b", borderVisible: false,
      wickUpColor: "#3ecf8e", wickDownColor: "#f2616b",
    });
    series.setData(candles.map((c) => ({
      time: Math.floor(new Date(c.time).getTime() / 1000) as UTCTimestamp,
      open: c.open, high: c.high, low: c.low, close: c.close,
    })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles]);

  return <div ref={ref} className="h-96 w-full" role="img" aria-label="Candlestick chart of stored bars" />;
}
