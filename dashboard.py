"""
GUCCI QUANT — Visual Dashboard
Runs alongside the bot in a separate screen session.
Access: http://YOUR_VPS_IP:8080
"""
from flask import Flask, jsonify, render_template_string
import sqlite3, os
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "data/gucci_quant.db")


# ── DB helpers ──────────────────────────────────────────────────────────────

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


# ── API endpoint ─────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    starting_capital = float(os.getenv("STARTING_CAPITAL", "67"))
    today = date.today().isoformat()

    total_pnl   = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades")
    today_pnl   = scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE date(timestamp)=?", (today,))
    total_trades= scalar("SELECT COUNT(*) FROM trades", default=0)
    wins        = scalar("SELECT COUNT(*) FROM trades WHERE net_pnl > 0", default=0)
    win_rate    = round(wins / total_trades * 100, 1) if total_trades else 0
    capital     = round(starting_capital + total_pnl, 2)
    best_rate   = scalar("SELECT COALESCE(MAX(funding_rate),0) FROM trades WHERE date(timestamp)=?", (today,))

    # Open positions with estimated PnL
    positions = query("SELECT * FROM positions WHERE status='open'")
    now = datetime.utcnow()
    for p in positions:
        try:
            entry = datetime.fromisoformat(p["entry_time"])
            hrs = (now - entry).total_seconds() / 3600
            notional = p["size_usd"] * 2
            gross    = notional * p["funding_rate"] * hrs
            fees     = notional * 0.0010
            p["est_pnl"]  = round(gross - fees, 4)
            p["held_hrs"] = round(hrs, 1)
            p["annual_pct"] = round(p["funding_rate"] * 24 * 365 * 100, 1)
        except Exception:
            p["est_pnl"]  = 0
            p["held_hrs"] = 0
            p["annual_pct"] = 0

    # Recent trades
    trades = query("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20")

    # Cumulative PnL chart
    all_trades = query("SELECT timestamp, net_pnl FROM trades ORDER BY timestamp ASC")
    cumulative, chart_labels, chart_data = 0, [], []
    for t in all_trades:
        cumulative += t["net_pnl"]
        chart_labels.append(t["timestamp"][:16].replace("T", " "))
        chart_data.append(round(cumulative, 4))

    # Scan activity log — last 96 entries (24hrs at 15min intervals)
    scans = query(
        "SELECT * FROM scan_log ORDER BY timestamp DESC LIMIT 96"
    )

    return jsonify({
        "capital":       capital,
        "total_pnl":     round(total_pnl, 4),
        "today_pnl":     round(today_pnl, 4),
        "total_trades":  total_trades,
        "win_rate":      win_rate,
        "best_rate_pct": round(best_rate * 100, 4),
        "positions":     positions,
        "trades":        trades,
        "scans":         scans,
        "chart_labels":  chart_labels,
        "chart_data":    chart_data,
        "mode":          os.getenv("PAPER_MODE", "true").lower(),
        "last_updated":  now.strftime("%H:%M:%S UTC"),
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
    --bg:     #080810;
    --card:   #0e0e18;
    --border: #1a1a2e;
    --green:  #00e676;
    --red:    #ff1744;
    --text:   #dde1f0;
    --muted:  #4a4a6a;
    --accent: #7c6fff;
  }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace; font-size: 13px; }

  /* ── Header ── */
  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 28px; background: var(--card);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 10;
  }
  .logo { font-size: 17px; font-weight: 700; color: var(--green); letter-spacing: 3px; }
  .logo span { color: var(--accent); }
  .badge {
    margin-left: 12px; padding: 3px 10px; border-radius: 4px; font-size: 11px;
    font-weight: 700; letter-spacing: 1px;
    background: #0a2a18; color: var(--green); border: 1px solid var(--green);
  }
  .badge.live { background: #2a0a10; color: var(--red); border-color: var(--red); }
  .header-right { display: flex; align-items: center; gap: 16px; color: var(--muted); font-size: 11px; }
  .dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--green);
    display: inline-block; margin-right: 6px; animation: blink 2s ease-in-out infinite;
  }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }

  /* ── Layout ── */
  .page { padding: 24px 28px; max-width: 1440px; margin: 0 auto; }

  /* ── Metric Cards ── */
  .metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 20px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 18px 20px;
  }
  .card-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 10px; }
  .card-val { font-size: 26px; font-weight: 700; color: var(--text); line-height: 1; }
  .card-val.green { color: var(--green); }
  .card-val.red   { color: var(--red); }
  .card-sub { font-size: 11px; color: var(--muted); margin-top: 6px; }

  /* ── Middle row ── */
  .mid { display: grid; grid-template-columns: 3fr 2fr; gap: 14px; margin-bottom: 20px; }

  /* ── Panels ── */
  .panel {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px;
  }
  .panel-title {
    font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 1.5px; margin-bottom: 16px;
    display: flex; align-items: center; gap: 8px;
  }
  .panel-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }

  /* ── Table ── */
  table { width: 100%; border-collapse: collapse; }
  th {
    font-size: 10px; color: var(--muted); font-weight: 500;
    text-transform: uppercase; letter-spacing: 1px;
    padding: 0 0 10px 0; text-align: left; border-bottom: 1px solid var(--border);
  }
  td { padding: 11px 0; border-bottom: 1px solid #0e0e18; color: #b0b8d0; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  .tag {
    background: #141424; color: var(--accent); padding: 2px 8px;
    border-radius: 4px; font-size: 11px; font-weight: 700; letter-spacing: 1px;
    border: 1px solid #2a2a4a;
  }
  .pos { color: var(--green) !important; }
  .neg { color: var(--red) !important; }
  .dim { color: var(--muted); }

  .empty {
    text-align: center; padding: 28px 0; color: var(--muted);
    font-size: 12px; line-height: 2;
  }

  @media (max-width: 1024px) { .metrics { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 768px)  { .metrics { grid-template-columns: repeat(2, 1fr); } .mid { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center">
    <span class="logo">⚡ GUCCI <span>QUANT</span></span>
    <span class="badge" id="badge">PAPER</span>
  </div>
  <div class="header-right">
    <span><span class="dot"></span><span id="upd">Loading…</span></span>
  </div>
</header>

<div class="page">

  <!-- Metrics -->
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
      <div class="card-sub" id="tradect">— trades</div>
    </div>
    <div class="card">
      <div class="card-label">Best Rate Today</div>
      <div class="card-val green" id="bestrate">—</div>
      <div class="card-sub">%/hr funding</div>
    </div>
  </div>

  <!-- Chart + Positions -->
  <div class="mid">
    <div class="panel">
      <div class="panel-title">Cumulative PnL</div>
      <canvas id="chart" height="110"></canvas>
      <div id="no-chart" class="empty" style="display:none">
        📊 Chart appears after first completed trade<br>
        <span style="font-size:11px">Bot is live — scanning every 15 minutes</span>
      </div>
    </div>
    <div class="panel">
      <div class="panel-title">Open Positions</div>
      <div id="pos"></div>
    </div>
  </div>

  <!-- Trade history -->
  <div class="panel">
    <div class="panel-title">Trade History</div>
    <div id="trades"></div>
  </div>

  <!-- Scan activity log -->
  <div class="panel" style="margin-top:14px">
    <div class="panel-title">Scan Activity Log <span style="color:#2a2a4a;font-size:10px">— every 15 min</span></div>
    <div id="scanlog" style="max-height:320px;overflow-y:auto"></div>
  </div>

</div>

<script>
let chart = null;

const fmt = (v, d=4) => { const n=+v; return (n>=0?'+':'')+n.toFixed(d); };
const cls = v => +v >= 0 ? 'pos' : 'neg';

async function load() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());

    // Header
    document.getElementById('upd').textContent = 'Updated ' + d.last_updated;
    const badge = document.getElementById('badge');
    if (d.mode === 'false') { badge.textContent='LIVE'; badge.classList.add('live'); }
    else { badge.textContent='PAPER'; badge.classList.remove('live'); }

    // Metrics
    document.getElementById('capital').textContent = '$' + d.capital.toFixed(2);

    const ap = document.getElementById('allpnl');
    ap.textContent = fmt(d.total_pnl) + ' USDC';
    ap.className = 'card-val ' + cls(d.total_pnl);

    const dp = document.getElementById('daypnl');
    dp.textContent = fmt(d.today_pnl) + ' USDC';
    dp.className = 'card-val ' + cls(d.today_pnl);

    document.getElementById('today-date').textContent =
      new Date().toLocaleDateString('en-US', {weekday:'short',month:'short',day:'numeric'});

    const wr = document.getElementById('winrate');
    wr.textContent = d.win_rate + '%';
    wr.className = 'card-val ' + (d.win_rate >= 50 ? 'green' : 'red');
    document.getElementById('tradect').textContent = d.total_trades + ' completed';
    document.getElementById('bestrate').textContent = d.best_rate_pct.toFixed(4) + '%';

    // Chart
    if (d.chart_data.length > 0) {
      document.getElementById('no-chart').style.display = 'none';
      document.getElementById('chart').style.display = 'block';
      const last = d.chart_data[d.chart_data.length - 1];
      const col = last >= 0 ? '#00e676' : '#ff1744';
      if (chart) {
        chart.data.labels = d.chart_labels;
        chart.data.datasets[0].data = d.chart_data;
        chart.data.datasets[0].borderColor = col;
        chart.data.datasets[0].backgroundColor = col + '18';
        chart.update('none');
      } else {
        chart = new Chart(document.getElementById('chart').getContext('2d'), {
          type: 'line',
          data: {
            labels: d.chart_labels,
            datasets: [{
              data: d.chart_data, borderColor: col,
              backgroundColor: col + '18', borderWidth: 2,
              pointRadius: d.chart_data.length < 30 ? 3 : 0,
              tension: 0.35, fill: true,
            }]
          },
          options: {
            responsive: true, animation: false,
            plugins: { legend: { display: false }, tooltip: {
              callbacks: { label: ctx => (ctx.parsed.y >= 0 ? '+' : '') + ctx.parsed.y.toFixed(4) + ' USDC' }
            }},
            scales: {
              x: { ticks: { color:'#4a4a6a', maxTicksLimit:8, font:{size:10} }, grid: { color:'#12121e' } },
              y: { ticks: { color:'#4a4a6a', font:{size:10},
                    callback: v => (v>=0?'+':'')+v.toFixed(3) }, grid: { color:'#12121e' } }
            }
          }
        });
      }
    } else {
      document.getElementById('chart').style.display = 'none';
      document.getElementById('no-chart').style.display = 'block';
    }

    // Open positions
    const posEl = document.getElementById('pos');
    if (!d.positions.length) {
      posEl.innerHTML = '<div class="empty">No open positions<br><span style="font-size:11px">Bot scanning every 15 min for rates > 0.15%/hr</span></div>';
    } else {
      let h = '<table><thead><tr><th>Asset</th><th>Rate/yr</th><th>Size</th><th>Held</th><th>Est. PnL</th></tr></thead><tbody>';
      for (const p of d.positions) {
        h += `<tr>
          <td><span class="tag">${p.asset}</span></td>
          <td>${p.annual_pct}%</td>
          <td>$${(+p.size_usd).toFixed(0)}</td>
          <td>${p.held_hrs}hr</td>
          <td class="${cls(p.est_pnl)}">${fmt(p.est_pnl)}</td>
        </tr>`;
      }
      posEl.innerHTML = h + '</tbody></table>';
    }

    // Trades
    const tEl = document.getElementById('trades');
    if (!d.trades.length) {
      tEl.innerHTML = '<div class="empty">No completed trades yet<br><span style="font-size:11px">Waiting for rate > 0.15%/hr on HYPE · MON · BERA · ANIME · AZTEC</span></div>';
    } else {
      let h = `<table><thead><tr>
        <th>Time (UTC)</th><th>Asset</th><th>Duration</th>
        <th>Size</th><th>Funding earned</th><th>Fees</th><th>Net PnL</th><th>Mode</th>
      </tr></thead><tbody>`;
      for (const t of d.trades) {
        const ts = (t.timestamp||'').substring(5,16).replace('T',' ');
        h += `<tr>
          <td class="dim">${ts}</td>
          <td><span class="tag">${t.asset}</span></td>
          <td>${t.duration_hrs ? (+t.duration_hrs).toFixed(1)+'hr' : '—'}</td>
          <td>$${(+t.size_usd||0).toFixed(0)}</td>
          <td class="pos">+${(+t.gross_pnl||0).toFixed(4)}</td>
          <td class="dim">-${(+t.fees||0).toFixed(4)}</td>
          <td class="${cls(t.net_pnl)}">${fmt(t.net_pnl)}</td>
          <td class="dim">${t.paper ? '📄' : '💰'}</td>
        </tr>`;
      }
      tEl.innerHTML = h + '</tbody></table>';
    }

    // Scan activity log
    const slEl = document.getElementById('scanlog');
    if (!d.scans || !d.scans.length) {
      slEl.innerHTML = '<div class="empty">No scan data yet — appears after first 15-min cycle</div>';
    } else {
      let h = `<table><thead><tr>
        <th>Time (UTC)</th><th>Efficiency</th><th>Funding in</th>
        <th>Top Asset</th><th>Top Rate</th><th>Opportunities</th><th>Action</th>
      </tr></thead><tbody>`;
      for (const s of d.scans) {
        const ts = (s.timestamp||'').substring(5,16).replace('T',' ');
        const eff = s.efficiency || 0;
        const effCol = eff >= 80 ? 'pos' : eff >= 50 ? '' : 'neg';
        const action = s.action || '—';
        let actionCol = 'dim';
        if (action.startsWith('HOLDING')) actionCol = 'pos';
        else if (action.includes('ENTERED')) actionCol = 'green';
        const topRate = s.top_rate_pct ? s.top_rate_pct.toFixed(4) + '%/hr' : '—';
        h += `<tr>
          <td class="dim">${ts}</td>
          <td class="${effCol}">${eff}%</td>
          <td class="dim">${s.mins_to_fund != null ? s.mins_to_fund + ' min' : '—'}</td>
          <td>${s.top_asset ? '<span class="tag">'+s.top_asset+'</span>' : '<span class="dim">—</span>'}</td>
          <td>${topRate}</td>
          <td class="dim" style="text-align:center">${s.opportunities || 0}</td>
          <td class="${actionCol}" style="font-size:11px">${action}</td>
        </tr>`;
      }
      slEl.innerHTML = h + '</tbody></table>';
    }

  } catch(e) {
    document.getElementById('upd').textContent = '⚠ Connection error — retrying…';
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
    print(f"  📊  GUCCI QUANT DASHBOARD")
    print(f"  Open in browser: http://0.0.0.0:{port}")
    print(f"  From your Mac:   http://187.124.41.102:{port}")
    print(f"{'─'*48}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
