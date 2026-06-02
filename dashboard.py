"""
GUCCI QUANT — Dashboard v3 — Scalping Edition
EMA 9/21 + RSI 14 · BTC-PERP · 5m candles
Access: http://YOUR_VPS_IP:8080
"""
from flask import Flask, jsonify, render_template_string
import sqlite3, os, math, requests
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

app      = Flask(__name__)
DB_PATH  = os.getenv("DB_PATH", "data/gucci_quant.db")
LEVERAGE = int(os.getenv("SCALP_LEVERAGE", 5))
SL_PCT   = float(os.getenv("SCALP_SL_PCT", 0.005))
TP_PCT   = float(os.getenv("SCALP_TP_PCT", 0.010))


def query(sql, params=()):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def scalar(sql, params=(), default=0):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        val = conn.execute(sql, params).fetchone()[0]
        conn.close()
        return val if val is not None else default
    except Exception:
        return default


def get_mark_price(asset="BTC"):
    try:
        res = requests.post("https://api.hyperliquid.xyz/info",
                            json={"type": "metaAndAssetCtxs"}, timeout=5)
        meta, ctxs = res.json()
        for i, m in enumerate(meta["universe"]):
            if m["name"] == asset:
                return float(ctxs[i]["markPx"])
    except Exception:
        pass
    return None


@app.route("/api/stats")
def stats():
    starting_capital = float(os.getenv("STARTING_CAPITAL", "67"))
    today = date.today().isoformat()
    now   = datetime.utcnow()

    total_pnl    = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades")
    today_pnl    = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE date(timestamp)=?", (today,))
    total_trades = scalar("SELECT COUNT(*) FROM trades", default=0)
    wins         = scalar("SELECT COUNT(*) FROM trades WHERE net_pnl > 0", default=0)
    win_rate     = round(wins / total_trades * 100, 1) if total_trades else 0
    capital      = round(starting_capital + total_pnl, 2)
    total_fees   = scalar("SELECT COALESCE(SUM(fees),0) FROM trades")
    total_gross  = scalar("SELECT COALESCE(SUM(gross_pnl),0) FROM trades")

    # Sharpe
    daily_rows = query("SELECT SUM(net_pnl) as pnl FROM trades GROUP BY date(timestamp) ORDER BY date(timestamp)")
    sharpe = 0.0
    if len(daily_rows) >= 2:
        returns = [d["pnl"] for d in daily_rows]
        mean_r  = sum(returns) / len(returns)
        std_r   = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns))
        sharpe  = round(mean_r / std_r * math.sqrt(252), 2) if std_r > 0 else 0

    # Max drawdown
    equity, peak, max_dd = starting_capital, starting_capital, 0.0
    for t in query("SELECT net_pnl FROM trades ORDER BY timestamp ASC"):
        equity += t["net_pnl"]
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak if peak > 0 else 0
        max_dd  = max(max_dd, dd)
    max_drawdown_pct = round(max_dd * 100, 2)

    # Annualised return
    annual_return_pct = 0.0
    try:
        import sys
        if "/root/GucciQuant" not in sys.path:
            sys.path.insert(0, "/root/GucciQuant")
        from utils.performance import get_metrics
        annual_return_pct = get_metrics(capital).get("annual_return_pct", 0.0)
    except Exception:
        pass

    # Open positions with live mark price
    positions = query("SELECT * FROM positions WHERE status='open'")
    if positions:
        current_price = get_mark_price(positions[0].get("asset", "BTC"))
        for p in positions:
            try:
                mins = (now - datetime.fromisoformat(p["entry_time"])).total_seconds() / 60
                p["held_mins"] = round(mins, 1)
                margin   = p.get("size_usd") or 0
                notional = round(margin * LEVERAGE, 2)
                p["notional"]      = notional
                p["current_price"] = current_price
                if current_price and p.get("direction") and margin:
                    raw = notional * (current_price - p["entry_price"]) / p["entry_price"]
                    if p["direction"] == "SHORT":
                        raw = -raw
                    p["est_pnl"]   = round(raw - notional * 0.001, 4)
                    p["pct_to_sl"] = round(abs(current_price - (p.get("sl_price") or 0)) / p["entry_price"] * 100, 3) if p.get("sl_price") else None
                    p["pct_to_tp"] = round(abs(current_price - (p.get("tp_price") or 0)) / p["entry_price"] * 100, 3) if p.get("tp_price") else None
                else:
                    p["est_pnl"] = p["pct_to_sl"] = p["pct_to_tp"] = None
            except Exception:
                p["held_mins"] = p["est_pnl"] = p["notional"] = 0
                p["current_price"] = p["pct_to_sl"] = p["pct_to_tp"] = None

    # Trades + daily
    trades = query("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20")
    daily_breakdown = query("""
        SELECT date(timestamp) as day, COUNT(*) as trades,
               ROUND(SUM(gross_pnl),4) as gross, ROUND(SUM(fees),4) as fees,
               ROUND(SUM(net_pnl),4) as net
        FROM trades GROUP BY date(timestamp) ORDER BY day DESC LIMIT 30
    """)

    # Cumulative PnL
    cum, chart_labels, chart_data = 0, [], []
    for t in query("SELECT timestamp, net_pnl FROM trades ORDER BY timestamp ASC"):
        cum += t["net_pnl"]
        chart_labels.append(t["timestamp"][:16].replace("T", " "))
        chart_data.append(round(cum, 4))

    # Scan log — columns repurposed for scalping:
    #   efficiency   = signal code (100=LONG, 50=FLAT, 0=SHORT)
    #   mins_to_fund = RSI value
    #   top_rate_pct = EMA diff %
    scans = query("SELECT * FROM scan_log ORDER BY timestamp DESC LIMIT 96")

    last_signal = "—"
    if scans:
        eff = scans[0].get("efficiency", 50)
        last_signal = "LONG" if eff == 100 else ("SHORT" if eff == 0 else "FLAT")

    rsi_labels = [s["timestamp"][:16].replace("T", " ") for s in reversed(scans)]
    rsi_data   = [s.get("mins_to_fund", 50) for s in reversed(scans)]

    return jsonify({
        "capital":           capital,
        "total_pnl":         round(total_pnl, 4),
        "today_pnl":         round(today_pnl, 4),
        "total_trades":      total_trades,
        "win_rate":          win_rate,
        "annual_return_pct": annual_return_pct,
        "sharpe":            sharpe,
        "max_drawdown_pct":  max_drawdown_pct,
        "total_fees":        round(total_fees, 4),
        "total_gross":       round(total_gross, 4),
        "positions":         positions,
        "trades":            trades,
        "daily_breakdown":   daily_breakdown,
        "scans":             scans,
        "last_signal":       last_signal,
        "chart_labels":      chart_labels,
        "chart_data":        chart_data,
        "rsi_labels":        rsi_labels,
        "rsi_data":          rsi_data,
        "mode":              os.getenv("PAPER_MODE", "true").lower(),
        "last_updated":      now.strftime("%H:%M:%S UTC"),
        "config": {
            "asset":        os.getenv("SCALP_ASSET", "BTC"),
            "leverage":     LEVERAGE,
            "sl_pct":       SL_PCT * 100,
            "tp_pct":       TP_PCT * 100,
            "margin_pct":   float(os.getenv("SCALP_MARGIN_PCT", 0.5)) * 100,
            "daily_limit":  float(os.getenv("DAILY_LOSS_LIMIT", 5)),
        }
    })


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GUCCI QUANT</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#07070f;--card:#0c0c1a;--border:rgba(255,255,255,0.06);
  --green:#00e676;--red:#ff4060;--text:#e2e6f3;
  --muted:#3a3a5a;--accent:#7c6fff;--yellow:#ffd740;
}
html,body{height:100%;overflow:hidden}

