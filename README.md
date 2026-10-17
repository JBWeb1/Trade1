# Trade1 — Liquidity-Sweep Bot with Quiver Quant + MetaTrader 5

An automated trading system that:

1. **Pulls alternative data from [Quiver Quant](https://www.quiverquant.com/)**: US Congress
   trades and corporate insider trades. It turns them into a bias score from −1 to +1 for each ticker.
2. **Finds liquidity sweeps** in price: price wicks through a recent swing high or low (where
   stop orders sit), then closes back inside the range. That is a common reversal setup.
3. **Takes the trade only when the sweep agrees with the Quiver bias**. For example, a bullish sweep
   on a stock that politicians and insiders have been buying.
4. **Sizes the position from your risk %** and sends the order, with stop loss and take
   profit, **to MetaTrader 5**.

```
Quiver Quant API ──► bias per ticker ─┐
                                      ├─► signal ─► risk manager ─► MT5 order (SL/TP)
MT5 price bars ───► liquidity sweep ──┘
```

## Requirements

* **Windows** (or a Windows VPS). The official `MetaTrader5` Python package works only there.
* The MetaTrader 5 terminal, installed and logged in to your broker, with **Algo Trading** turned on.
  Your broker must offer the US stocks or stock CFDs you want to trade.
* Python 3.10+
* A Quiver Quant API token (from your account page; the API needs a paid tier).

## Setup

```bash
pip install -r requirements.txt
copy config.example.yaml config.yaml
copy .env.example .env          # then put your QUIVER_API_TOKEN in .env
```

In `config.yaml`, map each Quiver ticker to the name your broker uses in MT5's Market
Watch. Brokers name stocks differently: `AAPL`, `AAPL.US`, `#AAPL`, `AAPL.NAS`, ...

## Usage

```bash
python -m trade1 bias    # print the Quiver Quant bias table
python -m trade1 scan    # one pass over all symbols on real MT5 prices, paper fills only
python -m trade1 run     # run continuously; mode in config.yaml decides paper vs live
```

`mode: paper` (the default) reads real prices from MT5 but simulates every fill.
Switch to `mode: live` only after you've watched it on a **demo account**.

## Key settings

| Setting | Meaning |
|---|---|
| `strategy.bias_mode` | `require`: trade only in the Quiver direction. `boost`: block only trades against it. `ignore`: price action only. |
| `strategy.reward_risk` | Take-profit as a multiple of the risk; capped at the opposite liquidity pool. |
| `risk.risk_per_trade_pct` | % of equity lost if the stop is hit. |
| `risk.max_daily_loss_pct` | Stops opening new trades for the day after this drawdown. |
| `auto_universe.enabled` | Adds the tickers with the strongest Quiver bias, if your broker lists them. |
| `mt5.magic` | Tags the bot's orders. The bot never touches your manual trades. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Project layout

```
trade1/quiver.py    Quiver Quant client + bias scoring
trade1/strategy.py  liquidity-sweep detection + liquidity/volatility filters
trade1/risk.py      position sizing, daily loss limit, spread filter
trade1/broker.py    MT5Broker (live) and PaperBroker (simulated)
trade1/engine.py    main loop
```

> **Disclaimer:** This is software, not financial advice. Automated trading can lose money
> quickly. Test on a demo account first.
