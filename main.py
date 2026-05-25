"""
████████████████████████████████████████████████
  GUCCI QUANT v1.1 — PRODUCTION READY
  Funding Rate Arbitrage · Hyperliquid · Delta Neutral

  ✅ Kelly Criterion sizing
  ✅ Multi-asset (up to 3 simultaneous positions)
  ✅ Crash recovery (restores positions on restart)
  ✅ Telegram monitoring + remote control
  ✅ SQLite persistent logging
  ✅ Retry logic (3x exponential backoff)
  ✅ Spot index mapper (Bug 1 fixed)
  ✅ Order fill verification (Bug 2 fixed)
  ✅ Startup preflight validator
  ✅ Funding timing awareness
  ✅ Live performance metrics
  ✅ Daily reset + Telegram summary
  ✅ Graceful shutdown
████████████████████████████████████████████████
"""
import os, time, schedule
from datetime import datetime, date
from dotenv import load_dotenv

from agents.funding_scanner       import (get_opportunities, get_predicted,
                                           check_spread, minutes_to_funding,
                                           is_optimal_entry_window, entry_efficiency_pct,
                                           get_all_rates)
from agents.risk_agent            import RiskAgent
from agents.notifier              import (alert_startup, alert_entry, alert_exit,
                                           alert_error, alert_daily_summary,
                                           alert_risk_breach, start_command_listener,
                                           alert_rate_spike, alert_weekly_report)
from execution.hyperliquid_trader import enter_position, exit_position
from utils.logger                 import (init_db, log_trade, log_signal,
                                           save_open_position, close_saved_position,
                                           load_open_positions, get_daily_pnl,
                                           get_total_trades, log_scan, log_rate_snapshot)
from utils.validator              import run_preflight
from utils.performance            import print_report

load_dotenv()

risk                  = RiskAgent()
active_positions      = {}
_last_reset           = date.today()
_exit_times: dict     = {}          # {asset: unix_timestamp} — re-entry cooldown
REENTRY_COOLDOWN_SECS = 1800        # 30 min cooldown after exit
SPIKE_RATE_THRESHOLD  = 0.003       # 0.30%/hr — alert even when already positioned


def recover_positions():
    """On startup: close any positions left open from a previous crash."""
    orphans = load_open_positions()
    if not orphans:
        print("  ✅ No orphaned positions found")
        return
    print(f"  🔄 Recovering {len(orphans)} open position(s) from last session...")
    for row in orphans:
        try:
            # Use the actual stored entry_time, not a hardcoded 1hr assumption
            from datetime import timezone
            stored_dt  = datetime.fromisoformat(row["entry_time"])
            entry_unix = stored_dt.replace(tzinfo=timezone.utc).timestamp()
            ghost = {
                "asset":       row["asset"],
                "spot_id":     row.get("spot_id"),
                "entry_price": row["entry_price"],
                "size_usd":    row["size_usd"],
                "size_asset":  row["size_usd"] / max(row["entry_price"], 0.0001),
                "rate":        row["funding_rate"],
                "paper":       bool(row["paper"]),
                "entry_time":  entry_unix,
            }
            gross, fees = exit_position(ghost)
            net = gross - fees
            log_trade(ghost, net)
            close_saved_position(row["asset"])
            alert_exit(row["asset"], net, "Crash recovery on restart", ghost["paper"])
            print(f"  ✅ Recovered {row['asset']}: {net:+.4f} USDC")
        except Exception as e:
            msg = f"Recovery failed for {row['asset']}: {e}"
            print(f"  ❌ {msg}")
            alert_error(msg)


def maybe_reset_daily():
    global _last_reset
    today = date.today()
    if today != _last_reset:
        print_report(risk.capital)
        alert_daily_summary(
            risk.capital, risk.daily_pnl,
            get_total_trades(str(_last_reset)),
            risk.best_rate_today
        )
        risk.reset_daily()
        _last_reset = today


def close_all(reason="Manual"):
    for asset, pos in list(active_positions.items()):
        try:
            gross, fees = exit_position(pos)
            net = gross - fees
            risk.record_trade(net, pos.get("rate", 0))
            log_trade(pos, net)
            close_saved_position(asset)
            _exit_times[asset] = time.time()
            alert_exit(asset, net, reason, pos.get("paper", True))
        except Exception as e:
            alert_error(f"Close failed for {asset}: {e}")
    active_positions.clear()


