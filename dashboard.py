"""
GUCCI QUANT — Visual Dashboard v2
Runs alongside the bot in a separate screen session.
Access: http://YOUR_VPS_IP:8080
"""
from flask import Flask, jsonify, render_template_string
import sqlite3, os, math, sys
from datetime import datetime, date
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "data/gucci_quant.db")


# ── DB helpers ───────────────────────────────────────────────────────────────

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


# ── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    starting_capital = float(os.getenv("STARTING_CAPITAL", "67"))
    today = date.today().isoformat()

    # ── Core metrics ──
    total_pnl    = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades")
    today_pnl    = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE date(timestamp)=?", (today,))
    total_trades = scalar("SELECT COUNT(*) FROM trades", default=0)
    wins         = scalar("SELECT COUNT(*) FROM trades WHERE net_pnl > 0", default=0)
    win_rate     = round(wins / total_trades * 100, 1) if total_trades else 0
    capital      = round(starting_capital + total_pnl, 2)
    best_rate    = scalar("SELECT COALESCE(MAX(funding_rate),0) FROM trades WHERE date(timestamp)=?", (today,))
    total_fees   = scalar("SELECT COALESCE(SUM(fees),0) FROM trades")
    total_gross  = scalar("SELECT COALESCE(SUM(gross_pnl),0) FROM trades")

    # ── Sharpe ratio & max drawdown ──
    daily_rows = query("""
        SELECT date(timestamp) as day, SUM(net_pnl) as pnl
        FROM trades GROUP BY date(timestamp) ORDER BY day
    """)
    sharpe = 0.0
    if len(daily_rows) >= 2:
        returns = [d["pnl"] for d in daily_rows]
        n       = len(returns)
        mean_r  = sum(returns) / n
        var     = sum((r - mean_r) ** 2 for r in returns) / n
        std_r   = math.sqrt(var) if var > 0 else 0
        sharpe  = round(mean_r / std_r * math.sqrt(252), 2) if std_r > 0 else 0

    equity, peak, max_dd = starting_capital, starting_capital, 0.0
    for t in query("SELECT net_pnl FROM trades ORDER BY timestamp ASC"):
        equity += t["net_pnl"]
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak if peak > 0 else 0
        max_dd  = max(max_dd, dd)
    max_drawdown_pct = round(max_dd * 100, 2)

    # ── Open positions with estimated PnL ──
    positions = query("SELECT * FROM positions WHERE status='open'")
    now = datetime.utcnow()
    for p in positions:
        try:
            entry = datetime.fromisoformat(p["entry_time"])
            hrs   = (now - entry).total_seconds() / 3600
            notional = p["size_usd"] * 2
            p["est_pnl"]    = round(notional * p["funding_rate"] * hrs - notional * 0.001, 4)
            p["held_hrs"]   = round(hrs, 1)
            p["annual_pct"] = round(p["funding_rate"] * 24 * 365 * 100, 1)
            p["exit_threshold_pct"] = round(max(p["funding_rate"] * 0.33, 0.0003) * 100, 4)
        except Exception:
            p["est_pnl"] = p["held_hrs"] = p["annual_pct"] = p["exit_threshold_pct"] = 0

    # ── Recent trades ──
    trades = query("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20")

    # ── Daily breakdown ──
    daily_breakdown = query("""
        SELECT date(timestamp) as day,
               COUNT(*)        as trades,
               ROUND(SUM(gross_pnl),4) as gross,
               ROUND(SUM(fees),4)      as fees,
               ROUND(SUM(net_pnl),4)   as net
        FROM trades GROUP BY date(timestamp) ORDER BY day DESC LIMIT 30
    """)

    # ── Cumulative PnL chart ──
    all_trades = query("SELECT timestamp, net_pnl FROM trades ORDER BY timestamp ASC")
    cumulative, chart_labels, chart_data = 0, [], []
    for t in all_trades:
        cumulative += t["net_pnl"]
        chart_labels.append(t["timestamp"][:16].replace("T", " "))
        chart_data.append(round(cumulative, 4))

    # ── 24hr rate history per asset ──
    rate_history = query("""
        SELECT asset, timestamp, rate_pct FROM rate_snapshot
        WHERE timestamp > datetime('now', '-24 hours')
        ORDER BY timestamp ASC
    """)
    rate_series = defaultdict(lambda: {"labels": [], "data": []})
    for r in rate_history:
        ts = r["timestamp"][:16].replace("T", " ")
        rate_series[r["asset"]]["labels"].append(ts)
        rate_series[r["asset"]]["data"].append(r["rate_pct"])

    # ── Annualised return from performance module ──
    annual_return_pct = 0.0
    try:
        if "/root/GucciQuant" not in sys.path:
            sys.path.insert(0, "/root/GucciQuant")
        from utils.performance import get_metrics
        m = get_metrics(capital)
        annual_return_pct = m.get("annual_return_pct", 0.0)
    except Exception:
        pass

    # ── Scan log ──
    scans = query("SELECT * FROM scan_log ORDER BY timestamp DESC LIMIT 96")

    return jsonify({
        "capital":          capital,
        "total_pnl":        round(total_pnl, 4),
        "today_pnl":        round(today_pnl, 4),
        "total_trades":     total_trades,
        "win_rate":         win_rate,
        "best_rate_pct":    round(best_rate * 100, 4),
        "total_fees":       round(total_fees, 4),
        "total_gross":      round(total_gross, 4),
        "sharpe":           sharpe,
        "max_drawdown_pct": max_drawdown_pct,
        "positions":        positions,
        "trades":           trades,
        "daily_breakdown":  daily_breakdown,
        "scans":            scans,
        "chart_labels":     chart_labels,
        "chart_data":       chart_data,
        "rate_series":      dict(rate_series),
        "annual_return_pct": annual_return_pct,
        "mode":             os.getenv("PAPER_MODE", "true").lower(),
        "last_updated":     now.strftime("%H:%M:%S UTC"),
    })


# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GUCCI QUANT / Graphite Terminal</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #050607;
    --sidebar: #080a0c;
    --panel: #0d1012;
    --panel-strong: #101316;
    --border: rgba(255,255,255,.08);
    --border-strong: rgba(255,255,255,.12);
    --text: #f4f4f5;
    --secondary: #a1a1aa;
    --muted: #71717a;
    --quiet: #494950;
    --positive: #6d9d82;
    --positive-fill: rgba(109,157,130,.12);
    --warning: #a58c67;
    --warning-fill: rgba(165,140,103,.12);
    --negative: #a36b6b;
    --negative-fill: rgba(163,107,107,.12);
    --radius: 9px;
    --sidebar-width: 224px;
  }
  html { background: var(--bg); scroll-behavior: smooth; }
  body {
    min-width: 1100px;
    background: var(--bg);
    color: var(--text);
    font: 400 13px/1.45 "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; text-decoration: none; }
  button { font: inherit; color: inherit; }
  .terminal { min-height: 100vh; display: flex; }
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: var(--sidebar-width);
    display: flex;
    flex-direction: column;
    background: var(--sidebar);
    border-right: 1px solid var(--border);
    padding: 26px 18px 18px;
  }
  .wordmark {
    padding: 0 10px 27px;
    border-bottom: 1px solid var(--border);
    font-size: 14px;
    font-weight: 600;
    letter-spacing: .25em;
    white-space: nowrap;
  }
  .suite {
    display: block;
    margin-top: 9px;
    color: var(--muted);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: .22em;
  }
  .navigation { padding-top: 29px; }
  .nav-section {
    padding: 0 10px;
    margin-bottom: 12px;
    color: var(--quiet);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .18em;
    text-transform: uppercase;
  }
  .nav-link {
    display: flex;
    align-items: center;
    gap: 11px;
    height: 38px;
    padding: 0 11px;
    margin-bottom: 5px;
    border: 1px solid transparent;
    border-radius: 7px;
    color: var(--secondary);
    transition: border-color .18s ease, background .18s ease, color .18s ease;
  }
  .nav-link:hover, .nav-link.active {
    background: var(--panel);
    border-color: var(--border);
    color: var(--text);
  }
  .nav-mark {
    width: 13px;
    height: 13px;
    border: 1px solid currentColor;
    border-radius: 3px;
    opacity: .75;
  }
  .sidebar-state {
    margin-top: auto;
    padding: 17px 11px 7px;
    border-top: 1px solid var(--border);
  }
  .state-line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    color: var(--muted);
    font-size: 11px;
  }
  .status-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-right: 8px;
    border-radius: 50%;
    background: var(--positive);
    box-shadow: 0 0 0 4px rgba(109,157,130,.08);
  }
  .status-dot.offline { background: var(--negative); box-shadow: 0 0 0 4px var(--negative-fill); }
  .mode-pill, .small-pill {
    display: inline-flex;
    align-items: center;
    min-height: 22px;
    padding: 0 9px;
    border: 1px solid var(--border-strong);
    border-radius: 99px;
    color: var(--secondary);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .12em;
  }
  .mode-pill.live, .small-pill.good {
    border-color: rgba(109,157,130,.3);
    background: var(--positive-fill);
    color: var(--positive);
  }
  .workspace { flex: 1; min-width: 0; margin-left: var(--sidebar-width); }
  .header {
    position: sticky;
    top: 0;
    z-index: 5;
    height: 70px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 30px 0 31px;
    border-bottom: 1px solid var(--border);
    background: rgba(5,6,7,.94);
    backdrop-filter: blur(12px);
  }
  .page-title {
    font-size: 18px;
    font-weight: 500;
    letter-spacing: -.02em;
  }
  .page-subtitle {
    margin-top: 2px;
    color: var(--muted);
    font-size: 11px;
    letter-spacing: .05em;
    text-transform: uppercase;
  }
  .header-tools { display: flex; align-items: center; gap: 14px; }
  .updated { color: var(--muted); font-variant-numeric: tabular-nums; }
  .button {
    height: 33px;
    padding: 0 15px;
    background: transparent;
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    color: var(--secondary);
    cursor: pointer;
    font-size: 12px;
    transition: background .18s ease, color .18s ease;
  }
  .button:hover { background: rgba(255,255,255,.035); color: var(--text); }
  .button:focus-visible { outline: 1px solid var(--secondary); outline-offset: 2px; }
  .content { display: flex; flex-direction: column; gap: 14px; padding: 22px 28px 34px; }
  .kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(180px, 1fr));
    gap: 12px;
  }
  .panel, .kpi {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .kpi { min-height: 108px; padding: 17px 19px; }
  .label {
    color: var(--muted);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .15em;
    text-transform: uppercase;
  }
  .value {
    margin-top: 13px;
    color: var(--text);
    font-size: 27px;
    font-weight: 500;
    letter-spacing: -.04em;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .value.compact { font-size: 23px; }
  .caption { margin-top: 8px; color: var(--muted); font-size: 11px; }
  .positive { color: var(--positive) !important; }
  .negative { color: var(--negative) !important; }
  .warning { color: var(--warning) !important; }
  .grid-primary {
    display: grid;
    grid-template-columns: minmax(600px, 2.2fr) minmax(315px, 1fr);
    align-items: start;
    gap: 14px;
  }
  .column { min-width: 0; display: flex; flex-direction: column; gap: 14px; }
  .grid-two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .grid-primary > *, .grid-two > * { min-width: 0; }
  .panel { padding: 16px 18px; overflow: hidden; }
  .panel.tall { min-height: 260px; }
  .panel-header {
    min-height: 29px;
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .panel-title {
    color: var(--text);
    font-size: 13px;
    font-weight: 500;
    letter-spacing: -.01em;
  }
  .panel-meta {
    margin-top: 3px;
    color: var(--muted);
    font-size: 11px;
  }
  .panel-count {
    color: var(--muted);
    font-size: 11px;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .chart-shell { height: 198px; position: relative; }
  .chart-shell.rate { height: 226px; }
  canvas { max-width: 100%; }
  .table-wrap { overflow-x: auto; scrollbar-color: var(--border-strong) transparent; }
  .table-wrap::-webkit-scrollbar { width: 5px; height: 5px; }
  .table-wrap::-webkit-scrollbar-track { background: transparent; }
  .table-wrap::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 99px; }
  .table-scroll { max-height: 290px; overflow-y: auto; }
  .table-scroll::-webkit-scrollbar { width: 5px; height: 5px; }
  .table-scroll::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 99px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th {
    padding: 0 10px 9px 0;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .11em;
    text-align: left;
    text-transform: uppercase;
    white-space: nowrap;
  }
  td {
    padding: 10px 10px 10px 0;
    border-bottom: 1px solid rgba(255,255,255,.045);
    color: var(--secondary);
    font-size: 12px;
    white-space: nowrap;
  }
  tbody tr:last-child td { border-bottom: 0; }
  .right { text-align: right; padding-right: 0; }
  .instrument {
    display: inline-flex;
    min-width: 48px;
    padding: 3px 8px;
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    color: var(--text);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: .05em;
  }
  .empty {
    min-height: 104px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 5px;
    color: var(--secondary);
    text-align: center;
  }
  .empty::before {
    content: "";
    width: 24px;
    height: 1px;
    margin-bottom: 8px;
    background: var(--border-strong);
  }
  .empty-title { font-size: 12px; }
  .empty-note { color: var(--muted); font-size: 11px; }
  .ledger { display: flex; flex-direction: column; }
  .ledger-row {
    min-height: 38px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid rgba(255,255,255,.05);
    color: var(--secondary);
    font-size: 12px;
  }
  .ledger-row:last-child { border-bottom: 0; }
  .ledger-row strong { color: var(--text); font-weight: 500; font-variant-numeric: tabular-nums; }
  .health-row {
    min-height: 39px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid rgba(255,255,255,.05);
    color: var(--secondary);
    font-size: 12px;
  }
  .health-row:last-child { border-bottom: 0; }
  .health-value { color: var(--secondary); }
  .timeline-row {
    position: relative;
    min-height: 46px;
    padding: 5px 0 5px 22px;
    border-left: 1px solid var(--border-strong);
    margin-left: 5px;
  }
  .timeline-row::before {
    content: "";
    position: absolute;
    left: -4px;
    top: 11px;
    width: 7px;
    height: 7px;
    background: #6b6b70;
    border-radius: 50%;
  }
  .timeline-time { color: var(--muted); font-size: 10px; text-transform: uppercase; }
  .timeline-copy { margin-top: 3px; color: var(--secondary); font-size: 12px; }
  .notice {
    padding: 10px 11px;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--secondary);
    font-size: 12px;
  }
  .notice + .notice { margin-top: 8px; }
  .notice.warning { background: var(--warning-fill); border-color: rgba(165,140,103,.24); }
  .notice.error { background: var(--negative-fill); border-color: rgba(163,107,107,.25); color: var(--negative); }
  .bottom-grid { display: grid; grid-template-columns: 1.18fr .82fr; gap: 14px; }
  .footer {
    display: flex;
    justify-content: space-between;
    padding: 8px 3px 0;
    color: var(--muted);
    font-size: 11px;
  }
  .footer-links { display: flex; gap: 18px; }
  .footer-links a:hover { color: var(--secondary); }
  .mobile-nav { display: none; }
  .mobile-only { display: none; }
  @media (max-width: 1220px) {
    body { min-width: 0; }
    .sidebar { position: static; width: 194px; flex: 0 0 194px; }
    .workspace { margin-left: 0; }
    .grid-primary, .bottom-grid { grid-template-columns: 1fr; }
  }
  @media (max-width: 900px) {
    .terminal { display: block; }
    .sidebar {
      width: auto;
      flex-direction: row;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 12px 16px;
      border-right: 0;
      border-bottom: 1px solid var(--border);
    }
    .wordmark {
      padding: 0;
      border-bottom: 0;
      font-size: 12px;
      letter-spacing: .2em;
    }
    .suite { margin-top: 2px; font-size: 8px; letter-spacing: .18em; }
    .navigation { display: none; }
    .sidebar-state {
      margin-top: 0;
      padding: 0;
      border-top: 0;
      display: flex;
      align-items: center;
      gap: 9px;
    }
    .state-line { margin: 0; font-size: 10px; }
    .feed-label { display: inline-flex; align-items: center; }
    .feed-copy { display: none; }
    .status-dot { width: 6px; height: 6px; margin-right: 6px; }
    .mode-pill { min-height: 20px; padding: 0 8px; font-size: 9px; }
    .header {
      position: static;
      height: 54px;
      min-height: 54px;
      flex-direction: row;
      align-items: center;
      gap: 10px;
      padding: 0 16px;
    }
    .page-title { font-size: 16px; }
    .page-subtitle, .updated { display: none; }
    .header-tools { width: auto; margin-left: auto; }
    .button { height: 30px; padding: 0 12px; font-size: 11px; }
    .content { padding: 16px 16px 66px; }
    .kpis, .grid-two { grid-template-columns: 1fr 1fr; }
    .mobile-hide { display: none; }
    .mobile-table { overflow-x: hidden; }
    #daily, #trades, #scanlog { overflow-x: hidden; }
    .mobile-table table, table.mobile-table { table-layout: fixed; }
    .mobile-only { display: inline; }
    .mobile-table .mobile-action {
      max-width: 128px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .mobile-nav {
      position: fixed;
      right: 12px;
      bottom: 12px;
      left: 12px;
      z-index: 8;
      height: 45px;
      display: flex;
      align-items: center;
      justify-content: space-around;
      padding: 0 8px;
      border: 1px solid var(--border-strong);
      border-radius: 9px;
      background: rgba(13,16,18,.96);
      backdrop-filter: blur(12px);
      color: var(--muted);
      font-size: 10px;
      font-weight: 500;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .mobile-nav a { padding: 10px 12px; }
    .mobile-nav a:focus-visible { color: var(--text); }
  }
  @media (max-width: 360px) {
    .kpi { min-height: 96px; padding: 13px 12px; }
    .value { margin-top: 10px; font-size: 22px; }
    .value.compact { font-size: 20px; }
    .caption { margin-top: 6px; font-size: 10px; }
  }
</style>
</head>
<body>
<div class="terminal">
  <aside class="sidebar">
    <div class="wordmark">GUCCI QUANT<span class="suite">GRAPHITE TERMINAL</span></div>
    <nav class="navigation" aria-label="Dashboard sections">
      <div class="nav-section">Overview</div>
      <a class="nav-link active" href="#overview"><span class="nav-mark"></span>Dashboard</a>
      <a class="nav-link" href="#positions"><span class="nav-mark"></span>Positions</a>
      <a class="nav-link" href="#opportunities"><span class="nav-mark"></span>Opportunities</a>
      <a class="nav-link" href="#performance"><span class="nav-mark"></span>Performance</a>
      <a class="nav-link" href="#logs"><span class="nav-mark"></span>System Logs</a>
    </nav>
    <div class="sidebar-state">
      <div class="state-line"><span class="feed-label"><span class="status-dot" id="sidebar-dot"></span><span class="feed-copy">System feed</span></span><span id="feed-state">ONLINE</span></div>
      <span class="mode-pill" id="badge">PAPER</span>
    </div>
  </aside>
  <section class="workspace">
    <header class="header">
      <div>
        <h1 class="page-title">Portfolio Overview</h1>
        <div class="page-subtitle">Funding arbitrage operations / USDC settlement</div>
      </div>
      <div class="header-tools">
        <span class="updated" id="upd">Connecting</span>
        <button class="button" id="refresh" type="button">Refresh</button>
      </div>
    </header>
    <main class="content" id="overview">
      <section class="kpis" aria-label="Key performance indicators">
        <article class="kpi"><div class="label">Capital</div><div class="value" id="capital">--</div><div class="caption">USDC tracked balance</div></article>
        <article class="kpi"><div class="label">Net PnL</div><div class="value" id="allpnl">--</div><div class="caption">All-time after fees</div></article>
        <article class="kpi"><div class="label">Today</div><div class="value" id="daypnl">--</div><div class="caption" id="today-date">--</div></article>
        <article class="kpi"><div class="label">Best Funding Rate</div><div class="value compact" id="bestrate">--</div><div class="caption">Observed today / hour</div></article>
      </section>
      <div class="grid-primary">
        <div class="column">
          <section class="panel" id="positions">
            <div class="panel-header">
              <div><h2 class="panel-title">Open Positions</h2><div class="panel-meta">Active allocations and estimated net PnL</div></div>
              <span class="panel-count" id="pos-count">--</span>
            </div>
            <div id="pos"></div>
          </section>
          <section class="panel tall">
            <div class="panel-header">
              <div><h2 class="panel-title">Funding Rate History</h2><div class="panel-meta">Last 24 hours / all observed assets</div></div>
            </div>
            <div class="chart-shell rate"><canvas id="rate-chart"></canvas><div id="no-rate-chart" class="empty" style="display:none"><div class="empty-title">No funding snapshots available</div><div class="empty-note">Rate history appears after a completed scan cycle.</div></div></div>
          </section>
          <div class="grid-two">
            <section class="panel" id="performance">
              <div class="panel-header"><div><h2 class="panel-title">PnL Trend</h2><div class="panel-meta">Cumulative completed trades</div></div></div>
              <div class="chart-shell"><canvas id="pnl-chart"></canvas><div id="no-pnl-chart" class="empty" style="display:none"><div class="empty-title">No completed trades</div><div class="empty-note">Trend data begins after first close.</div></div></div>
            </section>
            <section class="panel">
              <div class="panel-header"><div><h2 class="panel-title">PnL Attribution</h2><div class="panel-meta">Gross funding less costs</div></div></div>
              <div id="attribution"></div>
            </section>
          </div>
          <section class="panel">
            <div class="panel-header">
              <div><h2 class="panel-title">Daily Breakdown</h2><div class="panel-meta">Net settlement by trading day</div></div>
            </div>
            <div class="table-wrap" id="daily"></div>
          </section>
          <section class="panel">
            <div class="panel-header">
              <div><h2 class="panel-title">Trade History</h2><div class="panel-meta">Completed position ledger</div></div>
              <span class="panel-count" id="trade-count">--</span>
            </div>
            <div class="table-wrap table-scroll" id="trades"></div>
          </section>
          <section class="panel" id="logs">
            <div class="panel-header">
              <div><h2 class="panel-title">System Logs</h2><div class="panel-meta">Most recent scan cycle events</div></div>
              <span class="panel-count">30 sec refresh</span>
            </div>
            <div class="table-wrap table-scroll" id="scanlog"></div>
          </section>
        </div>
        <aside class="column">
          <section class="panel">
            <div class="panel-header"><div><h2 class="panel-title">Risk Status</h2><div class="panel-meta">Dashboard risk indicators</div></div><span class="small-pill good" id="risk-pill">MONITORING</span></div>
            <div id="risk-status" class="ledger"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><div><h2 class="panel-title">System Health</h2><div class="panel-meta">Feed and operating mode</div></div></div>
            <div id="system-health"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><div><h2 class="panel-title">Margin Info</h2><div class="panel-meta">Capital allocated to positions</div></div></div>
            <div id="margin-info" class="ledger"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><div><h2 class="panel-title">Alerts</h2><div class="panel-meta">Dashboard notices</div></div></div>
            <div id="alerts"></div>
          </section>
          <section class="panel" id="opportunities">
            <div class="panel-header"><div><h2 class="panel-title">Live Opportunities</h2><div class="panel-meta">Latest scanner observation</div></div></div>
            <div id="opps"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><div><h2 class="panel-title">Recent Activity</h2><div class="panel-meta">Latest completed trades or scans</div></div></div>
            <div id="activity"></div>
          </section>
        </aside>
      </div>
      <footer class="footer">
        <span>GUCCI QUANT / Graphite Terminal</span>
        <span class="footer-links"><a href="#overview">Overview</a><a href="#positions">Positions</a><a href="#performance">Performance</a><a href="#logs">Logs</a></span>
      </footer>
    </main>
  </section>
</div>
<nav class="mobile-nav" aria-label="Mobile sections">
  <a href="#overview">Overview</a>
  <a href="#positions">Positions</a>
  <a href="#performance">PnL</a>
  <a href="#logs">Logs</a>
</nav>
<script>
let pnlChart = null;
let rateChart = null;
const $ = id => document.getElementById(id);
const safe = value => String(value ?? "--").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const n = value => Number(value) || 0;
const sign = (value, decimals=4) => `${n(value) >= 0 ? "+" : "-"}${Math.abs(n(value)).toFixed(decimals)}`;
const money = value => `$${n(value).toFixed(2)}`;
const tone = value => n(value) > 0 ? "positive" : n(value) < 0 ? "negative" : "";
const empty = (title, note="") => `<div class="empty"><div class="empty-title">${safe(title)}</div>${note ? `<div class="empty-note">${safe(note)}</div>` : ""}</div>`;

const grid = {
  color: "rgba(255,255,255,.045)",
  ticks: "#71717a",
  line: "#d4d4d8",
  lines: ["#f4f4f5", "#b8b8bc", "#85858c", "#606067", "#444449", "#98989f"]
};
const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { intersect: false, mode: "index" },
  plugins: { legend: { display: false } },
  scales: {
    x: { border: { display: false }, grid: { display: false }, ticks: { color: grid.ticks, font: { size: 10 }, maxTicksLimit: 6 } },
    y: { border: { display: false }, grid: { color: grid.color }, ticks: { color: grid.ticks, font: { size: 10 } } }
  }
};

function renderPositions(d) {
  const positions = d.positions || [];
  $("pos-count").textContent = `${positions.length} OPEN`;
  if (!positions.length) {
    $("pos").innerHTML = empty("No open positions", "Positions appear when the execution feed reports active exposure.");
    return;
  }
  let rows = "";
  for (const p of positions) {
    rows += `<tr><td><span class="instrument">${safe(p.asset)}</span></td><td>${safe(p.annual_pct)}%</td><td class="mobile-hide">${safe(p.held_hrs)} h</td><td class="mobile-hide">${safe(p.exit_threshold_pct)}%/hr</td><td class="right ${tone(p.est_pnl)}">${sign(p.est_pnl)}</td></tr>`;
  }
  $("pos").innerHTML = `<div class="table-wrap mobile-table"><table><thead><tr><th>Asset</th><th><span class="mobile-hide">Annualized</span><span class="mobile-only">Rate</span></th><th class="mobile-hide">Held</th><th class="mobile-hide">Exit Rate</th><th class="right"><span class="mobile-hide">Est. PnL</span><span class="mobile-only">PnL</span></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderOperationalPanels(d) {
  const positions = d.positions || [];
  const allocation = positions.reduce((total, p) => total + n(p.size_usd), 0);
  const available = Math.max(n(d.capital) - allocation, 0);
  $("risk-status").innerHTML = `
    <div class="ledger-row"><span>Open positions</span><strong>${positions.length}</strong></div>
    <div class="ledger-row"><span>Max drawdown</span><strong>${n(d.max_drawdown_pct).toFixed(2)}%</strong></div>
    <div class="ledger-row"><span>Sharpe ratio</span><strong>${n(d.sharpe).toFixed(2)}</strong></div>
    <div class="ledger-row"><span>Win rate</span><strong>${n(d.win_rate).toFixed(1)}%</strong></div>`;
  $("system-health").innerHTML = `
    <div class="health-row"><span>Statistics feed</span><span class="small-pill good">ONLINE</span></div>
    <div class="health-row"><span>Execution mode</span><span class="health-value">${d.mode === "false" ? "LIVE" : "PAPER"}</span></div>
    <div class="health-row"><span>Refresh interval</span><span class="health-value">30 seconds</span></div>
    <div class="health-row"><span>Last update</span><span class="health-value">${safe(d.last_updated)}</span></div>`;
  $("margin-info").innerHTML = `
    <div class="ledger-row"><span>Tracked capital</span><strong>${money(d.capital)}</strong></div>
    <div class="ledger-row"><span>Position allocation</span><strong>${money(allocation)}</strong></div>
    <div class="ledger-row"><span>Unallocated balance</span><strong>${money(available)}</strong></div>`;
  $("alerts").innerHTML = `<div class="notice">No active dashboard notices.</div>`;
}

function renderOpportunities(d) {
  const scan = (d.scans || [])[0];
  if (!scan) {
    $("opps").innerHTML = empty("No scanner observations", "This panel updates after the next recorded scan.");
    return;
  }
  $("opps").innerHTML = `
    <div class="ledger-row"><span>Top asset</span><strong>${scan.top_asset ? safe(scan.top_asset) : "--"}</strong></div>
    <div class="ledger-row"><span>Observed rate</span><strong>${scan.top_rate_pct ? n(scan.top_rate_pct).toFixed(4) + "%/hr" : "--"}</strong></div>
    <div class="ledger-row"><span>Opportunities</span><strong>${n(scan.opportunities).toFixed(0)}</strong></div>
    <div class="ledger-row"><span>Scanner action</span><strong>${safe(scan.action || "--")}</strong></div>`;
}

function renderActivity(d) {
  const trades = (d.trades || []).slice(0, 3);
  const scans = (d.scans || []).slice(0, 3);
  const items = trades.length ? trades.map(t => ({
    time: (t.timestamp || "").slice(5, 16).replace("T", " "),
    copy: `${t.asset || "--"} closed / ${sign(t.net_pnl)} USDC`
  })) : scans.map(s => ({
    time: (s.timestamp || "").slice(5, 16).replace("T", " "),
    copy: s.action || "Scan cycle recorded"
  }));
  if (!items.length) {
    $("activity").innerHTML = empty("No recent activity", "Activity appears once scans or trades are recorded.");
    return;
  }
  $("activity").innerHTML = items.map(item => `<div class="timeline-row"><div class="timeline-time">${safe(item.time)} UTC</div><div class="timeline-copy">${safe(item.copy)}</div></div>`).join("");
}

function renderTradeHistory(d) {
  const trades = d.trades || [];
  $("trade-count").textContent = `${n(d.total_trades).toFixed(0)} TOTAL`;
  if (!trades.length) {
    $("trades").innerHTML = empty("No completed trades", "Closed trades will be listed here.");
    return;
  }
  const rows = trades.map(t => `<tr><td>${safe((t.timestamp || "").slice(5, 16).replace("T", " "))}</td><td><span class="instrument">${safe(t.asset)}</span></td><td class="mobile-hide">${t.duration_hrs ? n(t.duration_hrs).toFixed(1) + " h" : "--"}</td><td class="mobile-hide">${money(t.size_usd)}</td><td class="positive mobile-hide">${sign(t.gross_pnl)}</td><td class="mobile-hide">${sign(-n(t.fees))}</td><td class="right ${tone(t.net_pnl)}">${sign(t.net_pnl)}</td></tr>`).join("");
  $("trades").innerHTML = `<table class="mobile-table"><thead><tr><th>Time UTC</th><th>Asset</th><th class="mobile-hide">Duration</th><th class="mobile-hide">Size</th><th class="mobile-hide">Funding</th><th class="mobile-hide">Fees</th><th class="right">Net PnL</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderDailyBreakdown(d) {
  const rows = d.daily_breakdown || [];
  if (!rows.length) {
    $("daily").innerHTML = empty("No daily settlements", "Settled trading days will be summarized here.");
    return;
  }
  $("daily").innerHTML = `<table class="mobile-table"><thead><tr><th>Date</th><th class="mobile-hide">Trades</th><th class="mobile-hide">Gross</th><th class="mobile-hide">Fees</th><th class="right">Net</th></tr></thead><tbody>${rows.map(row => `<tr><td>${safe(row.day)}</td><td class="mobile-hide">${safe(row.trades)}</td><td class="positive mobile-hide">${sign(row.gross)}</td><td class="mobile-hide">${sign(-n(row.fees))}</td><td class="right ${tone(row.net)}">${sign(row.net)}</td></tr>`).join("")}</tbody></table>`;
}

function renderLogs(d) {
  const scans = d.scans || [];
  if (!scans.length) {
    $("scanlog").innerHTML = empty("No system log entries", "Recorded scans will appear here.");
    return;
  }
  const rows = scans.map(s => `<tr><td>${safe((s.timestamp || "").slice(5, 16).replace("T", " "))}</td><td class="mobile-hide">${n(s.efficiency).toFixed(0)}%</td><td class="mobile-hide">${s.mins_to_fund != null ? safe(s.mins_to_fund) + " min" : "--"}</td><td>${s.top_asset ? safe(s.top_asset) : "--"}</td><td class="mobile-hide">${s.top_rate_pct ? n(s.top_rate_pct).toFixed(4) + "%" : "--"}</td><td class="right mobile-action">${safe(s.action || "--")}</td></tr>`).join("");
  $("scanlog").innerHTML = `<table class="mobile-table"><thead><tr><th>Time UTC</th><th class="mobile-hide">Efficiency</th><th class="mobile-hide">Funding In</th><th>Asset</th><th class="mobile-hide">Top Rate</th><th class="right">Action</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderAttribution(d) {
  const gross = n(d.total_gross);
  const fees = n(d.total_fees);
  const net = n(d.total_pnl);
  const feeDrag = gross > 0 ? `${(fees / gross * 100).toFixed(1)}%` : "--";
  $("attribution").innerHTML = `<div class="ledger">
    <div class="ledger-row"><span>Funding earned</span><strong class="positive">${sign(gross)} USDC</strong></div>
    <div class="ledger-row"><span>Fees paid</span><strong>${sign(-fees)} USDC</strong></div>
    <div class="ledger-row"><span>Fee drag</span><strong>${feeDrag}</strong></div>
    <div class="ledger-row"><span>Net PnL</span><strong class="${tone(net)}">${sign(net)} USDC</strong></div>
    <div class="ledger-row"><span>Annual return</span><strong>${n(d.annual_return_pct).toFixed(1)}%</strong></div>
  </div>`;
}

function renderCharts(d) {
  const pnlData = d.chart_data || [];
  if (!pnlData.length || typeof Chart === "undefined") {
    $("pnl-chart").style.display = "none";
    $("no-pnl-chart").style.display = "flex";
  } else {
    $("pnl-chart").style.display = "block";
    $("no-pnl-chart").style.display = "none";
    const dataset = { data: pnlData, borderColor: grid.line, backgroundColor: "rgba(244,244,245,.045)", fill: true, borderWidth: 1.5, tension: .32, pointRadius: 0 };
    if (pnlChart) {
      pnlChart.data.labels = d.chart_labels;
      pnlChart.data.datasets = [dataset];
      pnlChart.update("none");
    } else {
      pnlChart = new Chart($("pnl-chart"), { type: "line", data: { labels: d.chart_labels, datasets: [dataset] }, options: { ...chartOptions, scales: { ...chartOptions.scales, y: { ...chartOptions.scales.y, ticks: { ...chartOptions.scales.y.ticks, callback: value => sign(value, 2) } } } } });
    }
  }
  const series = d.rate_series || {};
  const assets = Object.keys(series);
  if (!assets.length || typeof Chart === "undefined") {
    $("rate-chart").style.display = "none";
    $("no-rate-chart").style.display = "flex";
    return;
  }
  $("rate-chart").style.display = "block";
  $("no-rate-chart").style.display = "none";
  const labels = [...new Set(assets.flatMap(asset => series[asset].labels))].sort();
  const datasets = assets.map((asset, index) => ({
    label: asset,
    data: labels.map(label => { const i = series[asset].labels.indexOf(label); return i >= 0 ? series[asset].data[i] : null; }),
    borderColor: grid.lines[index % grid.lines.length],
    backgroundColor: "transparent",
    borderWidth: 1.25,
    tension: .25,
    pointRadius: 0,
    spanGaps: true
  }));
  const options = { ...chartOptions, plugins: { legend: { display: true, align: "end", labels: { color: grid.ticks, boxWidth: 14, boxHeight: 1, padding: 14, font: { size: 10 } } } }, scales: { ...chartOptions.scales, y: { ...chartOptions.scales.y, ticks: { ...chartOptions.scales.y.ticks, callback: value => `${n(value).toFixed(4)}%` } } } };
  if (rateChart) {
    rateChart.data.labels = labels;
    rateChart.data.datasets = datasets;
    rateChart.update("none");
  } else {
    rateChart = new Chart($("rate-chart"), { type: "line", data: { labels, datasets }, options });
  }
}

async function load() {
  try {
    const response = await fetch("/api/stats");
    if (!response.ok) throw new Error("stats unavailable");
    const d = await response.json();
    $("upd").textContent = `UPDATED ${safe(d.last_updated)}`;
    $("feed-state").textContent = "ONLINE";
    $("sidebar-dot").classList.remove("offline");
    $("capital").textContent = money(d.capital);
    $("allpnl").textContent = `${sign(d.total_pnl)} USDC`;
    $("allpnl").className = `value ${tone(d.total_pnl)}`;
    $("daypnl").textContent = `${sign(d.today_pnl)} USDC`;
    $("daypnl").className = `value ${tone(d.today_pnl)}`;
    $("today-date").textContent = new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
    $("bestrate").textContent = `${n(d.best_rate_pct).toFixed(4)}%`;
    const badge = $("badge");
    badge.textContent = d.mode === "false" ? "LIVE" : "PAPER";
    badge.classList.toggle("live", d.mode === "false");
    renderPositions(d);
    renderOperationalPanels(d);
    renderOpportunities(d);
    renderActivity(d);
    renderAttribution(d);
    renderDailyBreakdown(d);
    renderTradeHistory(d);
    renderLogs(d);
    renderCharts(d);
  } catch (error) {
    $("upd").textContent = "CONNECTION ERROR";
    $("feed-state").textContent = "OFFLINE";
    $("sidebar-dot").classList.add("offline");
    $("system-health").innerHTML = `<div class="notice error">Unable to reach dashboard statistics feed. Retrying automatically.</div>`;
    $("alerts").innerHTML = `<div class="notice error">Dashboard statistics feed unavailable.</div>`;
  }
}

$("refresh").addEventListener("click", load);
load();
setInterval(load, 30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    print(f"\n{'─'*48}")
    print(f"  📊  GUCCI QUANT DASHBOARD v2")
    print(f"  Open in browser: http://0.0.0.0:{port}")
    print(f"  From your Mac:   http://187.124.41.102:{port}")
    print(f"{'─'*48}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
