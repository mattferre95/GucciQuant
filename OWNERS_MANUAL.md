# GucciQuant — Owner's Manual & Go-Live Brief

> Written so anyone can follow it — no trading background needed.
> Status (July 19, 2026): **Bot running 24/7 in practice mode · Tripwire armed · Live trading on hold (market too quiet)**

---

## 1 · What the bot does, in plain English

On crypto exchanges, people who borrow money to bet that prices will go **up** have to pay a small fee, every hour, to the people betting the other way. This fee is called the **funding rate**. When a coin gets hyped, the fee gets big — because everyone is crowding onto the "up" side and someone has to balance them.

Your bot's entire job is to be the party that **collects** that hourly fee, while making sure price moves can't hurt it:

- It places a **"down" bet** (a short) — this is the side that *receives* the hourly fee.
- At the same moment, it **buys the same coin normally** — so if the price rises, the coin it owns gains exactly what the down-bet loses. The two cancel out.

Result: price goes up, down, sideways — the bot doesn't care. Its profit is purely the hourly fee, minus trading costs. Think of it as **selling patience to impatient gamblers and charging them by the hour**.

**Why this is a real edge:** most trading bots try to predict where price goes next — and most fail. This one predicts nothing. The fee is a payment somebody *must* make whenever the crowd leans bullish. The only skill involved is showing up when the fee is rich enough to beat costs, and staying out when it isn't.

## 2 · The story so far

| When | What happened |
|---|---|
| May–June 2026 | Bot built and hardened: safety limits, crash recovery, Discord alerts, dashboard. A profit-calculation bug was found and fixed (it was counting income twice — all numbers since are honest). |
| July 3 | Practice run started: the bot watched the **real live market** 24/7 and recorded every trade it *would* have made — without risking a cent. |
| July 3–19 | Perfect attendance: ~96 market checks per day, every day, zero downtime. Trades taken: **zero** — the hourly fee never got high enough to be worth the trading costs. |
| July 19 | Deep check: 90 days of real market history across all 10 tradeable coins — 21,600 hours of data. **Not a single hour** offered a fee above the profit bar. The market has been cold all summer. |
| Now | Bot on standby, watching. A **tripwire** pings Discord the moment the market warms up. Live trading waits for that signal. |

**Why zero trades is the right answer, not a failure:** the fee the market was offering was ~0.0013% per hour; a round trip costs ~0.22%. A trade would need roughly *a week* of uninterrupted fee collection just to pay for itself — and fees never stay put that long. Imagine driving a taxi where the fare is 1/170th of the fuel cost: the smart move is to park and wait for surge pricing. That's what the bot is doing — and it checks for surge pricing 96 times a day without getting bored.

## 3 · The rules it trades by

**When it enters:**

| Check | Rule | In plain terms |
|---|---|---|
| Hourly fee | ≥ 0.05%/hr | Rich enough to beat costs within a few hours |
| Market tightness | ≤ 0.05% spread | Don't trade where entering/exiting is expensive |
| Next-hour forecast | positive | Don't enter just as the fee dries up |
| 4-hour trend | not falling | Avoid collapsing fees |
| Open positions | max 3 | Never all eggs in one basket |
| Re-entry pause | 30 min | No churning in and out, burning costs |

**How much it bets:** the Kelly formula (bets more when the edge is bigger), run at half strength for safety, capped at 80% of capital. On a bad day, sizes shrink automatically before the hard stop kicks in.

**When it exits:** fee falls below a third of entry level → take profit and leave. Fee flips negative → exit immediately. A fast safety check runs every 5 minutes.

## 4 · The go/no-go gate — scorecard as of July 19, 2026

| Bar | Meaning | Result |
|---|---|---|
| Trades ≥ 10 | Enough to be evidence, not luck | ✗ 0 trades |
| Win rate ≥ 55% | Wins most of the time, by design | — no data |
| Profit > 0 | Positive after all costs | — no data |
| Ran continuously | ~96 checks/day, no gaps | ✓ passed |

**Verdict: NOT cleared → live trading stays off.** The gate failed because the *market* offered nothing — not because the strategy lost. Going live today would change nothing except attach real money to the same zero trades.

## 5 · The tripwire

Every 15 minutes the bot checks **every** coin's hourly fee — no filters — and **pings Discord the moment any fee crosses 0.03%/hr** (deliberately below the 0.05% trading bar, so you hear about a warming market early). Max one ping per coin per 6 hours.