/* Thin scrollbars */
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--muted);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:#5a5a8a}
*{scrollbar-width:thin;scrollbar-color:var(--muted) transparent}

body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Inter','SF Pro Text',sans-serif;font-size:12px}

/* Shell grid */
.shell{display:grid;grid-template-columns:180px 1fr 258px;grid-template-rows:44px 1fr 32px;
  grid-template-areas:"hdr hdr hdr""nav main aside""foot foot foot";height:100vh}

/* Header */
header{grid-area:hdr;display:flex;align-items:center;justify-content:space-between;
  padding:0 20px;background:var(--card);border-bottom:1px solid var(--border)}
.hdr-left{display:flex;flex-direction:column}
.hdr-title{font-size:12px;font-weight:700;color:var(--text);letter-spacing:3px;font-family:'SF Mono','Fira Code',monospace}
.hdr-title span{color:var(--accent)}
.hdr-sub{font-size:8px;color:var(--muted);letter-spacing:2px;margin-top:2px;font-family:'SF Mono',monospace}
.badge{padding:2px 8px;border-radius:4px;font-size:9px;font-weight:600;letter-spacing:1px;
  background:rgba(0,230,118,0.08);color:var(--green);border:1px solid rgba(0,230,118,0.2);font-family:'SF Mono',monospace}
