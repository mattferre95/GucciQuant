"""
GUCCI QUANT — Live Performance Metrics
Calculated from real SQLite trade history.
Metrics: Sharpe ratio, win rate, annualized return, max drawdown.
"""
import math
from utils.logger import get_conn


def get_metrics(starting_capital: float = 100.0) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT net_pnl, duration_hrs FROM trades ORDER BY timestamp"
        ).fetchall()

    if not rows:
        return {"status": "No trades yet", "total_trades": 0}

    pnls  = [r["net_pnl"] for r in rows]
    durs  = [r["duration_hrs"] or 1 for r in rows]
    n     = len(pnls)

    total_pnl = sum(pnls)
    win_rate  = sum(1 for p in pnls if p > 0) / n * 100
    avg_pnl   = total_pnl / n

    # Sharpe (annualized from hourly data)
    if n > 1:
        std    = math.sqrt(sum((p - avg_pnl)**2 for p in pnls) / (n - 1))
        sharpe = (avg_pnl / std * math.sqrt(24 * 365)) if std > 0 else 0
    else:
        sharpe = 0

    # Annualized return from actual results
    total_hrs = sum(durs)
    annual    = (total_pnl / (total_hrs / 24) * 365 / starting_capital * 100
                 ) if total_hrs >= 24 else 0

    # Max drawdown
    rpnl = peak = mdd = 0
    for p in pnls:
        rpnl += p
        peak  = max(peak, rpnl)
        mdd   = max(mdd, peak - rpnl)

    return {
        "total_trades":      n,
        "total_pnl":         round(total_pnl, 4),
        "win_rate_pct":      round(win_rate, 1),
        "best_trade":        round(max(pnls), 4),
        "worst_trade":       round(min(pnls), 4),
        "sharpe_ratio":      round(sharpe, 2),
        "max_drawdown":      round(mdd, 4),
        "annual_return_pct": round(annual, 1),
        "avg_hold_hrs":      round(sum(durs) / len(durs), 2),
    }


def print_report(starting_capital: float = 100.0):
    m = get_metrics(starting_capital)
    if not m.get("total_trades"):
        print("  📊 No trades yet")
        return
    print("\n" + "═"*44 + "\n  📊 GUCCI QUANT PERFORMANCE\n" + "═"*44)
    for k, v in m.items():
        print(f"  {k+':':<22} {v}")
    print("═"*44 + "\n")
