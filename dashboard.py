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
  .logo-sub { font-size: 10px; color: var(--muted); letter-spacing: 1px; margin-top: 3px; }
  .badge { margin-left: 12px; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700;
    letter-spacing: 1px; background: #0a2a18; color: var(--green); border: 1px solid var(--green); }
  .badge.live { background: #2a0a10; color: var(--red); border-color: var(--red); }
  .header-right { color: var(--muted); font-size: 11px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green);
    display: inline-block; margin-right: 6px; animation: blink 2s ease-in-out infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }

  .page { padding: 20px 28px; max-width: 1440px; margin: 0 auto; }
  .gap { margin-bottom: 14px; }

  .metrics { display: grid; grid-template-columns: repeat(8,1fr); gap: 12px; margin-bottom: 14px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .card-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px; }
  .card-val { font-size: 22px; font-weight: 700; color: var(--text); line-height: 1; }
  .card-val.green { color: var(--green); } .card-val.red { color: var(--red); }
  .card-val.yellow { color: var(--yellow); }
  .card-sub { font-size: 11px; color: var(--muted); margin-top: 5px; }

  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }
  .panel-title { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .panel-title::after { content:''; flex:1; height:1px; background:var(--border); }
  .panel-sub { font-size: 10px; color: var(--muted); margin-left: 6px; }

  .two-col   { display: grid; grid-template-columns: 3fr 2fr; gap: 14px; }
  .two-col-eq{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .right-col { display: flex; flex-direction: column; gap: 14px; }

  table { width: 100%; border-collapse: collapse; }
  th { font-size: 10px; color: var(--muted); font-weight: 500; text-transform: uppercase;
    letter-spacing: 1px; padding: 0 0 8px 0; text-align: left; border-bottom: 1px solid var(--border); }
  td { padding: 9px 0; border-bottom: 1px solid #0e0e18; color: #b0b8d0; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }

  .tag { background: #141424; color: var(--accent); padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 1px; border: 1px solid #2a2a4a; }
  .tag.long  { background: #0a2010; color: var(--green); border-color: #1a4a2a; }
  .tag.short { background: #200a10; color: var(--red);   border-color: #4a1a2a; }
  .tag.flat  { background: #141424; color: var(--muted); border-color: #2a2a4a; }

  .pos { color: var(--green) !important; } .neg { color: var(--red) !important; }
  .dim { color: var(--muted); }
  .empty { text-align: center; padding: 24px 0; color: var(--muted); font-size: 12px; line-height: 2; }
  .scroll-table { max-height: 300px; overflow-y: auto; }

  .pos-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
  .pos-cell { background: #0a0a18; border-radius: 6px; padding: 10px 12px; }
  .pos-cell-label { font-size: 10px; color: var(--muted); margin-bottom: 4px; letter-spacing: 1px; }
  .pos-cell-val { font-size: 15px; font-weight: 700; }

  .cfg-row { display: flex; justify-content: space-between; padding: 7px 0;
    border-bottom: 1px solid var(--border); font-size: 12px; }
  .cfg-row:last-child { border-bottom: none; }

  @media (max-width:1200px) { .metrics{grid-template-columns:repeat(4,1fr)} }
  @media (max-width:768px)  { .metrics{grid-template-columns:repeat(2,1fr)} .two-col,.two-col-eq{grid-template-columns:1fr} }
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center">
    <div>
      <div style="display:flex;align-items:center">
        <span class="logo">⚡ GUCCI <span>QUANT</span></span>
        <span class="badge" id="badge">PAPER</span>
      </div>
      <div class="logo-sub">EMA 9/21 + RSI 14 SCALPING · BTC-PERP · 5M CANDLES</div>
    </div>
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
      <div class="card-label">Annual Return</div>
      <div class="card-val green" id="annual">—</div>
      <div class="card-sub">extrapolated</div>
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
      <div class="card-label">Last Signal</div>
      <div class="card-val" id="lastsig">—</div>
      <div class="card-sub">EMA + RSI</div>
    </div>
  </div>

  <!-- ── PnL Chart + Right Column ── -->
  <div class="two-col gap">
    <div class="panel">
      <div class="panel-title">Cumulative PnL</div>
      <canvas id="pnl-chart" height="130"></canvas>
      <div id="no-pnl-chart" class="empty" style="display:none">
        📊 Chart appears after the first completed trade
      </div>
    </div>

    <div class="right-col">
      <!-- Open Position -->
      <div class="panel">
        <div class="panel-title">Open Position</div>
        <div id="pos"></div>
      </div>

      <!-- Strategy Config -->
      <div class="panel">
        <div class="panel-title">Strategy Config</div>
        <div id="cfg"></div>
      </div>
    </div>
  </div>

  <!-- ── RSI History ── -->
  <div class="panel gap">
    <div class="panel-title">RSI 14 History <span class="panel-sub">— last 96 scans · dashed lines: 30 / 50 / 70</span></div>
    <canvas id="rsi-chart" height="70"></canvas>
    <div id="no-rsi-chart" class="empty" style="display:none">
      📈 RSI history appears after the first scan cycle
    </div>
  </div>

  <!-- ── Daily + Attribution ── -->
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
    <div class="panel-title">Trade History <span class="panel-sub" id="trade-count"></span></div>
    <div id="trades"></div>
  </div>

  <!-- ── Signal Log ── -->
  <div class="panel">
    <div class="panel-title">Signal Log <span class="panel-sub">— every 5 min scan</span></div>
    <div id="scanlog" class="scroll-table"></div>
  </div>

</div>

<script>
let pnlChart = null, rsiChart = null;

const fmt = (v, d=4) => { const n=+v; return (n>=0?'+':'')+n.toFixed(d); };
const cls = v => +v >= 0 ? 'pos' : 'neg';
const $   = id => document.getElementById(id);

function sigTag(sig) {
  const c = sig==='LONG'?'long':sig==='SHORT'?'short':'flat';
  return `<span class="tag ${c}">${sig}</span>`;
}

async function load() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());

    // Header
    $('upd').textContent = 'Updated ' + d.last_updated;
    const badge = $('badge');
    if (d.mode === 'false') { badge.textContent='LIVE'; badge.classList.add('live'); }
    else { badge.textContent='PAPER'; badge.classList.remove('live'); }

    // ── Metrics ──
    $('capital').textContent = '$' + d.capital.toFixed(2);

    const ap = $('allpnl');
    ap.textContent  = fmt(d.total_pnl) + ' USDC';
    ap.className    = 'card-val ' + cls(d.total_pnl);

    const dp = $('daypnl');
    dp.textContent  = fmt(d.today_pnl) + ' USDC';
    dp.className    = 'card-val ' + cls(d.today_pnl);
    $('today-date').textContent = new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});

    const wr = $('winrate');
    wr.textContent  = d.win_rate + '%';
    wr.className    = 'card-val ' + (d.win_rate >= 50 ? 'green' : 'red');
    $('tradect').textContent = d.total_trades + ' completed';

    const ar = $('annual');
    ar.textContent  = (d.annual_return_pct||0).toFixed(1) + '%';
    ar.className    = 'card-val ' + ((d.annual_return_pct||0)>20?'green':(d.annual_return_pct||0)>0?'yellow':'red');

    const sh = $('sharpe');
    sh.textContent  = d.sharpe || '—';
    sh.className    = 'card-val ' + (d.sharpe>1?'green':d.sharpe<0?'red':'yellow');

    const md = $('maxdd');
    md.textContent  = d.max_drawdown_pct + '%';
    md.className    = 'card-val ' + (d.max_drawdown_pct<5?'green':d.max_drawdown_pct<15?'yellow':'red');

    const ls = $('lastsig');
    ls.textContent  = d.last_signal;
    ls.className    = 'card-val ' + (d.last_signal==='LONG'?'green':d.last_signal==='SHORT'?'red':'yellow');

    // ── Cumulative PnL chart ──
    if (d.chart_data.length > 0) {
      $('no-pnl-chart').style.display = 'none';
      $('pnl-chart').style.display = 'block';
      const last = d.chart_data[d.chart_data.length-1];
      const col  = last >= 0 ? '#00e676' : '#ff1744';
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

    // ── RSI history chart ──
    if (d.rsi_data && d.rsi_data.length > 0) {
      $('no-rsi-chart').style.display = 'none';
      $('rsi-chart').style.display = 'block';
      const ref = d.rsi_data.map(() => null);
      if (rsiChart) {
        rsiChart.data.labels = d.rsi_labels;
        rsiChart.data.datasets[0].data = d.rsi_data;
        rsiChart.update('none');
      } else {
        rsiChart = new Chart($('rsi-chart').getContext('2d'), {
          type:'line',
          data:{ labels:d.rsi_labels, datasets:[
            { label:'RSI 14', data:d.rsi_data, borderColor:'#7c6fff', backgroundColor:'#7c6fff12',
              borderWidth:1.5, pointRadius:0, tension:0.3, fill:true },
            { label:'70', data:d.rsi_data.map(()=>70), borderColor:'#ff174455', borderWidth:1,
              borderDash:[4,4], pointRadius:0, fill:false },
            { label:'50', data:d.rsi_data.map(()=>50), borderColor:'#4a4a6a', borderWidth:1,
              borderDash:[4,4], pointRadius:0, fill:false },
            { label:'30', data:d.rsi_data.map(()=>30), borderColor:'#00e67655', borderWidth:1,
              borderDash:[4,4], pointRadius:0, fill:false },
          ]},
          options:{ responsive:true, animation:false,
            plugins:{ legend:{ display:false } },
            scales:{
              x:{ticks:{color:'#4a4a6a',maxTicksLimit:10,font:{size:10}},grid:{color:'#12121e'}},
              y:{min:0, max:100, ticks:{color:'#4a4a6a',font:{size:10},stepSize:25},grid:{color:'#12121e'}}
            }}
        });
      }
    } else {
      $('rsi-chart').style.display = 'none';
      $('no-rsi-chart').style.display = 'block';
    }

    // ── Open position ──
    const posEl = $('pos');
    if (!d.positions || !d.positions.length) {
      posEl.innerHTML = `<div class="empty">No position open<br>
        <span style="font-size:11px">Last signal: ${sigTag(d.last_signal)}</span></div>`;
    } else {
      const p   = d.positions[0];
      const dir = p.direction || 'LONG';
      posEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
          ${sigTag(dir)}
          <span style="font-weight:700;font-size:15px">${p.asset}-PERP</span>
          <span class="dim" style="margin-left:auto;font-size:11px">${p.held_mins}min</span>
        </div>
        <div class="pos-grid">
          <div class="pos-cell">
            <div class="pos-cell-label">ENTRY</div>
            <div class="pos-cell-val">$${(+p.entry_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
          </div>
          <div class="pos-cell">
            <div class="pos-cell-label">CURRENT</div>
            <div class="pos-cell-val">${p.current_price?'$'+(+p.current_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>
          </div>
          <div class="pos-cell">
            <div class="pos-cell-label">STOP LOSS</div>
            <div class="pos-cell-val neg">${p.sl_price?'$'+(+p.sl_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>
            ${p.pct_to_sl!=null?`<div class="dim" style="font-size:10px">${p.pct_to_sl}% away</div>`:''}
          </div>
          <div class="pos-cell">
            <div class="pos-cell-label">TAKE PROFIT</div>
            <div class="pos-cell-val pos">${p.tp_price?'$'+(+p.tp_price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>
            ${p.pct_to_tp!=null?`<div class="dim" style="font-size:10px">${p.pct_to_tp}% away</div>`:''}
          </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding-top:10px;border-top:1px solid var(--border)">
          <span class="dim" style="font-size:11px">$${(+p.size_usd||0).toFixed(2)} × ${d.config.leverage}x = $${(+p.notional||0).toFixed(2)}</span>
          <span class="${cls(p.est_pnl||0)}" style="font-weight:700">${fmt(p.est_pnl||0)} USDC</span>
        </div>`;
    }

    // ── Strategy config ──
    const cfg = d.config || {};
    $('cfg').innerHTML = `
      <div class="cfg-row"><span class="dim">Asset</span><span>${cfg.asset}-PERP</span></div>
      <div class="cfg-row"><span class="dim">Timeframe</span><span>5m candles</span></div>
      <div class="cfg-row"><span class="dim">EMA Fast / Slow</span><span>9 / 21</span></div>
      <div class="cfg-row"><span class="dim">RSI Period</span><span>14 (threshold: 50)</span></div>
      <div class="cfg-row"><span class="dim">Leverage</span><span>${cfg.leverage}x</span></div>
      <div class="cfg-row"><span class="dim">Margin / trade</span><span>${cfg.margin_pct}%</span></div>
      <div class="cfg-row"><span class="dim">Stop Loss</span><span class="neg">${cfg.sl_pct}%</span></div>
      <div class="cfg-row"><span class="dim">Take Profit</span><span class="pos">${cfg.tp_pct}%</span></div>
      <div class="cfg-row"><span class="dim">Daily loss limit</span><span class="neg">-$${cfg.daily_limit}</span></div>`;

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

    // ── PnL attribution ──
    const gross    = +d.total_gross || 0;
    const fees     = +d.total_fees  || 0;
    const net      = +d.total_pnl   || 0;
    const feeRatio = gross > 0 ? (fees / gross * 100).toFixed(1) : '—';
    $('attribution').innerHTML = `
      <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
        <span class="dim">Gross trading PnL</span><span class="pos">+${gross.toFixed(4)} USDC</span>
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
        Sharpe &nbsp;<span style="color:var(--text)">${d.sharpe||'—'}</span>&nbsp;&nbsp;
        Max DD &nbsp;<span style="color:var(--text)">${d.max_drawdown_pct}%</span>&nbsp;&nbsp;
        Win Rate &nbsp;<span style="color:var(--text)">${d.win_rate}%</span>
      </div>`;

    // ── Trade history ──
    $('trade-count').textContent = d.total_trades + ' total';
    const tEl = $('trades');
    if (!d.trades.length) {
      tEl.innerHTML = '<div class="empty">No completed trades yet<br><span style="font-size:11px">Trades appear here once SL, TP, or signal reversal fires</span></div>';
    } else {
      let h = `<table><thead><tr>
        <th>Time (UTC)</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Duration</th>
        <th>Margin</th><th>Gross</th><th>Fees</th><th>Net PnL</th><th></th>
        </tr></thead><tbody>`;
      for (const t of d.trades) {
        const ts   = (t.timestamp||'').substring(5,16).replace('T',' ');
        const dur  = t.duration_hrs ? ((+t.duration_hrs)*60).toFixed(0)+'min' : '—';
        const dir  = t.funding_rate > 0 ? 'LONG' : t.funding_rate < 0 ? 'SHORT' : '—';
        h += `<tr>
          <td class="dim">${ts}</td>
          <td><span class="tag flat" style="font-size:10px">${t.asset}</span></td>
          <td class="dim">${t.entry_price?(+t.entry_price).toFixed(0):'—'}</td>
          <td class="dim">${t.exit_price?(+t.exit_price).toFixed(0):'—'}</td>
          <td class="dim">${dur}</td>
          <td class="dim">$${(+t.size_usd||0).toFixed(0)}</td>
          <td class="pos">+${(+t.gross_pnl||0).toFixed(4)}</td>
          <td class="dim">-${(+t.fees||0).toFixed(4)}</td>
          <td class="${cls(t.net_pnl)}">${fmt(t.net_pnl)}</td>
          <td class="dim">${t.paper?'📄':'💰'}</td></tr>`;
      }
      tEl.innerHTML = h + '</tbody></table>';
    }

    // ── Signal log ──
    const slEl = $('scanlog');
    if (!d.scans || !d.scans.length) {
      slEl.innerHTML = '<div class="empty">No scan data yet — appears after first 5-min cycle</div>';
    } else {
      let h = `<table><thead><tr>
        <th>Time (UTC)</th><th>Signal</th><th>RSI</th><th>EMA Δ%</th><th>Pos</th><th>Details</th>
        </tr></thead><tbody>`;
      for (const s of d.scans) {
        const ts    = (s.timestamp||'').substring(5,16).replace('T',' ');
        const eff   = s.efficiency != null ? s.efficiency : 50;
        const sig   = eff === 100 ? 'LONG' : eff === 0 ? 'SHORT' : 'FLAT';
        const rsi   = s.mins_to_fund != null ? s.mins_to_fund : '—';
        const rsiCls= rsi > 70 ? 'neg' : rsi < 30 ? 'pos' : 'dim';
        const emaDiff = s.top_rate_pct != null ? ((+s.top_rate_pct)>=0?'+':'')+(+s.top_rate_pct).toFixed(4)+'%' : '—';
        const hasPosIcon = s.open_positions ? '●' : '○';
        const posCls = s.open_positions ? 'pos' : 'dim';
        h += `<tr>
          <td class="dim">${ts}</td>
          <td>${sigTag(sig)}</td>
          <td class="${rsiCls}">${rsi}</td>
          <td class="dim">${emaDiff}</td>
          <td class="${posCls}" style="text-align:center">${hasPosIcon}</td>
          <td class="dim" style="font-size:11px">${s.action||'—'}</td></tr>`;
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
    print(f"\n{'─'*52}")
    print(f"  📊  GUCCI QUANT DASHBOARD v3 — Scalping Edition")
    print(f"  Open: http://0.0.0.0:{port}")
    print(f"{'─'*52}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