.badge.live{background:rgba(255,64,96,0.08);color:var(--red);border-color:rgba(255,64,96,0.2)}
.hdr-right{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:10px}
.dot{width:5px;height:5px;border-radius:50%;background:var(--green);display:inline-block;
  margin-right:5px;animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

/* Left nav */
nav{grid-area:nav;background:var(--card);border-right:1px solid var(--border);
  overflow-y:auto;display:flex;flex-direction:column}
.brand{padding:13px 16px;border-bottom:1px solid var(--border)}
.brand-name{font-size:12px;font-weight:700;color:var(--text);letter-spacing:3px;font-family:'SF Mono','Fira Code',monospace}
.brand-name span{color:var(--accent)}
.brand-sub{font-size:8px;color:var(--muted);letter-spacing:2px;margin-top:3px;font-family:'SF Mono',monospace}
.nav-section{font-size:8px;color:var(--muted);letter-spacing:2.5px;padding:14px 16px 6px;text-transform:uppercase;font-weight:500}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 16px;cursor:pointer;
  color:var(--muted);font-size:11px;transition:color .15s,background .15s;
  border-left:2px solid transparent}
.nav-item:hover{color:var(--text);background:rgba(255,255,255,0.02)}
.nav-item.active{color:var(--text);border-left-color:var(--accent);background:rgba(124,111,255,0.05)}
.nav-icon{width:4px;height:4px;border-radius:50%;background:rgba(255,255,255,0.15);flex-shrink:0}
.nav-item.active .nav-icon{background:var(--accent)}
.nav-item:hover .nav-icon{background:rgba(255,255,255,0.35)}

/* Main */
main{grid-area:main;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:8px}

/* Metric cards */
.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:6px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:11px 13px}
.card-label{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-bottom:5px;font-weight:500}
.card-val{font-size:17px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums;font-family:'SF Mono','Fira Code',monospace}
.card-val.green{color:var(--green)}.card-val.red{color:var(--red)}.card-val.yellow{color:var(--yellow)}
.card-sub{font-size:9px;color:var(--muted);margin-top:4px}

/* Panels */
.panel{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.panel-hdr{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;
  margin-bottom:10px;font-weight:500;display:flex;align-items:center;gap:8px}
.panel-hdr::after{content:'';flex:1;height:1px;background:var(--border)}
.panel-sub{font-size:8px;color:var(--muted);margin-left:4px}
.two-col{display:grid;grid-template-columns:55% 1fr;gap:8px}

/* Tables */
table{width:100%;border-collapse:collapse}
th{font-size:8px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:1.5px;
  padding:0 10px 6px 0;text-align:left;border-bottom:1px solid var(--border)}
th:first-child{padding-left:0}
td{padding:6px 10px 6px 0;color:rgba(180,188,210,0.8);vertical-align:middle;font-size:11px;
  border-bottom:1px solid rgba(255,255,255,0.025)}
td:first-child{padding-left:0}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,0.018)}
.scroll-table{overflow-y:auto;max-height:150px}

/* Tags */
.tag{padding:2px 7px;border-radius:4px;font-size:9px;font-weight:600;letter-spacing:0.5px;display:inline-block;font-family:'SF Mono',monospace}
.tag.long {background:rgba(0,230,118,0.1);color:var(--green)}
.tag.short{background:rgba(255,64,96,0.1);color:var(--red)}
.tag.flat {background:rgba(255,255,255,0.05);color:var(--muted)}

.pos{color:var(--green)!important}.neg{color:var(--red)!important}.dim{color:var(--muted)}
.empty{text-align:center;padding:16px 0;color:var(--muted);font-size:11px;line-height:2}

/* Open position */
.pos-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:8px}
.pos-cell{background:rgba(255,255,255,0.028);border-radius:6px;padding:8px 10px}
.pos-cell-lbl{font-size:8px;color:var(--muted);letter-spacing:1.5px;margin-bottom:3px;text-transform:uppercase}
.pos-cell-val{font-size:13px;font-weight:700;font-family:'SF Mono','Fira Code',monospace}

/* Right sidebar */
aside{grid-area:aside;background:var(--card);border-left:1px solid var(--border);
  overflow-y:auto;display:flex;flex-direction:column}
.aside-section{padding:14px 16px;border-bottom:1px solid var(--border)}
.aside-section:last-child{border-bottom:none}
.aside-title{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px;font-weight:500}
.aside-row{display:flex;justify-content:space-between;align-items:center;
  padding:5px 0;font-size:11px;border-bottom:1px solid rgba(255,255,255,0.025)}
.aside-row:last-child{border-bottom:none}

/* Footer */
footer{grid-area:foot;display:flex;align-items:center;gap:18px;
  padding:0 18px;background:var(--card);border-top:1px solid var(--border);font-size:9px;color:var(--muted)}