**When it pings:**
1. Open the dashboard: `http://187.124.41.102:8080`. If fees climb past 0.05%/hr, the bot starts taking practice trades on its own.
2. Let practice trades accumulate — a handful of profitable ones during a hot market is the gate evidence we've been missing.
3. Re-check the gate scorecard (section 8), and if it clears, follow the go-live sequence.

## 6 · Going live — the exact sequence

Only after the gate passes. Budget 20 minutes.

1. **Rotate your keys — not optional.** The old wallet key, Anthropic API key, and Discord webhook were all exposed during setup. Create a brand-new wallet, re-create the Anthropic key, re-create the webhook. Doing this today makes go-live day a 15-minute job.
2. **Fund the new wallet — small.** USDC into Hyperliquid → Perps balance. Start with $50–100.
3. **Flip the switches** — copy-paste block in section 8.
4. **Restart and verify** — log must show `Mode: 💰 LIVE` with all checks green.
5. **Probation week** — cross-check the first few trades on the Hyperliquid app; expect live results slightly below practice (that gap is the real cost of execution).

**Standing safety rails (already built in):** daily loss limit −$5 pauses trading · max 3 positions · 80% capital cap · liquidation monitor force-closes anything at risk · if one half of a trade fails, the other half closes instantly.

## 7 · Day-to-day operator card

| You want to… | Do this |
|---|---|
| See the dashboard | `http://187.124.41.102:8080` |
| Know if the market woke up | Nothing — the tripwire pings Discord automatically |
| Check the bot is alive | `systemctl status gucci-bot` |
| Watch live logs | `journalctl -u gucci-bot -f` (Ctrl+C to stop, `q` if it shows END) |
| Restart after a settings change | `systemctl restart gucci-bot` |
| Stop everything now | `systemctl stop gucci-bot` |

## 8 · Every command, copy-paste ready

All run in the **Hostinger terminal** (`root@srv1700421` prompt).

### Check results

```bash
# Scorecard — totals, win rate, best/worst
sqlite3 ~/GucciQuant/data/gucci_quant_paper.db \
"SELECT COUNT(*) AS trades, ROUND(SUM(net_pnl),4) AS total_pnl, \
ROUND(AVG(net_pnl),4) AS avg_pnl, \
ROUND(100.0*SUM(net_pnl>0)/COUNT(*),1) AS win_rate, \
ROUND(MIN(net_pnl),4) AS worst, ROUND(MAX(net_pnl),4) AS best FROM trades;"

# Last 20 trades
sqlite3 ~/GucciQuant/data/gucci_quant_paper.db \
"SELECT substr(timestamp,1,16) AS time, asset, ROUND(size_usd,2) AS size, \
ROUND(funding_rate*100,3)||'%' AS rate, ROUND(net_pnl,4) AS pnl, \
ROUND(duration_hrs,1) AS hrs FROM trades ORDER BY timestamp DESC LIMIT 20;"

# Heartbeat — checks per day (healthy ≈ 96/day)
sqlite3 ~/GucciQuant/data/gucci_quant_paper.db \
"SELECT substr(timestamp,1,10) AS day, COUNT(*) AS scans \
FROM scan_log GROUP BY day ORDER BY day;"

# What fees the market is offering right now (last 48h)
sqlite3 ~/GucciQuant/data/gucci_quant_paper.db \
"SELECT asset, ROUND(MAX(rate_pct),4) AS max_rate, \
ROUND(AVG(rate_pct),4) AS avg_rate FROM rate_snapshot \
GROUP BY asset ORDER BY max_rate DESC;"
```

### Bot & dashboard control

```bash
systemctl status gucci-bot            # is the bot running?
systemctl status gucci-dashboard      # is the dashboard running?
journalctl -u gucci-bot -f            # live logs (Ctrl+C to exit)
journalctl -u gucci-bot -n 50         # last 50 log lines (q to quit)
systemctl restart gucci-bot           # restart the bot
systemctl restart gucci-dashboard     # restart the dashboard
systemctl stop gucci-bot              # stop trading (closes positions)
systemctl start gucci-bot             # start again
```

### Update to the latest code

```bash
cd ~/GucciQuant
git pull origin claude/strategy-backtest-testnet-4h4eJ
pip3 install --break-system-packages --ignore-installed -r requirements.txt
systemctl restart gucci-bot gucci-dashboard
```

### Edit settings without nano

