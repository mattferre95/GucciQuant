"""
GUCCI QUANT — Notifications
Primary: Discord webhook (one-way push, no setup friction)
Fallback: Telegram bot (if TOKEN + CHAT_ID set, also enables /commands)
Console: always prints regardless
"""
import os, requests, threading, time
from dotenv import load_dotenv
load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BASE    = f"https://api.telegram.org/bot{TOKEN}"


def _send(text: str):
    """Send to Discord, Telegram, or console — whichever is configured."""
    # Strip Telegram-style markdown for Discord (*bold* → **bold**)
    discord_text = text.replace("*", "**").replace("`", "`")

    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK,
                json={"content": discord_text}, timeout=5)
        except Exception as e:
            print(f"[DISCORD ERR] {e}")

    if TOKEN and CHAT_ID:
        try:
            requests.post(f"{BASE}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=5)
        except Exception as e:
            print(f"[TELEGRAM ERR] {e}")

    if not DISCORD_WEBHOOK and not (TOKEN and CHAT_ID):
        print(f"[NOTIFY] {text}")


def alert_startup():
    _send("🟢 **GUCCI QUANT v1.1 ONLINE**\nAll preflight checks passed. Scanning markets.")

def alert_risk_breach(reason: str):
    _send(f"🚨 **RISK BREACH**\n{reason}\nTrading suspended.")

def alert_error(msg: str):
    _send(f"⚠️ **ERROR**\n`{msg}`\nBot continues running.")

def alert_entry(asset, rate_pct, size_usd, annual_pct, paper=True):
    mode = "📄 PAPER" if paper else "💰 LIVE"
    _send(f"📈 **ENTRY** {mode}\n"
          f"Asset: `{asset}`\n"
          f"Rate: `{rate_pct:.3f}%/hr` ({annual_pct:.1f}%/yr)\n"
          f"Size: `${size_usd:.2f}` each leg")

def alert_exit(asset, net_pnl, reason, paper=True):
    e = "✅" if net_pnl >= 0 else "🔴"
    _send(f"{e} **EXIT** {'📄' if paper else '💰'}\n"
          f"Asset: `{asset}`\nNet: `{net_pnl:+.4f} USDC`\nReason: {reason}")

def alert_daily_summary(capital, daily_pnl, n_trades, best_rate):
    e = "📈" if daily_pnl >= 0 else "📉"
    _send(f"{e} **DAILY SUMMARY**\n"
          f"Capital: `${capital:.2f}`\n"
          f"Day PnL: `{daily_pnl:+.4f}`\n"
          f"Trades: `{n_trades}`\n"
          f"Best rate: `{best_rate:.3f}%/hr`")


# ── Telegram command listener (optional, only starts if TOKEN+CHAT_ID set) ──

_state = {"last_update": 0}


def _poll(risk_agent, get_pos_fn, get_rates_fn):
    while True:
        try:
            res = requests.get(f"{BASE}/getUpdates",
                params={"offset": _state["last_update"] + 1, "timeout": 10},
                timeout=15).json()
            for u in res.get("result", []):
                _state["last_update"] = u["update_id"]
                text = u.get("message", {}).get("text", "").strip().lower()
                cid  = str(u.get("message", {}).get("chat", {}).get("id", ""))
                if cid != CHAT_ID:
                    continue
                if text == "/status":
                    pos = get_pos_fn()
                    _send(f"🤖 *STATUS*\n"
                          f"State: `{'HOLDING' if pos else 'SCANNING'}`\n"
                          f"Capital: `${risk_agent.capital:.2f}`\n"
                          f"Day PnL: `{risk_agent.daily_pnl:+.4f}`\n"
                          f"Trading: `{'ON' if risk_agent.trading_on else 'OFF'}`")
                elif text == "/stop":
                    risk_agent.trading_on = False
                    _send("🛑 Trading *disabled* via Telegram.")
                elif text == "/start":
                    risk_agent.trading_on = True
                    _send("🟢 Trading *enabled* via Telegram.")
                elif text == "/pnl":
                    _send(f"💰 Day PnL: `{risk_agent.daily_pnl:+.4f}`\n"
                          f"Capital: `${risk_agent.capital:.2f}`")
                elif text == "/rates":
                    opps  = get_rates_fn()[:3]
                    lines = "\n".join([f"`{o['asset']}`: {o['rate_pct']:.3f}%/hr"
                                       for o in opps]) if opps else "None above threshold"
                    _send(f"📊 *TOP RATES*\n{lines}")
                elif text == "/positions":
                    pos = get_pos_fn()
                    if not pos:
                        _send("📭 No open positions.")
                    else:
                        lines = "\n".join([
                            f"`{a}`: ${p['size_usd']:.0f} @ {p['rate']*100:.3f}%/hr"
                            for a, p in pos.items()])
                        _send(f"📂 *POSITIONS*\n{lines}")
                elif text == "/help":
                    _send("*Commands:*\n/status /start /stop\n/pnl /rates /positions")
        except Exception:
            pass
        time.sleep(2)


def start_command_listener(risk_agent, get_positions_fn, get_rates_fn):
    if TOKEN and CHAT_ID:
        threading.Thread(
            target=_poll,
            args=(risk_agent, get_positions_fn, get_rates_fn),
            daemon=True
        ).start()
        print("  📱 Telegram command listener started")
    elif DISCORD_WEBHOOK:
        print("  💬 Discord notifications active (webhook — no commands)")
    else:
        print("  🖥️  Console-only mode (no notification service configured)")
