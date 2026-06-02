"""
GUCCI QUANT v2.0 — Scalping Execution Engine
Directional perp trades on Hyperliquid with leverage, SL, and TP.
Paper mode only for now — set PAPER_MODE=false to enable live trading.
"""
import os, time
from dotenv import load_dotenv

from execution.hyperliquid_trader import get_mark_price, with_retry

load_dotenv()

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
LEVERAGE   = int(os.getenv("SCALP_LEVERAGE", 5))
SL_PCT     = float(os.getenv("SCALP_SL_PCT", 0.005))   # 0.5%
TP_PCT     = float(os.getenv("SCALP_TP_PCT", 0.010))   # 1.0%

FEE_RATE   = 0.001   # ~0.1% round-trip (taker both legs)


def _sl_tp_prices(direction: str, entry: float) -> tuple:
    if direction == "LONG":
        return entry * (1 - SL_PCT), entry * (1 + TP_PCT)
    return entry * (1 + SL_PCT), entry * (1 - TP_PCT)


def _paper_enter(asset: str, direction: str, margin_usd: float, price: float) -> dict:
    notional = round(margin_usd * LEVERAGE, 4)
    sl, tp   = _sl_tp_prices(direction, price)
    print(f"  📄 [PAPER SCALP] {direction} {asset} | "
          f"margin=${margin_usd:.2f} ×{LEVERAGE} = ${notional:.2f} notional | "
          f"entry=${price:.2f} | SL=${sl:.2f} | TP=${tp:.2f}")
    return {
        "asset":       asset,
        "direction":   direction,
        "margin_usd":  margin_usd,
        "notional":    notional,
        "entry_price": price,
        "sl_price":    sl,
        "tp_price":    tp,
        "entry_time":  time.time(),
        "paper":       True,
        "status":      "open",
    }


def _paper_exit(position: dict, exit_price: float, reason: str) -> float:
    entry     = position["entry_price"]
    notional  = position["notional"]
    direction = position["direction"]

    raw_pnl = notional * (exit_price - entry) / entry
    if direction == "SHORT":
        raw_pnl = -raw_pnl
    fees = notional * FEE_RATE
    net  = raw_pnl - fees

    held_min = (time.time() - position["entry_time"]) / 60
    sign     = "✅" if net > 0 else "🔴"
    print(f"  📄 [PAPER SCALP] {sign} EXIT {direction} {position['asset']} | "
          f"${entry:.2f} → ${exit_price:.2f} | {held_min:.1f}min | net={net:+.4f} USDC | {reason}")
    return net


def check_sl_tp(position: dict, price: float) -> tuple:
    """Returns (should_exit, reason, exit_price)."""
    sl, tp    = position["sl_price"], position["tp_price"]
    direction = position["direction"]

    if direction == "LONG":
        if price <= sl:
            return True, "Stop Loss", sl
        if price >= tp:
            return True, "Take Profit", tp
    else:
        if price >= sl:
            return True, "Stop Loss", sl
        if price <= tp:
            return True, "Take Profit", tp

    return False, "", price


def enter_position(asset: str, direction: str, margin_usd: float) -> dict:
    price = with_retry(get_mark_price, asset)
    if PAPER_MODE:
        return _paper_enter(asset, direction, margin_usd, price)
    raise NotImplementedError("Live scalping not yet enabled — set PAPER_MODE=true")


def exit_position(position: dict, price: float = None, reason: str = "Signal") -> float:
    if price is None:
        price = with_retry(get_mark_price, position["asset"])
    if position.get("paper", True) or PAPER_MODE:
        return _paper_exit(position, price, reason)
    raise NotImplementedError("Live scalping not yet enabled — set PAPER_MODE=true")