```bash
# See current settings (secrets hidden)
grep -vE 'PRIVATE_KEY|WEBHOOK|API_KEY' ~/GucciQuant/.env

# Change any single setting — pattern: sed -i 's|NAME=.*|NAME=newvalue|' file
sed -i 's|MIN_RATE=.*|MIN_RATE=0.0005|'               ~/GucciQuant/.env
sed -i 's|DAILY_LOSS_LIMIT=.*|DAILY_LOSS_LIMIT=5|'    ~/GucciQuant/.env
sed -i 's|MAX_POSITIONS=.*|MAX_POSITIONS=3|'          ~/GucciQuant/.env

# Tripwire sensitivity (%/hr — lower = earlier warning, more pings)
grep -q REGIME_ALERT_PCT ~/GucciQuant/.env || echo "REGIME_ALERT_PCT=0.03" >> ~/GucciQuant/.env
sed -i 's|REGIME_ALERT_PCT=.*|REGIME_ALERT_PCT=0.03|' ~/GucciQuant/.env

# Settings only load at startup — always restart after editing
systemctl restart gucci-bot
```

### Go-live switch (only after the gate passes + new keys)

```bash
# 1. Point at a fresh live database
sed -i 's|DB_PATH=.*|DB_PATH=data/gucci_quant_live.db|' ~/GucciQuant/.env

# 2. Set your NEW wallet (replace the placeholders!)
sed -i 's|HYPERLIQUID_PRIVATE_KEY=.*|HYPERLIQUID_PRIVATE_KEY=0xYOUR_NEW_KEY|' ~/GucciQuant/.env
sed -i 's|HYPERLIQUID_WALLET_ADDRESS=.*|HYPERLIQUID_WALLET_ADDRESS=0xYOUR_NEW_ADDRESS|' ~/GucciQuant/.env

# 3. Set real capital amount (what you deposited)
sed -i 's|STARTING_CAPITAL=.*|STARTING_CAPITAL=100|' ~/GucciQuant/.env

# 4. Flip to live
sed -i 's|PAPER_MODE=.*|PAPER_MODE=false|'         ~/GucciQuant/.env
sed -i 's|TRADING_ENABLED=.*|TRADING_ENABLED=true|' ~/GucciQuant/.env

# 5. Restart and confirm you see "Mode: 💰 LIVE" + all checks green
systemctl restart gucci-bot
journalctl -u gucci-bot -n 30
```

### Emergency stop

```bash
# Stop the bot — it closes all open positions on the way down
systemctl stop gucci-bot

# Verify nothing is left open on the exchange:
# app.hyperliquid.xyz → connect wallet → check Positions tab
```

### The 90-day reality check (re-run anytime)

```bash
# Pulls 90 days of REAL market history and simulates the strategy.
# "No trades" = market still cold. Trades + PASS = time to consider live.
cd ~/GucciQuant && python3 backtest.py
```

---

## 9 · Full technical specification (for a trader's review)

Everything a professional needs to evaluate the strategy. All numbers are from the code as deployed, not aspirational.

### Structure

| Item | Spec |
|---|---|
| Venue | Hyperliquid (perps + native spot), funding paid hourly at :00 UTC |
| Position | Short perp + long spot, equal notional per leg, no leverage |
| Universe | Assets with BOTH a Hyperliquid native spot market and a perp — currently 10: ANIME, AZTEC, BERA, HYPE, MON, PUMP, PURR, STABLE, TRUMP, WLFI |
| Funding accrual | Perp leg's notional only (spot leg earns nothing) |
| Scan cadence | Full scan every 15 min; exit-only fast check every 5 min |

### Entry conditions (ALL must pass)

| Condition | Value | Notes |
|---|---|---|
| Funding rate | ≥ 0.05%/hr (0.0005) | Env-tunable `MIN_RATE`; code default 0.15%/hr |
| 24h volume | ≥ $1,000,000 | Filters illiquid names |
| Bid–ask spread | ≤ 0.05% (0.0005) | From live L2 book |
| Predicted next-hour rate | ≥ 0.05%/hr | Hyperliquid's own forecast |
| 4h rate trend | not "falling" | Classifier: last 4 hourly rates, early-half avg vs late-half avg, falling if −15% or worse |
| Concurrent positions | < 3 | |
| Re-entry cooldown | 30 min per asset | Prevents fee-churn oscillation |
| Timing window | :45–:59 preferred for rates < 0.30%/hr | First funding payment lands within 15 min of entry |

### Position sizing

Half-Kelly with overlays. Per-leg USD size =

