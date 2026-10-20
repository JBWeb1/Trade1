import { Card, Kv } from "@/components/ui";
import { num, pct } from "@/lib/format";
import type { MonteCarlo } from "@/lib/types";

export function MonteCarloCard({ mc, title = "Monte Carlo: what these trades could have looked like" }: { mc?: MonteCarlo | null; title?: string }) {
  return (
    <Card title={title}>
      {!mc ? <p className="text-sm text-dim">Not enough trades (needs at least 10).</p> : (
        <>
          <Kv rows={[
            ["Max drawdown: typical / bad (95%) / very bad (99%)",
              `${num(mc.max_drawdown_pct.p50)}% / ${num(mc.max_drawdown_pct.p95)}% / ${num(mc.max_drawdown_pct.p99)}%`],
            ["Final return: 5th / median / 95th percentile",
              `${num(mc.final_return_pct.p5)}% / ${num(mc.final_return_pct.p50)}% / ${num(mc.final_return_pct.p95)}%`],
            ["Longest losing streak: typical / bad (95%)", `${mc.longest_losing_streak.p50} / ${mc.longest_losing_streak.p95} trades`],
            ["Chance of ending with a loss", pct(mc.probability_of_loss, 1, true)],
          ]} />
          <p className="mt-3 text-xs text-faint">{mc.simulations.toLocaleString()} reshuffles of {mc.trades_per_simulation} trades at {mc.risk_pct}% risk.
            {" "}{mc.assumptions}. Plan for the bad case, not the one history happened to produce.</p>
        </>
      )}
    </Card>
  );
}
