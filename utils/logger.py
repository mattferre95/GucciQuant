"""
GUCCI QUANT — SQLite Logger
Crash-safe, concurrent-write-safe, fully queryable.
Replaces CSV. Three tables: trades, signals, positions.
"""
import sqlite3, os
from datetime import datetime, date
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "data/gucci_quant.db")
os.makedirs("data", exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_snapshot (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT,
    asset      TEXT,
    rate_pct   REAL,
    annual_pct REAL
);
CREATE TABLE IF NOT EXISTS scan_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT,
    efficiency   REAL,
    mins_to_fund INTEGER,
    top_asset    TEXT,
    top_rate_pct REAL,
    opportunities INTEGER DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    action       TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT,
    asset        TEXT,
    entry_price  REAL,
    exit_price   REAL,
    size_usd     REAL,
    funding_rate REAL,
    gross_pnl    REAL,
    fees         REAL,
    net_pnl      REAL,
    duration_hrs REAL,
    paper        INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT,
    asset      TEXT,
    rate       REAL,
    rate_pct   REAL,
    annual_pct REAL,
    predicted  REAL,
    spread     REAL,
    action     TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset        TEXT NOT NULL UNIQUE,
    entry_time   TEXT,
    entry_price  REAL,
    size_usd     REAL,
    funding_rate REAL,
    spot_id      TEXT,
    paper        INTEGER DEFAULT 1,
    status       TEXT DEFAULT 'open'
);"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)
        # Migrate existing DBs that predate the spot_id column
        cols = {r[1] for r in c.execute("PRAGMA table_info(positions)")}
        if "spot_id" not in cols:
            c.execute("ALTER TABLE positions ADD COLUMN spot_id TEXT")
    print(f"  🗄️  DB ready: {DB_PATH}")


def log_rate_snapshot(rates: list):
    """Store current rates for all tradeable assets — powers the 24hr rate chart."""
    ts = datetime.utcnow().isoformat()
    with get_conn() as c:
        c.executemany(
            "INSERT INTO rate_snapshot (timestamp, asset, rate_pct, annual_pct) VALUES (?,?,?,?)",
            [(ts, r["asset"], r["rate_pct"], r["annual_pct"]) for r in rates]
        )
    # Keep only last 48hr to prevent unbounded growth
    with get_conn() as c:
        c.execute("DELETE FROM rate_snapshot WHERE timestamp < datetime('now', '-48 hours')")


def log_scan(efficiency, mins_to_fund, top_asset, top_rate_pct,
             opportunities, open_positions, action):
    """Record every 15-min scan cycle for dashboard activity feed."""
    with get_conn() as c:
        c.execute(
            """INSERT INTO scan_log
               (timestamp, efficiency, mins_to_fund, top_asset, top_rate_pct,
                opportunities, open_positions, action)
               VALUES (?,?,?,?,?,?,?,?)""",
            (datetime.utcnow().isoformat(), efficiency, mins_to_fund,
             top_asset, top_rate_pct, opportunities, open_positions, action)
        )


def log_trade(pos, net_pnl, exit_price=0, duration_hrs=1):
    fees = pos.get("size_usd", 0) * 2 * 0.0011
    with get_conn() as c:
        c.execute(
            "INSERT INTO trades VALUES (null,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), pos.get("asset"),
             pos.get("entry_price", 0), exit_price,
             pos.get("size_usd", 0), pos.get("rate", 0),
             round(net_pnl + fees, 6), round(fees, 6),
             round(net_pnl, 6), duration_hrs,
             1 if pos.get("paper", True) else 0)
        )
    print(f"  📝 {pos.get('asset')} logged: {net_pnl:+.4f}")


def log_signal(asset, rate, predicted, spread, action):
    with get_conn() as c:
        c.execute(
            "INSERT INTO signals VALUES (null,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), asset, rate,
             rate * 100, rate * 24 * 365 * 100,
             predicted, spread, action)
        )


def save_open_position(pos):
    with get_conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO positions
               (asset, entry_time, entry_price, size_usd, funding_rate, spot_id, paper, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pos["asset"], datetime.utcnow().isoformat(),
             pos.get("entry_price", 0), pos.get("size_usd", 0),
             pos.get("rate", 0), pos.get("spot_id"),
             1 if pos.get("paper", True) else 0, "open")
        )


def close_saved_position(asset):
    with get_conn() as c:
        c.execute("UPDATE positions SET status='closed' WHERE asset=?", (asset,))


def load_open_positions() -> list:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM positions WHERE status='open'"
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_pnl(d=None) -> float:
    d = d or date.today().isoformat()
    with get_conn() as c:
        return c.execute(
            "SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE date(timestamp)=?", (d,)
        ).fetchone()[0]


def get_total_trades(d=None) -> int:
    d = d or date.today().isoformat()
    with get_conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM trades WHERE date(timestamp)=?", (d,)
        ).fetchone()[0]