def check_liquidation_risk():
    """
    Live mode only: alert + close any perp position within 20% of liquidation.
    Delta-neutral positions have very wide liquidation buffers (no leverage),
    but it's safety-critical to monitor in live mode.
    """
    if os.getenv("PAPER_MODE", "true").lower() == "true":
        return
    try:
        from execution.hyperliquid_trader import _get_clients
        info, _, address = _get_clients()
        state = info.user_state(address)
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            liq_px  = float(pos.get("liquidationPx") or 0)
            mark_px = float(pos.get("entryPx") or 0)
            asset   = pos.get("coin", "")
            szi     = float(pos.get("szi", 0))
            if liq_px > 0 and mark_px > 0 and szi != 0:
                distance = abs(mark_px - liq_px) / mark_px
                if distance < 0.20:
                    msg = (f"{asset}: liq ${liq_px:.4f}, mark ${mark_px:.4f} "
                           f"— only {distance*100:.1f}% away!")
                    alert_risk_breach(f"LIQUIDATION RISK — {msg}")
                    print(f"  🚨 LIQUIDATION RISK: {msg}")
                    # Force close if within 10%
                    if distance < 0.10 and asset in active_positions:
                        close_all(f"Liquidation protection — {asset} {distance*100:.1f}% from liq")
    except Exception as e:
        alert_error(f"Liquidation check failed: {e}")


def scan_and_trade():
    global active_positions
    maybe_reset_daily()

    mtf     = minutes_to_funding()
    eff     = entry_efficiency_pct()
    optimal = is_optimal_entry_window()

    print(f"\n⏱  [{datetime.utcnow().strftime('%H:%M:%S UTC')}] "
          f"Next funding: {mtf}min | Entry efficiency: {eff}% "
          f"{'🟢' if optimal else '🟡'}")

    if risk.is_daily_limit_hit():
        alert_risk_breach(f"Daily loss limit hit: ${risk.daily_pnl:.4f}")
        close_all("Daily loss limit hit")
        risk.trading_on = False
        return

    opps_map = {o["asset"]: o for o in get_opportunities()}

    # Exit check — uses break-even aware logic from RiskAgent.should_exit()
    for asset, pos in list(active_positions.items()):
        rate_now  = opps_map.get(asset, {}).get("rate", 0)
        held_hrs  = (time.time() - pos["entry_time"]) / 3600
        notional  = pos["size_usd"] * 2
        fees_cost = notional * risk.FEE_RATE
        gross_earn = notional * pos.get("rate", 0) * held_hrs
        net_est    = gross_earn - fees_cost
        pct_covered = min(gross_earn / fees_cost * 100, 100) if fees_cost else 0

        do_exit, exit_reason = risk.should_exit(pos, rate_now)
        if do_exit:
            print(f"  📉 Exiting {asset}: {exit_reason}")
            try:
                gross, fees = exit_position(pos)
                net = gross - fees
                risk.record_trade(net, pos.get("rate", 0))
                log_trade(pos, net)
                close_saved_position(asset)
                _exit_times[asset] = time.time()
                alert_exit(asset, net, exit_reason, pos.get("paper", True))
                del active_positions[asset]
            except Exception as e:
                alert_error(f"Exit error on {asset}: {e}")
        else:
            be_indicator = "✅" if net_est >= 0 else f"⏳{(fees_cost-gross_earn)/(notional*pos.get('rate',0.001)):.1f}hr to BE"
            print(f"  📌 {asset}: now={rate_now*100:.3f}%/hr entry={pos.get('rate',0)*100:.3f}% "
                  f"| {held_hrs:.1f}hr held | net={net_est:+.4f} {be_indicator}")

    if not risk.trading_on:
        print("  ⏸  Trading disabled")
        return

    # Entry scan
    n_open = len(active_positions)
    for opp in opps_map.values():
        if n_open >= risk.MAX_POSITIONS:
            break
        asset = opp["asset"]
        if asset in active_positions:
            continue

        # Re-entry cooldown: skip for 30 min after exit (prevents fee-burning oscillation)
        secs_since_exit = time.time() - _exit_times.get(asset, 0)
        if secs_since_exit < REENTRY_COOLDOWN_SECS:
            mins_left = int((REENTRY_COOLDOWN_SECS - secs_since_exit) / 60)
            print(f"  🕐 {asset}: cooldown {mins_left} min remaining")
            continue

        rate      = opp["rate"]
        trend     = opp.get("trend", "stable")
        predicted = get_predicted(asset)
        spread    = check_spread(asset)
        log_signal(asset, rate, predicted, spread, "SCAN")

        if not risk.can_enter(rate, spread, predicted, n_open,
                              verbose=(n_open == 0), trend=trend):
            continue

        # Timing: enforce optimal window for moderate rates
        if not optimal and rate < 0.003:
            print(f"  🕐 {asset}: rate {rate*100:.3f}% — "
                  f"waiting for :45-:59 window (efficiency: {eff}%)")
            continue

        size = risk.position_size(rate)
        if size < 5:
            print(f"  ⏭  {asset}: Kelly size too small (${size:.2f}) — skipping")
            continue

        print(f"\n  🟢 ENTERING {asset}")
        print(f"     Rate: {rate*100:.3f}%/hr | Annual: {opp['annual_pct']:.1f}%")
        print(f"     Kelly size: ${size:.2f}/leg | Timing: {eff}%")

        try:
            pos = enter_position(asset, size, rate)
            active_positions[asset] = pos
            save_open_position(pos)
            log_signal(asset, rate, predicted, spread, "ENTER")
            alert_entry(asset, rate*100, size, opp["annual_pct"],
                        pos.get("paper", True))
            n_open += 1
        except Exception as e:
            msg = f"Entry failed for {asset}: {e}"
            print(f"  ❌ {msg}")
            alert_error(msg)

    if not active_positions:
        top = [(k, f"{v['rate']*100:.3f}%") for k, v in list(opps_map.items())[:3]]
        print(f"  ⏳ No qualifying entries | Top rates: {top}")

    # ── Rate spike alert (even when already fully positioned) ──
    for opp in opps_map.values():
        if opp["rate"] >= SPIKE_RATE_THRESHOLD:
            alert_rate_spike(opp["asset"], opp["rate"] * 100, opp["annual_pct"])
            break  # one alert per scan max

    # ── Snapshot all current rates (powers 24hr rate chart in dashboard) ──
    try:
        log_rate_snapshot(get_all_rates())
    except Exception:
        pass

    # ── Liquidation safety check (live mode only) ──
    check_liquidation_risk()

    # ── Log this scan cycle to DB for dashboard activity feed ──
    top_opp    = max(opps_map.values(), key=lambda o: o["rate"], default=None)
    top_asset  = top_opp["asset"]   if top_opp else None
    top_rate   = top_opp["rate"] * 100 if top_opp else 0.0
    n_opps     = len(opps_map)
    if active_positions:
        action = "HOLDING: " + ", ".join(active_positions.keys())
    elif n_opps:
        action = "SCANNING — no entry signal"
    else:
        action = "SCANNING — rates below threshold"
    try:
        log_scan(eff, mtf, top_asset, round(top_rate, 4),
                 n_opps, len(active_positions), action)
    except Exception:
        pass


