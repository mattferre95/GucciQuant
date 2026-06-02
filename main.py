"""
GUCCI QUANT v2.0 — EMA 9/21 + RSI 14 Scalping · BTC-PERP · 5m
5x leverage · SL 0.5% · TP 1.0% · Paper mode by default
"""
import os, time, schedule
from datetime import datetime, date
from dotenv import load_dotenv

from agents.scalping_agent        import get_signal
from agents.notifier              import (alert_startup, alert_error, alert_exit,
                                           alert_daily_summary, start_command_listener)
from execution.scalping_trader    import enter_position, exit_position, check_sl_tp
from execution.hyperliquid_trader import get_mark_price, with_retry
from utils.logger                 import init_db, log_trade
from utils.performance            import print_report

load_dotenv()

ASSET            = os.getenv("SCALP_ASSET", "BTC")
MARGIN_PCT       = float(os.getenv("SCALP_MARGIN_PCT", 0.50))
PAPER_MODE       = os.getenv("PAPER_MODE", "true").lower() == "true"
DAILY_LOSS_LIMIT = -float(os.getenv("DAILY_LOSS_LIMIT", 5))

capital         = float(os.getenv("STARTING_CAPITAL", 67))
daily_pnl       = 0.0
daily_trades    = 0
_last_reset     = date.today()
active_position = None
trading_on      = True


class _BotState:
    """Thin adapter so the Telegram listener can read/write bot state."""
    @property
    def capital(self):      return capital
    @property
    def daily_pnl(self):    return daily_pnl
    @property
    def trading_on(self):   return trading_on
    @trading_on.setter
    def trading_on(self, v):
        global trading_on
        trading_on = v

_state = _BotState()


def maybe_reset_daily():
    global _last_reset, daily_pnl, daily_trades
    today = date.today()
    if today != _last_reset:
        print_report(capital)
        alert_daily_summary(capital, daily_pnl, daily_trades, 0)
        daily_pnl    = 0.0
        daily_trades = 0
        _last_reset  = today


def _close(position: dict, price: float, reason: str):
    """Exit position, update global state, and log the trade."""
    global capital, daily_pnl, daily_trades, active_position
    net          = exit_position(position, price, reason)
    duration_hrs = round((time.time() - position["entry_time"]) / 3600, 4)
    capital      += net
    daily_pnl    += net
    daily_trades += 1
    position["size_usd"] = position["margin_usd"]   # logger compatibility
    log_trade(position, net, exit_price=price, duration_hrs=duration_hrs)
    alert_exit(position["asset"], net, reason, position.get("paper", True))
    active_position = None
    print(f"  {'✅' if net > 0 else '❌'} {reason}: {net:+.4f} USDC | Capital: ${capital:.2f}")


def scan_and_trade():
    global active_position
    maybe_reset_daily()

    mode = "PAPER" if PAPER_MODE else "LIVE"
    now  = datetime.utcnow().strftime("%H:%M:%S UTC")
    print(f"\n⏱  [{now}] [{mode}] {ASSET} 5m scan...")

    if not trading_on:
        print("  ⏸  Trading disabled")
        return

    if daily_pnl <= DAILY_LOSS_LIMIT:
        print(f"  🛑 Daily loss limit hit ({daily_pnl:.4f}) — trading paused")
        return

    # ── 1. Current mark price ─────────────────────────────────────
    try:
        price = with_retry(get_mark_price, ASSET)
    except Exception as e:
        alert_error(f"Price fetch failed: {e}")
        return

    # ── 2. SL/TP check on open position ──────────────────────────
    if active_position:
        hit, reason, exit_px = check_sl_tp(active_position, price)
        if hit:
            try:
                _close(active_position, exit_px, reason)
            except Exception as e:
                alert_error(f"SL/TP exit failed: {e}")
            return

    # ── 3. EMA + RSI signal ───────────────────────────────────────
    try:
        sig = get_signal(ASSET)
    except Exception as e:
        alert_error(f"Signal fetch failed: {e}")
        return

    signal, ef, es, rsi = sig["signal"], sig["ema_fast"], sig["ema_slow"], sig["rsi"]
    print(f"  Signal: {signal:5s} | EMA9={ef}  EMA21={es}  RSI={rsi}  Price=${price:.2f}")

    # ── 4. Exit if signal reverses direction ──────────────────────
    if active_position:
        direction = active_position["direction"]
        if (direction == "LONG" and signal == "SHORT") or \
           (direction == "SHORT" and signal == "LONG"):
            try:
                _close(active_position, price, "Signal Reversed")
            except Exception as e:
                alert_error(f"Signal reversal exit failed: {e}")
            return

        if signal == "FLAT":
            held = (time.time() - active_position["entry_time"]) / 60
            print(f"  📌 Holding {direction} | {held:.1f}min elapsed")
        return

    # ── 5. Enter new position ─────────────────────────────────────
    if signal in ("LONG", "SHORT"):
        margin = round(capital * MARGIN_PCT, 2)
        if margin < 5:
            print(f"  ⏭  Margin too small (${margin:.2f}) — skipping")
            return
        try:
            active_position = enter_position(ASSET, signal, margin)
        except Exception as e:
            alert_error(f"Entry failed: {e}")
    else:
        print(f"  ⏳ No signal — waiting")


schedule.every(5).minutes.do(scan_and_trade)
schedule.every().day.at("00:01").do(maybe_reset_daily)


if __name__ == "__main__":
    print("\n" + "█" * 50)
    print("  ██████  GUCCI QUANT v2.0  ██████")
    print(f"  EMA {os.getenv('SCALP_EMA_FAST', 9)}/{os.getenv('SCALP_EMA_SLOW', 21)} "
          f"+ RSI 14 Scalping · {ASSET}-PERP · 5m candles")
    mode = "📄 PAPER" if PAPER_MODE else "💰 LIVE"
    print(f"  Mode: {mode} | Capital: ${capital:.2f} | Asset: {ASSET}")
    print(f"  Margin/trade: {MARGIN_PCT*100:.0f}% (${capital * MARGIN_PCT:.2f}) | "
          f"Leverage: {os.getenv('SCALP_LEVERAGE', 5)}x | "
          f"SL: {float(os.getenv('SCALP_SL_PCT', 0.005))*100:.1f}% | "
          f"TP: {float(os.getenv('SCALP_TP_PCT', 0.010))*100:.1f}%")
    print("█" * 50 + "\n")

    init_db()
    start_command_listener(
        risk_agent=_state,
        get_positions_fn=lambda: {ASSET: active_position} if active_position else {},
        get_rates_fn=lambda: [],
    )
    alert_startup()
    scan_and_trade()

    print("⏰ Scanning every 5 minutes. Ctrl+C to stop cleanly.\n")

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down GUCCI QUANT v2.0...")
        if active_position:
            try:
                px = with_retry(get_mark_price, ASSET)
                _close(active_position, px, "Manual shutdown")
            except Exception as e:
                print(f"  ❌ Shutdown close failed: {e}")
        print_report(capital)
        print("✅ All done. Goodbye.\n")
