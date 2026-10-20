// Shapes returned by the Trade1 backend (only the fields the dashboard uses).

export type Health = { status: string; database: boolean; version: string; execution_mode: string; live_trading: string };

export type Verification = {
  verified_demo: boolean; account_mode: string; login: number | null; server: string | null;
  company: string | null; currency: string | null; reasons: string[]; warnings: string[];
};

export type Mt5Status = {
  connected: boolean; error?: string; terminal_trade_allowed?: boolean; ping_ms?: number; build?: number;
  verification: Verification;
};

export type Instrument = {
  symbol: string; server: string; category: string; subcategory: string | null; description: string | null;
  metadata_ok: boolean; issues: string[]; updated_at: string;
};

export type Coverage = { symbol: string; timeframe: string; bars: number; first: string; last: string };

export type IngestionRun = {
  id: number; symbol: string; timeframe: string; requested_start: string; requested_end: string;
  started_at: string; finished_at: string | null; status: string; bars_received: number; bars_valid: number;
  bars_inserted: number; bars_already_present: number; first_bar: string | null; last_bar: string | null;
  quality: { notes?: string[]; gaps_unexpected?: number; duplicates_removed?: number; invalid_rows?: number;
    stale?: boolean; history_starts_late_by_days?: number };
  error: string | null;
};

export type Headline = {
  closed_trades: number | null; net_return_pct: number | null; win_rate: number | null;
  expectancy_per_trade: number | null; profit_factor: number | null; max_drawdown_pct: number | null;
  sample_warning: string | null;
};

export type BacktestSummary = {
  id: number; status: string; error: string | null; result_kind: string; symbol: string; timeframe: string;
  htf: string | null; start: string; end: string; strategy_version_id: number; started_at: string;
  finished_at: string | null; headline: Headline;
};

export type EquityPoint = { time: string; equity: number; balance: number };

export type Breakdown = Record<string, { trades: number; net: number; expectancy?: number }>;

export type Metrics = {
  label?: string; closed_trades: number; win_rate: number | null; average_win: number | null;
  average_loss: number | null; expectancy_per_trade: number | null; expectancy_r: number | null;
  profit_factor: number | null; net_profit: number | null; net_return_pct: number | null;
  max_drawdown_pct?: number | null; max_drawdown_money?: number | null; max_drawdown_duration_days?: number | null;
  avg_drawdown_duration_days?: number | null; sharpe_daily_annualised?: number | null;
  longest_losing_streak: number; exposure_pct: number | null; turnover_lots: number | null;
  costs: Record<string, number | null>; exit_reasons: Record<string, number>; rejections: Record<string, number>;
  by_month: Breakdown; by_side: Breakdown; by_regime?: Breakdown; sample_warning: string | null; notes: string[];
  strategy?: { name: string; version: string; params: Record<string, unknown>; params_hash: string };
  final_equity?: number;
};

export type BacktestDetail = BacktestSummary & {
  config: { starting_balance: number; account_currency: string; leverage: number;
    costs: Record<string, unknown>; risk: Record<string, unknown> };
  data_fingerprint: Record<string, unknown>; equity_curve: EquityPoint[];
};

export type Trade = {
  id: number; side: string; signal_time: string; entry_time: string; exit_time: string | null;
  entry_price: number; exit_price: number | null; volume: number; stop_loss: number; take_profit: number;
  exit_reason: string; net_pnl: number; gross_pnl: number; commission: number; swap: number;
  spread_cost: number; slippage_cost: number; planned_risk_money: number; bars_held: number;
  ambiguous_bar: boolean; regime: string | null; rationale: string;
};

export type SegmentSummary = {
  closed_trades: number; win_rate: number | null; expectancy_per_trade: number | null; expectancy_r: number | null;
  profit_factor: number | null; net_return_pct: number | null; max_drawdown_pct: number | null;
  sharpe_daily_annualised: number | null; longest_losing_streak: number; first_bar_close?: string;
  last_bar_close?: string;
};

export type Check = { check: string; passed: boolean; detail: string; kind: string };

export type ValidationSummary = {
  id: number; status: string; verdict: "PASSED" | "FAILED" | "INCONCLUSIVE" | null; reasons: string[];
  error: string | null; symbol: string; timeframe: string; htf: string | null; start: string; end: string;
  strategy_version_id: number; backtest_run_id: number | null; oos_prior_uses: number; created_at: string;
};

export type ValidationReportBody = {
  verdict: string; reasons: string[]; checks: Check[];
  splits: { start: string; development_end: string; validation_end: string; end: string };
  segments: { development: SegmentSummary; validation: SegmentSummary; out_of_sample: SegmentSummary };
  oos_t_stat: number | null;
  walk_forward: { folds: { fold: number; in_sample: string[]; out_of_sample: string[];
    chosen_params: Record<string, unknown>; in_sample_expectancy_r: number | null; oos: SegmentSummary }[];
    oos_trades: number; oos_expectancy_r: number | null; positive_fraction: number | null; grid_params: string[] };
  sensitivity: { step: number; positive_fraction: number | null;
    runs: { param: string; value: unknown; closed_trades: number; expectancy_r: number | null; profit_factor: number | null }[] };
  disclaimer: string; strategy?: { name: string; version: string; params_hash: string; params: Record<string, unknown> };
};

export type ValidationDetail = ValidationSummary & { report: ValidationReportBody; data_fingerprint: Record<string, unknown> };

export type StrategyInfo = {
  available: { name: string; version: string; description: string; params_schema: { properties: Record<string, { default?: unknown; type?: string; minimum?: number; maximum?: number }> } }[];
  versions: { id: number; name: string; version: string; params: Record<string, unknown>; params_hash: string; created_at: string }[];
};

export type Diagnostics = {
  connection: { connected: boolean; error?: string };
  verification: Verification;
  estimated_server_utc_offset_hours: number | null;
  configured_server_utc_offset_hours: number;
  problems: string[];
  flagged_instruments: { symbol: string; issues: string[] }[];
  recent_problem_ingestions: { id: number; symbol: string; timeframe: string; status: string; error: string | null; notes: string[] }[];
};

export type NewsEvent = { provider: string; event_time: string; currency: string; title: string; impact: string; forecast: string | null; previous: string | null };