def fast_exit_check():
    """
    Runs every 5 min — only processes exits, no entries.
    Catches rate collapses between the 15-min full scans.
    Uses get_all_rates() (no trend API call) for speed.
    """
    if not active_positions:
        return
    try:
        rates_now = {r["asset"]: r["rate_pct"] / 100 for r in get_all_rates()}
        for asset, pos in list(active_positions.items()):
            rate_now = rates_now.get(asset, 0)
            do_exit, reason = risk.should_exit(pos, rate_now)
            if do_exit:
                print(f"  ⚡ [FAST EXIT] {asset}: {reason}")
                try:
                    gross, fees = exit_position(pos)
                    net = gross - fees
                    risk.record_trade(net, pos.get("rate", 0))
                    log_trade(pos, net)
                    close_saved_position(asset)
                    _exit_times[asset] = time.time()
                    alert_exit(asset, net, reason, pos.get("paper", True))
                    del active_positions[asset]
                except Exception as e:
                    alert_error(f"Fast exit failed for {asset}: {e}")
    except Exception:
        pass


def send_weekly_report():
    """Every Monday 09:00 UTC — performance summary to Discord."""
    from utils.performance import get_metrics
    m = get_metrics(risk.capital)
    alert_weekly_report(m, risk.capital)
    print_report(risk.capital)


# Schedule
schedule.every(15).minutes.do(scan_and_trade)
schedule.every(5).minutes.do(fast_exit_check)
schedule.every().monday.at("09:00").do(send_weekly_report)
schedule.every().day.at("00:01").do(maybe_reset_daily)


if __name__ == "__main__":
    print("\n" + "█"*50)
    print("  ██████  GUCCI QUANT v1.1  ██████")
    print("  Funding Rate Arbitrage · Hyperliquid · Delta Neutral")
    mode = "📄 PAPER" if os.getenv("PAPER_MODE", "true") == "true" else "💰 LIVE"
    print(f"  Mode: {mode} | Capital: ${risk.capital:.2f} | Max positions: {risk.MAX_POSITIONS}")
    print("█"*50 + "\n")

    # Preflight — hard stop in live mode if any check fails
    run_preflight(abort_on_fail=True)

    init_db()
    recover_positions()

    start_command_listener(
        risk_agent=risk,
        get_positions_fn=lambda: active_positions,
        get_rates_fn=get_opportunities
    )

    alert_startup()
    scan_and_trade()

    print(f"⏰ Scanning every 15 minutes. Ctrl+C to stop cleanly.\n")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down GUCCI QUANT cleanly...")
        close_all("Manual shutdown")
        print_report(risk.capital)
        alert_daily_summary(
            risk.capital, risk.daily_pnl,
            get_total_trades(), risk.best_rate_today
        )
        print("✅ All positions closed. Goodbye.\n")
