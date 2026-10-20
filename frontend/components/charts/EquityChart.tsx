"use client";

import { Area, AreaChart, CartesianGrid, Line, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { EquityPoint } from "@/lib/types";

const AXIS = { stroke: "#5d6880", fontSize: 11 };

function day(t: string) {
  return t.slice(0, 10);
}

export function EquityChart({ points, currency }: { points: EquityPoint[]; currency: string }) {
  return (
    <div className="h-72 w-full" role="img" aria-label="Equity and balance over time">
      <ResponsiveContainer>
        <ComposedChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#232c3d" vertical={false} />
          <XAxis dataKey="time" tickFormatter={day} minTickGap={60} tick={AXIS} stroke="#232c3d" />
          <YAxis domain={["auto", "auto"]} tick={AXIS} stroke="#232c3d" width={70}
            tickFormatter={(v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 })} />
          <Tooltip
            contentStyle={{ background: "#121722", border: "1px solid #232c3d", fontSize: 12 }}
            labelFormatter={(l) => `${String(l).slice(0, 16).replace("T", " ")} UTC`}
            formatter={(v, name) => [`${Number(v).toFixed(2)} ${currency}`, name === "equity" ? "Equity (marked)" : "Balance (closed)"]}
          />
          <Line type="monotone" dataKey="balance" stroke="#8a96aa" dot={false} strokeWidth={1} isAnimationActive={false} />
          <Line type="monotone" dataKey="equity" stroke="#4f9cf7" dot={false} strokeWidth={1.5} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DrawdownChart({ points }: { points: EquityPoint[] }) {
  let peak = -Infinity;
  const data = points.map((p) => {
    peak = Math.max(peak, p.equity);
    return { time: p.time, dd: peak > 0 ? -((peak - p.equity) / peak) * 100 : 0 };
  });
  return (
    <div className="h-40 w-full" role="img" aria-label="Drawdown from the running equity peak, percent">
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#232c3d" vertical={false} />
          <XAxis dataKey="time" tickFormatter={day} minTickGap={60} tick={AXIS} stroke="#232c3d" />
          <YAxis tick={AXIS} stroke="#232c3d" width={70} tickFormatter={(v: number) => `${v.toFixed(1)}%`} />
          <Tooltip
            contentStyle={{ background: "#121722", border: "1px solid #232c3d", fontSize: 12 }}
            labelFormatter={(l) => `${String(l).slice(0, 16).replace("T", " ")} UTC`}
            formatter={(v) => [`${Number(v).toFixed(2)}%`, "Drawdown"]}
          />
          <Area type="monotone" dataKey="dd" stroke="#f2616b" fill="#f2616b" fillOpacity={0.15} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
