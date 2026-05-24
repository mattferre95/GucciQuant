"""
GUCCI QUANT — Visual Dashboard v2
Runs alongside the bot in a separate screen session.
Access: http://YOUR_VPS_IP:8080
"""
from flask import Flask, jsonify, render_template_string
import sqlite3, os, math
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
        "mode":             os.getenv("PAPER_MODE", "true").lower(),
        "last_updated":     now.strftime("%H:%M:%S UTC"),
    })


# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GUCCI QUANT</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #080810; --card: #0e0e18; --border: #1a1a2e;
    --green: #00e676; --red: #ff1744; --text: #dde1f0;
    --muted: #4a4a6a; --accent: #7c6fff; --yellow: #ffd740;
  }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono','Fira Code','Courier New',monospace; font-size: 13px; }

  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 28px; background: var(--card); border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 10;
  }
  .logo { font-size: 17px; font-weight: 700; color: var(--green); letter-spacing: 3px; }
  .logo span { color: var(--accent); }
  .badge { margin-left: 12px; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700;
    letter-spacing: 1px; background: #0a2a18; color: var(--green); border: 1px solid var(--green); }
  .badge.live { background: #2a0a10; color: var(--red); border-color: var(--red); }
  .header-right { color: var(--muted); font-size: 11px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green);
    display: inline-block; margin-right: 6px; animation: blink 2s ease-in-out infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }

  .page { padding: 20px 28px; max-width: 1440px; margin: 0 auto; }
  .gap { margin-bottom: 14px; }

  /* Cards */
  .metrics { display: grid; grid-template-columns: repeat(7,1fr); gap: 12px; margin-bottom: 14px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .card-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px; }
  .card-val { font-size: 22px; font-weight: 700; color: var(--text); line-height: 1; }
  .card-val.green { color: var(--green); } .card-val.red { color: var(--red); }
  .card-val.yellow { color: var(--yellow); }
  .card-sub { font-size: 11px; color: var(--muted); margin-top: 5px; }

  /* Panels */
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }
  .panel-title { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .panel-title::after { content:''; flex:1; height:1px; background:var(--border); }
  .panel-sub { font-size: 10px; color: var(--muted); margin-left: 6px; }

  /* Grid layouts */
  .two-col { display: grid; grid-template-columns: 3fr 2fr; gap: 14px; }
  .two-col-eq { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; }
  th { font-size: 10px; color: var(--muted); font-weight: 500; text-transform: uppercase;
    letter-spacing: 1px; padding: 0 0 8px 0; text-align: left; border-bottom: 1px solid var(--border); }
  td { padding: 9px 0; border-bottom: 1px solid #0e0e18; color: #b0b8d0; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  .tag { background: #141424; color: var(--accent); padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 1px; border: 1px solid #2a2a4a; }
  .tag.rising  { background: #0a2010; color: var(--green); border-color: #1a4a2a; }
  .tag.falling { background: #200a10; color: var(--red);   border-color: #4a1a2a; }
  .pos { color: var(--green) !important; } .neg { color: var(--red) !important; }
  .dim { color: var(--muted); }
  .empty { text-align: center; padding: 24px 0; color: var(--muted); font-size: 12px; line-height: 2; }

  .scroll-table { max-height: 300px; overflow-y: auto; }

  @media (max-width:1100px) { .metrics{grid-template-columns:repeat(4,1fr)} }
  @media (max-width:768px)  { .metrics{grid-template-columns:repeat(2,1fr)} .two-col,.two-col-eq{grid-template-columns:1fr} }
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center">
    <span class="logo">⚡ GUCCI <span>QUANT</span></span>
    <span class="badge" id="badge">PAPER</span>
  </div>
  <div class="header-right"><span class="dot"></span><span id="upd">Loading…</span></div>
</header>

<div class="page">

  <!-- ── Metrics ── -->
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
      <div class="card-label">Today's PnL</div>
      <div class="card-val" id="daypnl">—</div>
      <div class="card-sub" id="today-date">—</div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-val" id="winrate">—</div>
      <div class="card-sub" id="tradect">—</div>
    </div>
    <div class="card">
      <div class="card-label">Sharpe Ratio</div>
      <div class="card-val" id="sharpe">—</div>
      <div class="card-sub">annualised</div>
    </div>
    <div class="card">
      <div class="card-label">Max Drawdown</div>
      <div class="card-val" id="maxdd">—</div>
      <div class="card-sub">peak-to-trough</div>
    </div>
    <div class="card">
      <div class="card-label">Best Rate Today</div>
      <div class="card-val green" id="bestrate">—</div>
      <div class="card-sub">%/hr funding</div>
    </div>
  </div>

  <!-- ── PnL Chart + Open Positions ── -->
  <div class="two-col gap">
    <div class="panel">
      <div class="panel-title">Cumulative PnL</div>
      <canvas id="pnl-chart" height="110"></canvas>
      <div id="no-pnl-chart" class="empty" style="display:none">
        📊 Chart appears after first completed trade
      </div>
    </div>
    <div class="panel">
      <div class="panel-title">Open Positions</div>
      <div id="pos"></div>
    </div>
  </div>

  <!-- ── Rate History Chart ── -->
  <div class="panel gap">
    <div class="panel-title">24hr Rate History <span class="panel-sub">— all tradeable assets</span></div>
    <canvas id="rate-chart" height="70"></canvas>
    <div id="no-rate-chart" class="empty" style="display:none">
      📈 Rate history populates after the first scan cycle
    </div>
  </div>

  <!-- ── Daily Breakdown + PnL Attribution ── -->
  <div class="two-col-eq gap">
    <div class="panel">
      <div class="panel-title">Daily Breakdown</div>
      <div id="daily"></div>
    </div>
    <div class="panel">
      <div class="panel-title">PnL Attribution</div>
      <div id="attribution"></div>
    </div>
  </div>

  <!-- ── Trade History ── -->
  <div class="panel gap">
    <div class="panel-title">Trade History</div>
    <div id="trades"></div>
  </div>

  <!-- ── Scan Activity Log ── -->
  <div class="panel">
    <div class="panel-title">Scan Activity Log <span class="panel-sub">— every 15 min</span></div>
    <div id="scanlog" class="scroll-table"></div>
  </div>

</div>

<script>
let pnlChart = null, rateChart = null;

const fmt  = (v,d=4) => { const n=+v; return (n>=0?'+':'')+n.toFixed(d); };
const cls  = v => +v >= 0 ? 'pos' : 'neg';
const $    = id => document.getElementById(id);

const ASSET_COLORS = {
  'HYPE':  '#7c6fff', 'MON':   '#00e676', 'BERA':  '#ffd740',
  'ANIME': '#ff6d6d', 'AZTEC': '#40c4ff', 'PURR':  '#ea80fc',
};
const assetColor = a => ASSET_COLORS[a] || '#888';

async function load() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());

    // Header
    $('upd').textContent = 'Updated ' + d.last_updated;
    const badge = $('badge');
    if (d.mode==='false') { badge.textContent='LIVE'; badge.classList.add('live'); }
    else { badge.textContent='PAPER'; badge.classList.remove('live'); }

    // Metrics
    $('capital').textContent = '$' + d.capital.toFixed(2);

    const ap = $('allpnl');
    ap.textContent = fmt(d.total_pnl) + ' USDC';
    ap.className = 'card-val ' + cls(d.total_pnl);

    const dp = $('daypnl');
    dp.textContent = fmt(d.today_pnl) + ' USDC';
    dp.className = 'card-val ' + cls(d.today_pnl);
    $('today-date').textContent = new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});

    const wr = $('winrate');
    wr.textContent = d.win_rate + '%';
    wr.className = 'card-val ' + (d.win_rate >= 50 ? 'green' : 'red');
    $('tradect').textContent = d.total_trades + ' completed';

    const sh = $('sharpe');
    sh.textContent = d.sharpe || '—';
    sh.className = 'card-val ' + (d.sharpe > 1 ? 'green' : d.sharpe < 0 ? 'red' : 'yellow');

    const md = $('maxdd');
    md.textContent = d.max_drawdown_pct + '%';
    md.className = 'card-val ' + (d.max_drawdown_pct < 5 ? 'green' : d.max_drawdown_pct < 15 ? 'yellow' : 'red');

    $('bestrate').textContent = d.best_rate_pct.toFixed(4) + '%';

    // ── Cumulative PnL chart ──
    if (d.chart_data.length > 0) {
      $('no-pnl-chart').style.display = 'none';
      $('pnl-chart').style.display = 'block';
      const last = d.chart_data[d.chart_data.length-1];
      const col = last >= 0 ? '#00e676' : '#ff1744';
      if (pnlChart) {
        pnlChart.data.labels = d.chart_labels;
        pnlChart.data.datasets[0].data = d.chart_data;
        pnlChart.data.datasets[0].borderColor = col;
        pnlChart.data.datasets[0].backgroundColor = col+'18';
        pnlChart.update('none');
      } else {
        pnlChart = new Chart($('pnl-chart').getContext('2d'), {
          type:'line',
          data:{ labels:d.chart_labels, datasets:[{
            data:d.chart_data, borderColor:col, backgroundColor:col+'18',
            borderWidth:2, pointRadius:d.chart_data.length<30?3:0, tension:0.35, fill:true
          }]},
          options:{ responsive:true, animation:false, plugins:{legend:{display:false},
            tooltip:{callbacks:{label:c=>(c.parsed.y>=0?'+':'')+c.parsed.y.toFixed(4)+' USDC'}}},
            scales:{
              x:{ticks:{color:'#4a4a6a',maxTicksLimit:8,font:{size:10}},grid:{color:'#12121e'}},
              y:{ticks:{color:'#4a4a6a',font:{size:10},callback:v=>(v>=0?'+':'')+v.toFixed(3)},grid:{color:'#12121e'}}
            }}
        });
      }
    } else {
      $('pnl-chart').style.display = 'none';
      $('no-pnl-chart').style.display = 'block';
    }

    // ── 24hr Rate History chart ──
    const rs = d.rate_series || {};
    const assets = Object.keys(rs);
    if (assets.length > 0) {
      $('no-rate-chart').style.display = 'none';
      $('rate-chart').style.display = 'block';
      const allLabels = [...new Set(assets.flatMap(a => rs[a].labels))].sort();
      const datasets = assets.map(a => ({
        label: a,
        data: allLabels.map(lbl => {
          const idx = rs[a].labels.indexOf(lbl);
          return idx >= 0 ? rs[a].data[idx] : null;
        }),
        borderColor: assetColor(a),
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        spanGaps: true,
      }));
      if (rateChart) {
        rateChart.data.labels = allLabels;
        rateChart.data.datasets = datasets;
        rateChart.update('none');
      } else {
        rateChart = new Chart($('rate-chart').getContext('2d'), {
          type:'line',
          data:{ labels:allLabels, datasets },
          options:{ responsive:true, animation:false,
            plugins:{ legend:{ position:'top', labels:{ color:'#888', font:{size:11}, boxWidth:12, padding:16 }}},
            scales:{
              x:{ticks:{color:'#4a4a6a',maxTicksLimit:10,font:{size:10}},grid:{color:'#12121e'}},
              y:{ticks:{color:'#4a4a6a',font:{size:10},callback:v=>v.toFixed(4)+'%'},grid:{color:'#12121e'},
                title:{display:true,text:'%/hr',color:'#4a4a6a',font:{size:10}}}
            }}
        });
      }
    } else {
      $('rate-chart').style.display = 'none';
      $('no-rate-chart').style.display = 'block';
    }

    // ── Open positions ──
    const posEl = $('pos');
    if (!d.positions.length) {
      posEl.innerHTML = '<div class="empty">No open positions<br><span style="font-size:11px">Scanning every 15 min for rates > 0.15%/hr</span></div>';
    } else {
      let h = '<table><thead><tr><th>Asset</th><th>Rate/yr</th><th>Held</th><th>Exit@</th><th>Est. PnL</th></tr></thead><tbody>';
      for (const p of d.positions)
        h += `<tr><td><span class="tag">${p.asset}</span></td><td>${p.annual_pct}%</td>
          <td>${p.held_hrs}hr</td>
          <td class="dim">${p.exit_threshold_pct}%/hr</td>
          <td class="${cls(p.est_pnl)}">${fmt(p.est_pnl)}</td></tr>`;
      posEl.innerHTML = h + '</tbody></table>';
    }

    // ── Daily breakdown ──
    const dayEl = $('daily');
    if (!d.daily_breakdown || !d.daily_breakdown.length) {
      dayEl.innerHTML = '<div class="empty">No trade days yet</div>';
    } else {
      let h = '<table><thead><tr><th>Date</th><th>Trades</th><th>Gross</th><th>Fees</th><th>Net</th></tr></thead><tbody>';
      for (const r of d.daily_breakdown)
        h += `<tr><td class="dim">${r.day}</td><td class="dim">${r.trades}</td>
          <td class="pos">+${(+r.gross||0).toFixed(4)}</td>
          <td class="dim">-${(+r.fees||0).toFixed(4)}</td>
          <td class="${cls(r.net)}">${fmt(r.net)}</td></tr>`;
      dayEl.innerHTML = h + '</tbody></table>';
    }

    // ── PnL Attribution ──
    const attrEl = $('attribution');
    const gross = +d.total_gross || 0;
    const fees  = +d.total_fees  || 0;
    const net   = +d.total_pnl   || 0;
    const feeRatio = gross > 0 ? (fees / gross * 100).toFixed(1) : '—';
    attrEl.innerHTML = `
      <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
        <span class="dim">Funding earned</span><span class="pos">+${gross.toFixed(4)} USDC</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
        <span class="dim">Fees paid</span><span class="neg">-${fees.toFixed(4)} USDC</span>
      </div>
      <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
        <span class="dim">Fee drag</span><span class="dim">${feeRatio}% of gross</span>
      </div>
      <div style="padding:10px 0;display:flex;justify-content:space-between;font-weight:700">
        <span>Net PnL</span><span class="${cls(net)}">${fmt(net)} USDC</span>
      </div>
      <div style="margin-top:16px;padding:12px;background:#0a0a18;border-radius:6px;font-size:11px;color:var(--muted);line-height:1.8">
        Sharpe &nbsp;<span style="color:var(--text)">${d.sharpe || '—'}</span>&nbsp;&nbsp;
        Max DD &nbsp;<span style="color:var(--text)">${d.max_drawdown_pct}%</span>&nbsp;&nbsp;
        Win Rate &nbsp;<span style="color:var(--text)">${d.win_rate}%</span>
      </div>`;

    // ── Trade history ──
    const tEl = $('trades');
    if (!d.trades.length) {
      tEl.innerHTML = '<div class="empty">No completed trades yet</div>';
    } else {
      let h = `<table><thead><tr><th>Time (UTC)</th><th>Asset</th>
        <th>Duration</th><th>Size</th><th>Funding earned</th><th>Fees</th><th>Net PnL</th><th></th>
        </tr></thead><tbody>`;
      for (const t of d.trades) {
        const ts = (t.timestamp||'').substring(5,16).replace('T',' ');
        h += `<tr>
          <td class="dim">${ts}</td><td><span class="tag">${t.asset}</span></td>
          <td>${t.duration_hrs?(+t.duration_hrs).toFixed(1)+'hr':'—'}</td>
          <td>$${(+t.size_usd||0).toFixed(0)}</td>
          <td class="pos">+${(+t.gross_pnl||0).toFixed(4)}</td>
          <td class="dim">-${(+t.fees||0).toFixed(4)}</td>
          <td class="${cls(t.net_pnl)}">${fmt(t.net_pnl)}</td>
          <td class="dim">${t.paper?'📄':'💰'}</td></tr>`;
      }
      tEl.innerHTML = h + '</tbody></table>';
    }

    // ── Scan log ──
    const slEl = $('scanlog');
    if (!d.scans || !d.scans.length) {
      slEl.innerHTML = '<div class="empty">No scan data yet</div>';
    } else {
      let h = `<table><thead><tr>
        <th>Time (UTC)</th><th>Efficiency</th><th>Funding in</th>
        <th>Top Asset</th><th>Top Rate</th><th>Opps</th><th>Action</th>
        </tr></thead><tbody>`;
      for (const s of d.scans) {
        const ts  = (s.timestamp||'').substring(5,16).replace('T',' ');
        const eff = s.efficiency || 0;
        const ec  = eff>=80?'pos':eff>=50?'':'neg';
        const ac  = s.action?.startsWith('HOLDING')?'pos':s.action?.includes('ENTERED')?'green':'dim';
        h += `<tr>
          <td class="dim">${ts}</td>
          <td class="${ec}">${eff}%</td>
          <td class="dim">${s.mins_to_fund!=null?s.mins_to_fund+' min':'—'}</td>
          <td>${s.top_asset?'<span class="tag">'+s.top_asset+'</span>':'<span class="dim">—</span>'}</td>
          <td>${s.top_rate_pct?(+s.top_rate_pct).toFixed(4)+'%/hr':'—'}</td>
          <td class="dim" style="text-align:center">${s.opportunities||0}</td>
          <td class="${ac}" style="font-size:11px">${s.action||'—'}</td></tr>`;
      }
      slEl.innerHTML = h + '</tbody></table>';
    }

  } catch(e) {
    $('upd').textContent = '⚠ Connection error — retrying…';
  }
}

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