.status-item{display:flex;align-items:center;gap:5px}
.status-dot{width:5px;height:5px;border-radius:50%;flex-shrink:0}
.status-dot.online{background:var(--green)}.status-dot.standby{background:var(--muted)}
.footer-right{margin-left:auto;display:flex;align-items:center;gap:10px}

/* Refresh button */
.btn{background:rgba(255,255,255,0.04);color:var(--muted);border:1px solid var(--border);
  border-radius:5px;padding:3px 10px;cursor:pointer;font-size:9px;font-family:inherit;transition:color .15s,background .15s}
.btn:hover{color:var(--text);background:rgba(255,255,255,0.07)}
</style>

/* ── Shell grid ── */
.shell{
  display:grid;
  grid-template-columns:190px 1fr 272px;
  grid-template-rows:48px 1fr 34px;
  grid-template-areas:"hdr hdr hdr""nav main aside""foot foot foot";
  height:100vh;
}

/* ── Header ── */
header{
  grid-area:hdr;display:flex;align-items:center;justify-content:space-between;
  padding:0 20px;background:var(--card);border-bottom:1px solid var(--border);
}
.hdr-left{display:flex;flex-direction:column}
.hdr-title{font-size:13px;font-weight:700;color:var(--text);letter-spacing:2px}
.hdr-title span{color:var(--accent)}
.hdr-sub{font-size:9px;color:var(--muted);letter-spacing:1.5px;margin-top:2px}
.badge{padding:2px 9px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:1px;
  background:#0a2a18;color:var(--green);border:1px solid var(--green)}
.badge.live{background:#2a0a10;color:var(--red);border-color:var(--red)}
.hdr-right{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:10px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;
  margin-right:5px;animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

/* ── Left sidebar ── */
nav{
  grid-area:nav;background:var(--card);border-right:1px solid var(--border);
  padding:16px 0;display:flex;flex-direction:column;overflow-y:auto;
}
.brand{padding:0 16px 16px;border-bottom:1px solid var(--border);margin-bottom:12px}
.brand-name{font-size:14px;font-weight:700;color:var(--green);letter-spacing:2px}
.brand-name span{color:var(--accent)}
.brand-sub{font-size:9px;color:var(--muted);letter-spacing:1.5px;margin-top:2px}
.nav-section{font-size:9px;color:var(--muted);letter-spacing:2px;padding:0 16px 6px;
  text-transform:uppercase;margin-top:4px}
.nav-item{display:flex;align-items:center;gap:8px;padding:7px 16px;cursor:pointer;
  color:var(--muted);font-size:11px;transition:color .15s}
.nav-item:hover{color:var(--text)}
.nav-item.active{color:var(--text)}
.nav-icon{width:11px;height:11px;border:1px solid currentColor;border-radius:2px;flex-shrink:0}
.nav-item.active .nav-icon{background:var(--accent);border-color:var(--accent)}

/* ── Main ── */
main{grid-area:main;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:10px}

/* ── Metric cards ── */
.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.card-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}
.card-val{font-size:18px;font-weight:700;line-height:1}
.card-val.green{color:var(--green)}.card-val.red{color:var(--red)}.card-val.yellow{color:var(--yellow)}
.card-sub{font-size:10px;color:var(--muted);margin-top:4px}

/* ── Panels ── */
.panel{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.panel-hdr{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;
  margin-bottom:10px;display:flex;align-items:center;gap:8px}
.panel-hdr::after{content:'';flex:1;height:1px;background:var(--border)}
.panel-sub{font-size:9px;color:var(--muted);margin-left:6px}

.two-col{display:grid;grid-template-columns:55% 1fr;gap:10px}

/* ── Tables ── */
table{width:100%;border-collapse:collapse}
th{font-size:9px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:1px;
  padding:0 0 6px;text-align:left;border-bottom:1px solid var(--border)}
td{padding:7px 0;border-bottom:1px solid #0d0d1a;color:#b0b8d0;vertical-align:middle;font-size:11px}
tr:last-child td{border-bottom:none}
.scroll-table{overflow-y:auto;max-height:160px}

/* ── Tags ── */
.tag{padding:2px 7px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:1px;
  border:1px solid;display:inline-block}
.tag.long {background:#0a2010;color:var(--green);border-color:#1a4a2a}
.tag.short{background:#200a10;color:var(--red);border-color:#4a1a2a}
.tag.flat {background:#141424;color:var(--muted);border-color:#2a2a4a}
.tag.asset{background:#141424;color:var(--accent);border-color:#2a2a4a}

.pos{color:var(--green)!important}.neg{color:var(--red)!important}.dim{color:var(--muted)}
.empty{text-align:center;padding:18px 0;color:var(--muted);font-size:11px;line-height:2}

/* ── Open position grid ── */
.pos-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px}
.pos-cell{background:#0a0a18;border-radius:5px;padding:8px 10px}
.pos-cell-lbl{font-size:9px;color:var(--muted);letter-spacing:1px;margin-bottom:3px;text-transform:uppercase}
.pos-cell-val{font-size:13px;font-weight:700}

/* ── Right sidebar ── */
aside{
  grid-area:aside;background:var(--card);border-left:1px solid var(--border);
  padding:14px 0;overflow-y:auto;display:flex;flex-direction:column;
}
.aside-section{padding:0 16px 12px;border-bottom:1px solid var(--border);margin-bottom:12px}
.aside-section:last-child{border-bottom:none;margin-bottom:0}
.aside-title{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px}
.aside-row{display:flex;justify-content:space-between;align-items:center;
  padding:5px 0;border-bottom:1px solid #0d0d1a;font-size:11px}
.aside-row:last-child{border-bottom:none}

/* ── Footer ── */
footer{
  grid-area:foot;display:flex;align-items:center;gap:20px;
  padding:0 20px;background:var(--card);border-top:1px solid var(--border);font-size:10px;
}
.status-item{display:flex;align-items:center;gap:6px}
.status-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.status-dot.online{background:var(--green)}
.status-dot.standby{background:var(--red)}
.footer-right{margin-left:auto;display:flex;align-items:center;gap:10px}
</style>
</head>
<body>
<div class="shell">

<!-- ── Header ── -->
<header>
  <div class="hdr-left">
    <div class="hdr-title">⚡ GUCCI <span>QUANT</span></div>
    <div class="hdr-sub">EMA SCALPING · BTC-PERP · 5M</div>
  </div>
  <div class="hdr-right">
    <span><span class="dot"></span><span id="upd">Loading…</span></span>
    <span class="badge" id="badge">PAPER</span>
    <button onclick="load()" class="btn">Refresh</button>
  </div>
</header>

<!-- ── Left nav ── -->
<nav>
  <div class="brand">
    <div class="brand-name">GUCCI <span>QUANT</span></div>
    <div class="brand-sub">GRAPHITE TERMINAL</div>
  </div>
  <div class="nav-section">Overview</div>
  <div class="nav-item active"><div class="nav-icon"></div>Dashboard</div>
  <div class="nav-item"><div class="nav-icon"></div>Positions</div>
  <div class="nav-item"><div class="nav-icon"></div>Signal Log</div>
  <div class="nav-item"><div class="nav-icon"></div>Performance</div>
  <div class="nav-item"><div class="nav-icon"></div>Logs</div>
</nav>

<!-- ── Main content ── -->
<main>

  <!-- Metric cards -->
  <div class="metrics">
    <div class="card">
      <div class="card-label">Capital</div>
      <div class="card-val" id="capital">—</div>
      <div class="card-sub">USDC</div>
    </div>
    <div class="card">
      <div class="card-label">All-time PnL</div>
      <div class="card-val" id="allpnl">—</div>
      <div class="card-sub">net of fees</div>
    </div>
    <div class="card">
      <div class="card-label">Today</div>
      <div class="card-val" id="daypnl">—</div>
      <div class="card-sub" id="today-date">—</div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-val" id="winrate">—</div>
      <div class="card-sub" id="tradect">—</div>
    </div>
    <div class="card">
      <div class="card-label">Sharpe</div>
      <div class="card-val" id="sharpe">—</div>
      <div class="card-sub">annualised</div>
    </div>
    <div class="card">
      <div class="card-label">Max Drawdown</div>
      <div class="card-val" id="maxdd">—</div>
      <div class="card-sub">peak-to-trough</div>
    </div>
  </div>

  <!-- PnL chart + Open position -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-hdr">Cumulative PnL</div>
      <canvas id="pnl-chart" height="90"></canvas>
      <div id="no-pnl" class="empty" style="display:none">Chart appears after first closed trade</div>
    </div>
    <div class="panel">
      <div class="panel-hdr">Open Position</div>
      <div id="pos"></div>
    </div>
  </div>

  <!-- RSI chart -->
  <div class="panel">
    <div class="panel-hdr">RSI 14 History <span class="panel-sub">— last 96 scans · ref lines: 30 / 50 / 70</span></div>
    <canvas id="rsi-chart" height="55"></canvas>
    <div id="no-rsi" class="empty" style="display:none">Populates after first scan cycle</div>
  </div>

  <!-- Signal log -->
  <div class="panel">
    <div class="panel-hdr">Signal Log <span class="panel-sub">— 5 min scans</span></div>
    <div class="scroll-table" id="scanlog"></div>
  </div>

</main>

<!-- ── Right sidebar ── -->
<aside>

  <div class="aside-section">
    <div class="aside-title">Strategy Config</div>
    <div id="cfg"></div>
  </div>

  <div class="aside-section">
    <div class="aside-title">Live Signal</div>
    <div id="live-sig"></div>
  </div>

  <div class="aside-section">
    <div class="aside-title">Recent Activity</div>
    <div id="recent"></div>
  </div>

</aside>

<!-- ── Footer ── -->
<footer>
  <div class="status-item">
    <div class="status-dot online"></div>
    <span class="dim">System feed</span>
    <span style="color:var(--green)">ONLINE</span>
  </div>
  <div class="status-item">
    <div class="status-dot standby" id="mkt-dot"></div>
    <span class="dim">Market</span>
    <span id="mkt-status">STANDBY</span>
  </div>
  <div class="footer-right">
    <span class="dim" id="footer-capital">—</span>
    <span class="badge" id="footer-badge">PAPER</span>
  </div>
</footer>

</div><!-- .shell -->

<script>
let pnlChart=null, rsiChart=null;
const fmt=(v,d=4)=>{const n=+v;return(n>=0?'+':'')+n.toFixed(d)};
const cls=v=>+v>=0?'pos':'neg';
const $=id=>document.getElementById(id);
const sigTag=s=>{const c=s==='LONG'?'long':s==='SHORT'?'short':'flat';return`<span class="tag ${c}">${s}</span>`};

async function load(){
  try{
    const d=await fetch('/api/stats').then(r=>r.json());

    // Header + footer
    $('upd').textContent='UPDATED '+d.last_updated;
    const isPaper=d.mode!=='false';
    $('badge').textContent=isPaper?'PAPER':'LIVE';
    $('badge').className=isPaper?'badge':'badge live';
    $('footer-badge').textContent=isPaper?'PAPER':'LIVE';
    $('footer-badge').className=isPaper?'badge':'badge live';
    $('footer-capital').textContent='$'+d.capital.toFixed(2)+' USDC';

    // Market status (has open position = ACTIVE, else STANDBY)
    const hasPos=d.positions&&d.positions.length>0;
    $('mkt-status').textContent=hasPos?'ACTIVE':'STANDBY';
    $('mkt-status').style.color=hasPos?'var(--green)':'var(--red)';
    $('mkt-dot').className='status-dot '+(hasPos?'online':'standby');

    // Metric cards
    $('capital').textContent='$'+d.capital.toFixed(2);

    const ap=$('allpnl');ap.textContent=fmt(d.total_pnl)+' USDC';ap.className='card-val '+cls(d.total_pnl);
    const dp=$('daypnl');dp.textContent=fmt(d.today_pnl)+' USDC';dp.className='card-val '+cls(d.today_pnl);
    $('today-date').textContent=new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});

    const wr=$('winrate');wr.textContent=d.win_rate+'%';wr.className='card-val '+(d.win_rate>=50?'green':'red');
    $('tradect').textContent=d.total_trades+' trades';

    const sh=$('sharpe');sh.textContent=d.sharpe||'—';sh.className='card-val '+(d.sharpe>1?'green':d.sharpe<0?'red':'yellow');
    const md=$('maxdd');md.textContent=d.max_drawdown_pct+'%';md.className='card-val '+(d.max_drawdown_pct<5?'green':d.max_drawdown_pct<15?'yellow':'red');

    // PnL chart
    if(d.chart_data.length>0){
      $('no-pnl').style.display='none';$('pnl-chart').style.display='block';
      const last=d.chart_data[d.chart_data.length-1];const col=last>=0?'#00e676':'#ff1744';
      if(pnlChart){pnlChart.data.labels=d.chart_labels;pnlChart.data.datasets[0].data=d.chart_data;
        pnlChart.data.datasets[0].borderColor=col;pnlChart.data.datasets[0].backgroundColor=col+'18';pnlChart.update('none');}
      else pnlChart=new Chart($('pnl-chart').getContext('2d'),{type:'line',data:{labels:d.chart_labels,datasets:[{
        data:d.chart_data,borderColor:col,backgroundColor:col+'18',borderWidth:2,
        pointRadius:d.chart_data.length<30?3:0,tension:0.35,fill:true}]},
        options:{responsive:true,animation:false,plugins:{legend:{display:false},
          tooltip:{callbacks:{label:c=>(c.parsed.y>=0?'+':'')+c.parsed.y.toFixed(4)+' USDC'}}},
          scales:{x:{ticks:{color:'#4a4a6a',maxTicksLimit:6,font:{size:9}},grid:{color:'#12121e'}},
            y:{ticks:{color:'#4a4a6a',font:{size:9},callback:v=>(v>=0?'+':'')+v.toFixed(3)},grid:{color:'#12121e'}}}}});
    }else{$('pnl-chart').style.display='none';$('no-pnl').style.display='block';}

    // RSI chart
    if(d.rsi_data&&d.rsi_data.length>0){
      $('no-rsi').style.display='none';$('rsi-chart').style.display='block';
      if(rsiChart){rsiChart.data.labels=d.rsi_labels;rsiChart.data.datasets[0].data=d.rsi_data;rsiChart.update('none');}
      else rsiChart=new Chart($('rsi-chart').getContext('2d'),{type:'line',data:{labels:d.rsi_labels,datasets:[
        {label:'RSI',data:d.rsi_data,borderColor:'#7c6fff',backgroundColor:'#7c6fff10',borderWidth:1.5,pointRadius:0,tension:0.3,fill:true},
        {data:d.rsi_data.map(()=>70),borderColor:'#ff174440',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false},
        {data:d.rsi_data.map(()=>50),borderColor:'#4a4a6a',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false},
        {data:d.rsi_data.map(()=>30),borderColor:'#00e67640',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false},
      ]},options:{responsive:true,animation:false,plugins:{legend:{display:false}},
        scales:{x:{ticks:{color:'#4a4a6a',maxTicksLimit:8,font:{size:9}},grid:{color:'#12121e'}},
          y:{min:0,max:100,ticks:{color:'#4a4a6a',font:{size:9},stepSize:25},grid:{color:'#12121e'}}}}});
    }else{$('rsi-chart').style.display='none';$('no-rsi').style.display='block';}

    // Open position
    const posEl=$('pos');
    if(!d.positions||!d.positions.length){
      posEl.innerHTML=`<div class="empty">No position open<br><span style="font-size:10px">Last signal: ${sigTag(d.last_signal)}</span></div>`;
    }else{
      const p=d.positions[0];const dir=p.direction||'LONG';
      posEl.innerHTML=`
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          ${sigTag(dir)}<span style="font-weight:700;font-size:13px">${p.asset}-PERP</span>
          <span class="dim" style="margin-left:auto">${p.held_mins}min held</span>
        </div>
        <div class="pos-grid">
          <div class="pos-cell"><div class="pos-cell-lbl">Entry</div><div class="pos-cell-val">$${(+p.entry_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}</div></div>
          <div class="pos-cell"><div class="pos-cell-lbl">Current</div><div class="pos-cell-val">${p.current_price?'$'+(+p.current_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div></div>
          <div class="pos-cell"><div class="pos-cell-lbl">Stop Loss</div><div class="pos-cell-val neg">${p.sl_price?'$'+(+p.sl_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>${p.pct_to_sl!=null?`<div class="dim" style="font-size:9px">${p.pct_to_sl}% away</div>`:''}</div>
          <div class="pos-cell"><div class="pos-cell-lbl">Take Profit</div><div class="pos-cell-val pos">${p.tp_price?'$'+(+p.tp_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>${p.pct_to_tp!=null?`<div class="dim" style="font-size:9px">${p.pct_to_tp}% away</div>`:''}</div>
        </div>
        <div style="display:flex;justify-content:space-between;padding-top:8px;border-top:1px solid var(--border)">
          <span class="dim" style="font-size:10px">$${(+p.size_usd||0).toFixed(2)} × ${d.config.leverage}x = $${(+p.notional||0).toFixed(2)}</span>
          <span class="${cls(p.est_pnl||0)}" style="font-weight:700">${fmt(p.est_pnl||0)} USDC</span>
        </div>`;
    }

    // Signal log
    const slEl=$('scanlog');
    if(!d.scans||!d.scans.length){
      slEl.innerHTML='<div class="empty">No scan data yet</div>';
    }else{
      let h=`<table><thead><tr><th>Time UTC</th><th>Signal</th><th>RSI</th><th>EMA Δ%</th><th>Pos</th><th>Details</th></tr></thead><tbody>`;
      for(const s of d.scans){
        const ts=(s.timestamp||'').substring(5,16).replace('T',' ');
        const eff=s.efficiency!=null?s.efficiency:50;
        const sig=eff===100?'LONG':eff===0?'SHORT':'FLAT';
        const rsi=s.mins_to_fund!=null?s.mins_to_fund:'—';
        const rsiCls=rsi>70?'neg':rsi<30?'pos':'dim';
        const ed=s.top_rate_pct!=null?((+s.top_rate_pct)>=0?'+':'')+(+s.top_rate_pct).toFixed(4)+'%':'—';
        const posIcon=s.open_positions?'<span class="pos">●</span>':'<span class="dim">○</span>';
        h+=`<tr><td class="dim">${ts}</td><td>${sigTag(sig)}</td><td class="${rsiCls}">${rsi}</td><td class="dim">${ed}</td><td style="text-align:center">${posIcon}</td><td class="dim" style="font-size:10px;max-width:200px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${s.action||'—'}</td></tr>`;
      }
      slEl.innerHTML=h+'</tbody></table>';
    }

    // Right sidebar — Strategy config
    const cfg=d.config||{};
    $('cfg').innerHTML=`
      <div class="aside-row"><span class="dim">Asset</span><span>${cfg.asset}-PERP</span></div>
      <div class="aside-row"><span class="dim">Timeframe</span><span>5m candles</span></div>
      <div class="aside-row"><span class="dim">EMA Fast / Slow</span><span>9 / 21</span></div>
      <div class="aside-row"><span class="dim">RSI Period</span><span>14 · threshold 50</span></div>
      <div class="aside-row"><span class="dim">Leverage</span><span>${cfg.leverage}x</span></div>
      <div class="aside-row"><span class="dim">Margin / trade</span><span>${cfg.margin_pct}%</span></div>
      <div class="aside-row"><span class="dim">Stop Loss</span><span class="neg">${cfg.sl_pct}%</span></div>
      <div class="aside-row"><span class="dim">Take Profit</span><span class="pos">${cfg.tp_pct}%</span></div>
      <div class="aside-row"><span class="dim">Daily limit</span><span class="neg">-$${cfg.daily_limit}</span></div>`;

    // Right sidebar — Live signal
    const lsEl=$('live-sig');
    if(!d.scans||!d.scans.length){
      lsEl.innerHTML='<div class="dim" style="font-size:11px;padding:6px 0">No scan data yet</div>';
    }else{
      const s=d.scans[0];
      const eff=s.efficiency!=null?s.efficiency:50;
      const sig=eff===100?'LONG':eff===0?'SHORT':'FLAT';
      const rsi=s.mins_to_fund||'—';
      const action=s.action||'';
      // Parse EMA9, EMA21, Price from action string
      const ema9Match=action.match(/EMA9=([\d.]+)/);
      const ema21Match=action.match(/EMA21=([\d.]+)/);
      const priceMatch=action.match(/\$([\d.]+)/);
      lsEl.innerHTML=`
        <div style="margin-bottom:8px">${sigTag(sig)}</div>
        <div class="aside-row"><span class="dim">RSI 14</span><span class="${rsi>70?'neg':rsi<30?'pos':''}">${rsi}</span></div>
        <div class="aside-row"><span class="dim">EMA 9</span><span>${ema9Match?parseFloat(ema9Match[1]).toFixed(0):'—'}</span></div>
        <div class="aside-row"><span class="dim">EMA 21</span><span>${ema21Match?parseFloat(ema21Match[1]).toFixed(0):'—'}</span></div>
        <div class="aside-row"><span class="dim">Price</span><span>${priceMatch?'$'+parseFloat(priceMatch[1]).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</span></div>
        <div class="aside-row"><span class="dim">Opportunities</span><span>${s.opportunities||0}</span></div>
        <div class="aside-row"><span class="dim">Scanner</span><span class="dim" style="font-size:10px">${sig} · ${rsi>70?'OVERBOUGHT':rsi<30?'OVERSOLD':'NEUTRAL'}</span></div>`;
    }

    // Right sidebar — Recent activity
    const recEl=$('recent');
    const recentTrades=d.trades?d.trades.slice(0,4):[];
    if(!recentTrades.length){
      recEl.innerHTML='<div class="dim" style="font-size:11px;padding:6px 0">No completed trades yet</div>';
    }else{
      let h='';
      for(const t of recentTrades){
        const ts=(t.timestamp||'').substring(5,16).replace('T',' ');
        const dur=t.duration_hrs?((+t.duration_hrs)*60).toFixed(0)+'min':'—';
        h+=`<div class="aside-row">
          <span class="dim" style="font-size:10px">${ts}</span>
          <span class="${cls(t.net_pnl)}" style="font-size:11px">${fmt(t.net_pnl,4)}</span>
        </div>`;
      }
      recEl.innerHTML=h;
    }

  }catch(e){$('upd').textContent='⚠ Connection error — retrying…';}
}
load();
setInterval(load,30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    print(f"\n{'─'*52}")
    print(f"  📊  GUCCI QUANT DASHBOARD v3 — Scalping Edition")
    print(f"  Open: http://0.0.0.0:{port}")
    print(f"{'─'*52}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
