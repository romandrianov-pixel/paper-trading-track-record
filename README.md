# Paper-Trading Track Record

> Cryptographically timestamped, reproducible daily record of systematic paper
> trades across multiple uncorrelated strategies.

## What this repo is

Every day a runner in another repo executes 6 systematic trading strategies
(volatility, trend, mean-reversion across equities, crypto, options) and
commits the resulting state + raw market data + digests here. A weekly
OpenTimestamps job anchors the repo's commit hashes to the Bitcoin
blockchain.

**Any retroactive edit breaks the SHA256 hash-chain (`hashes.json`) and
invalidates the OpenTimestamps proof** — so an auditor can verify nothing was
back-dated or p-hacked into existence.

Track record start: **2026-04-23**.

## Claim

This repo records, cryptographically timestamps, and allows full reproduction
of every paper trade across **6 systematic strategies** starting 2026-04-23.
No look-ahead, no post-hoc editing — any retroactive change breaks the hash
chain.

## Strategies at a glance

| Strategy | Universe | Frequency | Signal |
|----------|----------|-----------|--------|
| `four_way` | SVXY / GLD / TLT / TSMOM portfolio | hourly + daily + monthly | VIX/VX1 ratio, SKEW, DXY, GVZ, MOVE, 12m mom |
| `vw_tsmom_crypto` | BTC/ETH/LTC/BNB | daily | 252-day vol-weighted momentum |
| `donchian_crypto` | 20 crypto pairs | daily | 55/20 Donchian channel |
| `vvix_spy_reversal` | SPY | daily | VVIX > 120 → 5-day reversal |
| `overnight_qqq_fri_skip` | QQQ | daily | Overnight-only, skip Friday |
| `weekly_spy_spread` | SPY options | weekly | Bull call spread + martingale |

Full specs in [METHODOLOGY.md](METHODOLOGY.md).

## How an auditor reproduces a day

```bash
git clone https://github.com/romandrianov-pixel/paper-trading-track-record
cd paper-trading-track-record
pip install -r requirements.txt  # pandas, numpy, pyyaml
python3 verify.py --date 2026-04-23
```

Expected output: `VERIFIED: hash chain intact, state matches raw prices`.

### OpenTimestamps verification

```bash
# Install the client once
pip install opentimestamps-client   # provides the `ots` CLI

# Verify a specific week's stamp
ots verify .ots/2026-04-26-tree.ots
```

`ots verify` walks back from the Bitcoin block-header Merkle root and confirms
the file hash was anchored at the claimed block height. If the repo was
modified after the stamp, the hash mismatch surfaces here.

## Repository layout

```
track_record/
├── README.md              # this file
├── METHODOLOGY.md         # strategy specs
├── LICENSE                # MIT
├── SETUP.md               # one-time setup (manual steps for Roma)
├── state/<date>/          # per-strategy JSON state snapshots
├── daily/<strategy>.csv   # append-only daily log per strategy
├── digests/<date>.md      # human-readable daily digest
├── raw_prices/<date>/     # snapshots of yfinance / ccxt / CBOE raw responses
├── hashes.json            # running SHA256 hash chain
├── code_versions.md       # paper_runner git SHA per day
├── .ots/                  # weekly OpenTimestamps proofs
├── verify.py              # auditor CLI
├── commit_daily.py        # called by paper_runner.py after each run
└── ots_weekly.py          # Sunday 22:00 UK stamping job
```

## Contact

Roman Andrianov — romandrianov@gmail.com

---

*Paper trades only. Not financial advice.*