```
edge        = rate − 0.0010            (fee-adjusted edge)
full_kelly  = edge / rate
size        = capital × min(half_kelly, 80%) / 3
            × tier_multiplier × drawdown_factor
```

| Overlay | Values |
|---|---|
| Tier multiplier | ≥0.50%/hr → 1.5× · 0.30–0.50 → 1.25× · 0.20–0.30 → 1.0× · below → 0.75× |
| Drawdown factor | day ≥ −$1 → 1.0× · to half the daily limit → 0.75× · beyond → 0.5× |
| Floor / min trade | $5 per leg minimum |

### Fees & break-even (verified against the account's actual fee tier)

| Leg | Entry | Exit |
|---|---|---|
| Perp | ALO maker 0.015% | market/taker 0.045% |
| Spot | maker ~0.040% | IOC/taker ~0.100% |

Round trip ≈ **0.22% of one leg's notional** (0.11% of combined). Since funding accrues on the perp leg only:

```
break-even hold (hours) = 0.0022 / hourly_rate
  0.15%/hr → 1.5h     0.05%/hr → 4.4h     0.0013%/hr → ~169h (why the bot won't trade today)
```

### Exit logic (priority order)

1. Rate < 0 → exit immediately (we'd be paying).
2. Rate < trail threshold AND fees covered → take profit.
3. Rate < trail threshold AND held ≥ 1h → cut.
4. Otherwise hold. Trail threshold = max(33% of entry rate, 0.03%/hr).

### Execution mechanics (live mode)

- Entries: ALO (post-only) limit orders — perp short at mark × 0.9998, spot long at mark × 1.0002; 45s fill verification by polling open orders; unfilled → cancel.
- **Single-leg protection:** if the spot leg fails after the perp fills, the perp is market-closed immediately. No naked exposure possible.
- Exits: perp market-close + spot IOC sell.
- Retries: 3× exponential backoff on all API calls.

### Risk rails

- Daily loss limit −$5 → close all positions, halt trading until next day.
- Liquidation monitor: alert at <20% distance, force-close at <10% (delta-neutral, unlevered, so buffers are naturally wide).
- Crash recovery: open positions persist in SQLite; on restart the bot restores exact entry times and manages/exits them.
- Max 80% of capital deployed; max 3 positions; separate DBs for paper/live.

### Empirical record (honest, includes the negative result)

| Test | Result |
|---|---|
| Synthetic backtest (90d, OU-process regime model calibrated to *hot-market* rates ~0.2%/hr avg) | Sharpe 58.4, win rate 87.1%, strategy beat no-filter baseline by +7.6 Sharpe — validates the *logic*, not the current market |
| Live-data practice run, Jul 3–19 2026 (16 days, ~1,500 scans, threshold 0.05%/hr) | **0 entries** — no scan found a qualifying opportunity |
| Real 90-day funding history, all 10 assets (21,600 asset-hours, tested at 0.15%/hr) | **0 hours above threshold** |
| Observed rate levels (48h window, Jul 19) | max 0.0066%/hr (PURR); typical 0.0013%/hr (HL baseline); STABLE avg −0.0053%/hr |

Interpretation: the strategy's edge exists only when funding spikes (new listings, hype cycles, bull frenzies pushing 0.1–0.5%/hr). The current regime pays ~1/170th of round-trip cost per hour. The bot correctly refuses to trade; a 0.03%/hr regime tripwire alerts the operator when conditions change.

### Known limitations & open questions (for the reviewer)

- **Universe is small** (10 names, HL native spot only — no BTC/ETH spot on HL), and mostly low-cap; capacity is limited by the $1M volume floor and thin spot books.
- **Hourly-resolution backtest** can't model intra-hour rate decay, queue position on ALO fills, or partial fills.
- The synthetic backtest's regime model was calibrated optimistically; treat its Sharpe as an upper bound on strategy logic quality, not an expected return.
- Paper fills assume perfect maker execution; live results will be worse by slippage + missed fills.
- Possible extensions worth a trader's opinion: cross-exchange funding capture (hedge leg elsewhere, larger universe), negative-funding capture (long perp side when shorts pay), and whether 0.05%/hr is the right bar given observed regime distributions.

---

*GucciQuant v1.3 · funding-fee collection on Hyperliquid · practice run July 3–19, 2026: perfect uptime, zero trades, gate not cleared — live trading waits for the tripwire. The gate in section 4 is the contract: no real money until the scorecard clears it.*
