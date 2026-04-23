# Methodology

All strategies run in a simulated fill engine (`sim_engine.py`) against EOD
close prices (or hourly close where noted). Fills are simulated at the
benchmark close with a 5 bp one-way transaction cost unless overridden.

## Data sources

| Source | Used for | Cache |
|--------|----------|-------|
| `yfinance` (Yahoo) | SPY, QQQ, GLD, TLT, SVXY, VIX, VVIX, SKEW, MOVE, GVZ, DXY | 6h parquet |
| `ccxt` (Binance spot) | 20 crypto pairs, daily OHLCV | 6h parquet |
| `cboe_vx_scraper.py` | VX1 delayed-quote scrape (CBOE website) | 30 min JSON |
| `FRED` (fallback) | VIX, GVZ, DXY fallback when Yahoo fails | 24h parquet |
| Local Barchart CSV | VX1 hourly history for VIX/VX1 ratio | static file |

Raw responses for every request are committed under `raw_prices/<date>/` as
JSON. An auditor can reproduce signals independently by re-parsing those
files.

## Cost model

Every fill pays `cost_bps_one_way` bps slippage (default 5 bps each side).
No borrow fees modeled. Weekly options assume mid-price using
Black-Scholes with a fallback IV of 18% and risk-free rate 4.5% when market
IV is unavailable.

## Strategies

### 1. `four_way` (multi-leg portfolio)

Four uncorrelated legs sized by fixed weights:

| Leg | Weight | Freq | Instrument | Signal |
|-----|--------|------|------------|--------|
| SVXY-VRP | 45 % | hourly | SVXY | VIX/VX1 ratio < 0.975 AND SKEW z < 1.0 AND DXY mom < 1.5 % AND RSP mom > −1 % AND SPY-SVXY corr(14d) > 0.3 |
| GLD-GVZ | 25 % | daily | GLD | GVZ < 18 → long |
| TLT-MOVE | 15 % | daily | TLT | MOVE < 80 → long, > 100 → short |
| TSMOM-macro | 15 % | monthly | SPY / TLT / GLD / DXY | 12-month total return sign, inverse-vol weights |

SVXY is SHIFT(1) on VIX/VX1 ratio per the Barchart look-ahead fix.
Volmageddon scaling (×0.5 pre-2018-02-05) applies historically but is
immaterial going forward.

### 2. `vw_tsmom_crypto`

Universe: BTC, ETH, LTC, BNB (USDT pairs on Binance).
Signal: 252-day total return → sign.
Sizing: inverse 60-day volatility, target 10 % portfolio vol, cap 5 × per
leg.
Long/short, rebalance daily.

### 3. `donchian_crypto`

Universe: 20 major crypto/USDT pairs.
Entry: close crosses above prior 55-day high → long (below 55-day low →
short).
Exit: opposite 20-day channel.
Sizing: inverse 60-day vol, 10 % target, 3× leverage cap.

### 4. `vvix_spy_reversal`

Universe: SPY.
Signal: VVIX close > 120 → long next day.
Exit: 5 business days.
Max 1 open position.

### 5. `overnight_qqq_fri_skip`

Buy QQQ at close, sell at next open — every trading day except Friday close
(holding over weekends historically underperforms).

### 6. `weekly_spy_spread`

ATM bull call spread on SPY weekly options.
Width: 2 % OTM short strike.
Sizing: 1 % of capital base, Martingale (double on loss) with cap 3
consecutive doubles, reset on win.

Black-Scholes pricing with risk-free 4.5 %, fallback IV 18 % when market IV
missing.

## Known limitations

- Yahoo adjusted close used for all ETFs / indices; vendor may silently
  revise historical splits.
- Binance data is spot; if an exchange credentials or counterparty issue
  removes a pair the universe auto-shrinks — logged but not fatal.
- VX1 relies on a local Barchart CSV (historic) plus CBOE delayed-quote
  scrape (live). A disaster recovery plan would be to switch to the CBOE
  data-feed API.
- Weekend and holiday skip: equity strategies skip NYSE holidays per the
  baked-in list in `data_sources.py` (updated yearly).
- Options pricing is a model, not traded fills — the weekly SPY spread
  strategy's P&L is theoretical BS-mid, not a broker-printed confirm.

## Version history

| Date | Change |
|------|--------|
| 2026-04-23 | Track record initialized. 6 strategies enabled. |
