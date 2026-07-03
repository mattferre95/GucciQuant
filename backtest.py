"""
GUCCI QUANT — 90-Day Strategy Backtest
======================================
Fetches hourly funding rate history from Hyperliquid API and simulates
the full strategy (trend filter + exit logic + cooldown) against a naive
baseline to validate RBI gate criteria before testnet deployment.

Usage:
  python backtest.py              # live API (requires network access)
  python backtest.py --mock       # synthetic data (CI / sandboxed envs)

RBI Gate:  Sharpe ≥ 1.5  AND  Win Rate ≥ 55%
Exit code: 0 = gate passed (safe to deploy testnet), 1 = failed
"""
import sys, time, math, random, argparse
from datetime import datetime, timezone

# ── API ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://api.hyperliquid.xyz/info"

# ── Strategy constants (mirror production) ────────────────────────────────────
DAYS          = 90
MIN_RATE      = 0.0015   # 0.15 %/hr — minimum qualifying rate
FEE_RATE      = 0.0011   # 0.11 % round-trip (verified against actual fee tier)
EXIT_RATIO    = 0.33     # exit when rate falls to 33 % of entry rate
EXIT_FLOOR    = 0.0003   # 0.03 %/hr hard floor on exit threshold
MIN_HOLD_HRS  = 1        # hold at least 1 full funding period
COOLDOWN_HRS  = 0.5      # 30-min cooldown after exit (prevents oscillation)
MAX_POSITIONS = 3
LEG_SIZE      = 20.0     # $ per leg (testnet sizing — proportional for metrics)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

def _post(payload: dict) -> dict | list:
    import requests
    r = requests.post(BASE_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_tradeable() -> list[str]:
    """Assets with both Hyperliquid native spot + perp market."""
    spot = {t["name"] for t in _post({"type": "spotMeta"}).get("tokens", [])}
    meta, _ = _post({"type": "metaAndAssetCtxs"})
    perp = {m["name"] for m in meta["universe"]}
    return sorted(spot & perp)


def fetch_history(asset: str) -> list[dict]:
    """
    Paginate fundingHistory for `asset` back DAYS days.
    Returns list of {time_ms, dt, rate} sorted ascending.
    """
    start_ms = int((time.time() - DAYS * 86400) * 1000)
    records = []
    while True:
        data = _post({"type": "fundingHistory", "coin": asset, "startTime": start_ms})
        if not isinstance(data, list) or not data:
            break
        records.extend(data)
        if len(data) < 500:
            break
        start_ms = data[-1]["time"] + 1
        time.sleep(0.15)
    records.sort(key=lambda x: x["time"])
    return [
        {
            "time_ms": h["time"],
            "dt":      datetime.fromtimestamp(h["time"] / 1000, tz=timezone.utc),
            "rate":    float(h["fundingRate"]),
        }
        for h in records
    ]


def mock_history(asset: str, seed: int = 42) -> list[dict]:
    """
    Synthetic funding-rate series that mirrors Hyperliquid native-token
    (HYPE-class) behaviour over 90 days.

    Model:  Ornstein-Uhlenbeck with 3-regime switching
      Regime A (high, ~30 % of time):  mean 0.25 %/hr, σ 0.12 %/hr
      Regime B (mid,  ~40 % of time):  mean 0.10 %/hr, σ 0.07 %/hr
      Regime C (low,  ~30 % of time):  mean 0.02 %/hr, σ 0.04 %/hr
    Regime transitions driven by a simple Markov chain.
    """
    rng = random.Random(seed + hash(asset) % 1000)
    N   = DAYS * 24

    # Regime params: (mean, sigma, theta)
    regimes = [
        (0.0025, 0.0012, 0.08),   # A — hot
        (0.0010, 0.0007, 0.12),   # B — moderate
        (0.0002, 0.0004, 0.20),   # C — cool
    ]
    # Transition probability matrix (row = current, col = next)
    trans = [
        [0.95, 0.04, 0.01],
        [0.05, 0.90, 0.05],
        [0.02, 0.08, 0.90],
    ]

    regime  = 1  # start moderate
    rate    = regimes[regime][0]
    records = []
    base_ms = int((time.time() - DAYS * 86400) * 1000)

    for i in range(N):
        # Markov regime switch
        p = rng.random()
        cum = 0.0
        for j, prob in enumerate(trans[regime]):
            cum += prob
            if p < cum:
                regime = j
                break

        mu, sigma, theta = regimes[regime]
        # OU step: dX = theta*(mu-X)*dt + sigma*sqrt(dt)*dW
        dt = 1.0
        dW = rng.gauss(0, 1)
        rate = rate + theta * (mu - rate) * dt + sigma * math.sqrt(dt) * dW

        # Occasional spike events (~1 % of hours)
        if rng.random() < 0.01:
            rate += rng.uniform(0.003, 0.015)

        rate = max(rate, -0.002)  # floor at -0.2 %/hr

        time_ms = base_ms + i * 3_600_000
        records.append({
            "time_ms": time_ms,
            "dt":      datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc),
            "rate":    rate,
        })

    return records


# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _trend(hist: list[dict], idx: int) -> str:
    """Replicate production get_rate_trend(): 4-point look-back."""
    window = hist[max(0, idx - 4) : idx]
    if len(window) < 3:
        return "stable"
    rates = [h["rate"] for h in window]
    mid   = len(rates) // 2
    ea    = sum(rates[:mid]) / mid
    la    = sum(rates[mid:]) / (len(rates) - mid)
    chg   = (la - ea) / max(abs(ea), 1e-9) * 100
    if chg >  15: return "rising"
    if chg < -15: return "falling"
    return "stable"


def simulate(
    histories: dict[str, list[dict]],
    trend_filter: bool = True,
    timing_filter: bool = True,
    name: str = "",
) -> dict:
    """
    Discrete hourly simulation over all assets simultaneously.

    trend_filter  — skip entries when 4-hr trend is falling
    timing_filter — proxy for :45-:59 entry window:
                    for rates < 0.30 %/hr, only enter on 1 in 4 hours
                    (25 % ≈ the 15-min optimal window fraction)
    """
    # Unified event stream sorted by (time_ms, asset)
    events: list[tuple] = []
    for asset, hist in histories.items():
        for i, pt in enumerate(hist):
            events.append((pt["time_ms"], asset, i, pt["rate"], pt["dt"]))
    events.sort(key=lambda x: (x[0], x[1]))

    positions: dict[str, dict] = {}   # asset → state
    cooldowns: dict[str, int]  = {}   # asset → last exit time_ms
    trades: list[dict]         = []

    for time_ms, asset, idx, rate, dt in events:
        hist = histories[asset]

        # ── Funding payment + exit check for open position ─────────────────
        if asset in positions:
            pos = positions[asset]
            # Funding accrues on the perp leg's notional only
            pos["earned"] += rate * LEG_SIZE
            pos["hrs"]    += 1

            thresh   = max(pos["entry_rate"] * EXIT_RATIO, EXIT_FLOOR)
            do_exit  = (rate < 0) or (rate <= thresh and pos["hrs"] >= MIN_HOLD_HRS)

            if do_exit:
                gross = pos["earned"]
                fees  = LEG_SIZE * 2 * FEE_RATE
                trades.append({
                    "net":        gross - fees,
                    "gross":      gross,
                    "fees":       fees,
                    "hrs":        pos["hrs"],
                    "entry_rate": pos["entry_rate"],
                    "exit_rate":  rate,
                })
                cooldowns[asset] = time_ms
                del positions[asset]

        # ── Entry scan ─────────────────────────────────────────────────────
        elif len(positions) < MAX_POSITIONS and rate > MIN_RATE:
            # Cooldown guard
            if time_ms - cooldowns.get(asset, 0) < COOLDOWN_HRS * 3_600_000:
                continue

            # Trend filter (strategy only)
            if trend_filter and _trend(hist, idx) == "falling":
                continue

            # Timing window proxy (strategy only):
            # Production only enters sub-0.30 % rates during :45-:59 window.
            # At hourly resolution we approximate this: only enter on 1/4 hours.
            if timing_filter and rate < 0.003 and dt.hour % 4 != 0:
                continue

            positions[asset] = {
                "entry_rate":    rate,
                "entry_time_ms": time_ms,
                "earned":        0.0,
                "hrs":           0,
            }

    # Force-close any positions still open at end of window
    for asset, pos in positions.items():
        gross = pos["earned"]
        fees  = LEG_SIZE * 2 * FEE_RATE
        trades.append({
            "net": gross - fees, "gross": gross, "fees": fees,
            "hrs": pos["hrs"], "entry_rate": pos["entry_rate"],
            "exit_rate": 0.0,
        })

    return _calc_metrics(trades, name)


# ══════════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════════

def _calc_metrics(trades: list[dict], name: str) -> dict:
    if not trades:
        return {"name": name, "n": 0}

    pnls = [t["net"] for t in trades]
    n    = len(pnls)
    tot  = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    avg  = tot / n

    variance = sum((p - avg) ** 2 for p in pnls) / max(n - 1, 1)
    std      = math.sqrt(variance)
    # Annualise from per-trade PnL using hourly trade frequency
    sharpe   = (avg / std * math.sqrt(24 * 365)) if std > 0 else 0.0

    total_hrs = sum(t["hrs"] for t in trades)
    cap       = LEG_SIZE * 2 * MAX_POSITIONS        # total deployed capital
    annual    = (
        (tot / max(total_hrs / 24, 1)) * 365 / cap * 100
    ) if total_hrs >= 24 else 0.0

    # Max drawdown (peak-to-trough on cumulative PnL)
    running = peak = mdd = 0.0
    for p in pnls:
        running += p
        peak     = max(peak, running)
        mdd      = max(mdd, peak - running)

    return {
        "name":       name,
        "n":          n,
        "total_pnl":  round(tot, 4),
        "win_rate":   round(wins / n * 100, 1),
        "sharpe":     round(sharpe, 2),
        "max_dd":     round(mdd, 4),
        "annual_pct": round(annual, 1),
        "avg_hrs":    round(total_hrs / n, 2),
        "best":       round(max(pnls), 4),
        "worst":      round(min(pnls), 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════

W = 54

def _bar(label: str, value: str) -> str:
    return f"  {label:<22} {value}"


def report(m: dict) -> None:
    sep = "═" * W
    print(f"\n{sep}")
    print(f"  {m.get('name', '')}")
    print(sep)
    if not m.get("n"):
        print("  No trades in backtest period.\n")
        return

    print(_bar("Trades:", str(m["n"])))
    print(_bar("Total PnL:", f"${m['total_pnl']:+.4f}"))
    print(_bar("Win Rate:", f"{m['win_rate']}%"))
    print(_bar("Sharpe Ratio:", str(m["sharpe"])))
    print(_bar("Max Drawdown:", f"${m['max_dd']:.4f}"))
    print(_bar("Annual Return:", f"{m['annual_pct']}%  (on ${LEG_SIZE*2*MAX_POSITIONS:.0f} deployed)"))
    print(_bar("Avg Hold Time:", f"{m['avg_hrs']} hrs"))
    print(_bar("Best / Worst:", f"${m['best']:+.4f}  /  ${m['worst']:+.4f}"))

    s_ok = m["sharpe"]   >= 1.5
    w_ok = m["win_rate"] >= 55.0
    print(f"\n  ── RBI Gate ──")
    print(f"  Sharpe  ≥ 1.5    {'✅ PASS' if s_ok else '❌ FAIL'}  ({m['sharpe']})")
    print(f"  Win Rate ≥ 55%   {'✅ PASS' if w_ok else '❌ FAIL'}  ({m['win_rate']}%)")
    status = "✅ TESTNET DEPLOY CLEARED" if (s_ok and w_ok) else "❌ GATE FAILED — hold testnet"
    print(f"\n  {status}")
    print(sep)


def compare(strat: dict, base: dict) -> None:
    if not strat.get("n") or not base.get("n"):
        return
    sep = "─" * W
    print(f"\n{sep}")
    print(f"  Strategy vs Baseline — Delta Summary")
    print(sep)
    ds = strat["sharpe"]   - base["sharpe"]
    dw = strat["win_rate"] - base["win_rate"]
    dp = strat["total_pnl"] - base["total_pnl"]
    nt = strat["n"] - base["n"]
    print(f"  Sharpe  delta:    {ds:+.2f}  ({base['sharpe']} → {strat['sharpe']})")
    print(f"  Win Rate delta:   {dw:+.1f}%  ({base['win_rate']}% → {strat['win_rate']}%)")
    print(f"  Total PnL delta:  ${dp:+.4f}")
    print(f"  Trade count:      {base['n']} → {strat['n']}  (delta {nt:+d})")
    print(sep)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="GucciQuant 90-day backtest")
    parser.add_argument("--mock", action="store_true",
                        help="Use synthetic data (no network needed)")
    args = parser.parse_args()

    print("\n" + "█" * W)
    print("  GUCCI QUANT — 90-Day Backtest Engine")
    print(f"  {DAYS}d · ${LEG_SIZE}/leg · Strategy vs Baseline")
    if args.mock:
        print("  ⚠️  SYNTHETIC DATA MODE — real API unavailable in this env")
        print("     Run without --mock flag on a server for live API data")
    print("█" * W + "\n")

    # ── Data acquisition ──────────────────────────────────────────────────────
    if args.mock:
        # Use the assets the production bot trades on Hyperliquid
        # HYPE is the primary high-funding asset; include a few others for diversity
        mock_assets = ["HYPE", "HFUN", "PURR", "BERA"]
        print(f"📦 Generating synthetic {DAYS}-day histories for: {mock_assets}\n")
        histories: dict[str, list[dict]] = {}
        for i, asset in enumerate(mock_assets):
            hist = mock_history(asset, seed=42 + i * 7)
            above = sum(1 for h in hist if h["rate"] > MIN_RATE)
            print(f"   {asset}: {len(hist)} pts  |  "
                  f"{above/len(hist)*100:.0f}% above threshold  |  "
                  f"avg {sum(h['rate'] for h in hist)/len(hist)*100:.3f}%/hr")
            histories[asset] = hist
    else:
        print("📡 Fetching tradeable assets from Hyperliquid API...")
        try:
            assets = fetch_tradeable()
        except Exception as e:
            print(f"\n❌ API unreachable: {e}")
            print("   Re-run with --mock flag or ensure network access to api.hyperliquid.xyz")
            return 2
        print(f"   {len(assets)} assets with spot + perp: {assets}\n")

        histories = {}
        for asset in assets:
            print(f"📥 {asset}: fetching {DAYS}-day history...", end=" ", flush=True)
            try:
                hist = fetch_history(asset)
            except Exception as e:
                print(f"⚠️  error ({e}) — skipping")
                continue
            if len(hist) < 72:
                print(f"⚠️  only {len(hist)} pts — skipping")
                continue
            above = sum(1 for h in hist if h["rate"] > MIN_RATE)
            print(f"✅ {len(hist)} pts ({len(hist)//24:.0f}d)  |  "
                  f"{above/len(hist)*100:.0f}% above threshold")
            histories[asset] = hist
            time.sleep(0.2)

        if not histories:
            print("\n❌ No historical data retrieved. Check API connectivity.")
            return 2

    # ── Simulations ───────────────────────────────────────────────────────────
    n_assets = len(histories)
    print(f"\n📊 Running simulations on {n_assets} asset(s)...\n")

    strat = simulate(
        histories,
        trend_filter=True,
        timing_filter=True,
        name=f"STRATEGY  (trend + timing filters)   [{n_assets} assets, {DAYS}d]",
    )
    base = simulate(
        histories,
        trend_filter=False,
        timing_filter=False,
        name=f"BASELINE  (no filters)               [{n_assets} assets, {DAYS}d]",
    )

    report(strat)
    report(base)
    compare(strat, base)

    # ── Final verdict ─────────────────────────────────────────────────────────
    gate = strat.get("sharpe", 0) >= 1.5 and strat.get("win_rate", 0) >= 55.0
    print(f"\n{'✅ RBI GATE PASSED — testnet deployment authorised' if gate else '❌ RBI GATE FAILED — do NOT deploy'}\n")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
