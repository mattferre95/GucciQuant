"""
GUCCI QUANT — Funding Rate Scanner + Timing Awareness
Hyperliquid pays funding at :00 every hour.
Optimal entry window: :45-:59 (first payment within 15 min).
"""
import requests, time as _time
from datetime import datetime

BASE_URL   = "https://api.hyperliquid.xyz/info"
MIN_RATE   = 0.0015    # 0.15%/hr — above 0.11% fee threshold
MIN_VOLUME = 1_000_000  # $1M daily volume (HL native tokens have lower vol than BTC/ETH)

# Cache of assets that have BOTH spot and perp markets (the only tradeable set)
_tradeable_cache: set = set()


def _get_tradeable_assets() -> set:
    """
    Return assets with both spot + perp markets.
    Hyperliquid spot only lists native tokens (HYPE, PURR, BERA etc.) — not BTC/ETH/SOL.
    This prevents the scanner from flagging untradeable perp-only assets.
    """
    global _tradeable_cache
    if _tradeable_cache:
        return _tradeable_cache
    res_spot = requests.post(BASE_URL, json={"type": "spotMeta"}, timeout=6)
    spot_names = {t["name"] for t in res_spot.json().get("tokens", [])}
    res_perp = requests.post(BASE_URL, json={"type": "metaAndAssetCtxs"}, timeout=6)
    perp_names = {m["name"] for m in res_perp.json()[0]["universe"]}
    _tradeable_cache = spot_names & perp_names
    return _tradeable_cache


def get_rate_trend(asset: str) -> str:
    """
    Fetch last 4 hourly funding rates and return trend direction.
    Returns 'rising', 'stable', or 'falling'.
    Avoids entering into collapsing rate environments — the biggest cause
    of fee-negative trades.
    """
    try:
        start_ms = int((_time.time() - 5 * 3600) * 1000)
        res = requests.post(BASE_URL, json={
            "type": "fundingHistory", "coin": asset, "startTime": start_ms
        }, timeout=6)
        history = res.json()
        if not isinstance(history, list) or len(history) < 3:
            return "stable"
        rates = [float(h["fundingRate"]) for h in history[-4:]]
        mid        = len(rates) // 2
        early_avg  = sum(rates[:mid]) / mid
        late_avg   = sum(rates[mid:]) / (len(rates) - mid)
        change_pct = (late_avg - early_avg) / max(abs(early_avg), 1e-9) * 100
        if change_pct > 15:
            return "rising"
        elif change_pct < -15:
            return "falling"
        return "stable"
    except Exception:
        return "stable"


def get_all_rates() -> list:
    """
    Return current funding rates for ALL tradeable assets (even below threshold).
    Used by the dashboard to plot 24hr rate history per asset.
    """
    tradeable = _get_tradeable_assets()
    try:
        res = requests.post(BASE_URL, json={"type": "metaAndAssetCtxs"}, timeout=6)
        meta, ctxs = res.json()
        return [
            {
                "asset":      meta["universe"][i]["name"],
                "rate_pct":   round(float(ctx.get("funding", 0)) * 100, 6),
                "annual_pct": round(float(ctx.get("funding", 0)) * 24 * 365 * 100, 2),
            }
            for i, ctx in enumerate(ctxs)
            if meta["universe"][i]["name"] in tradeable
        ]
    except Exception:
        return []


def get_opportunities() -> list:
    """
    Return tradeable assets with profitable funding rates, sorted desc.
    Only includes assets with BOTH spot + perp markets on Hyperliquid.
    Includes rate trend so the risk agent can skip falling-rate entries.
    """
    tradeable = _get_tradeable_assets()
    res = requests.post(BASE_URL, json={"type": "metaAndAssetCtxs"}, timeout=6)
    meta, ctxs = res.json()
    opps = []
    for i, ctx in enumerate(ctxs):
        asset = meta["universe"][i]["name"]
        if asset not in tradeable:
            continue
        rate  = float(ctx.get("funding", 0))
        vol   = float(ctx.get("dayNtlVlm", 0))
        price = float(ctx.get("markPx", 0))
        if rate > MIN_RATE and vol > MIN_VOLUME:
            trend = get_rate_trend(asset)
            opps.append({
                "asset": asset, "rate": rate,
                "rate_pct":    rate * 100,
                "annual_pct":  rate * 24 * 365 * 100,
                "volume": vol, "price": price,
                "trend": trend,
            })
    return sorted(opps, key=lambda x: x["rate"], reverse=True)


def get_predicted(asset: str) -> float:
    """Get predicted funding rate for next hourly period."""
    res = requests.post(BASE_URL, json={"type": "predictedFundings"}, timeout=6)
    for item in res.json():
        if item[0] == asset:
            for ex in item[1]:
                if ex[0] == "HlPerp":
                    return float(ex[1].get("fundingRate", 0))
    return 0.0


def check_spread(asset: str) -> float:
    """Return bid-ask spread as decimal. Must be < 0.05% for profitability."""
    res    = requests.post(BASE_URL, json={"type": "l2Book", "coin": asset}, timeout=6)
    levels = res.json()["levels"]
    bid    = float(levels[0][0]["px"])
    ask    = float(levels[1][0]["px"])
    return (ask - bid) / bid


def minutes_to_funding() -> int:
    """Minutes until next hourly funding payment."""
    return 60 - datetime.utcnow().minute


def is_optimal_entry_window() -> bool:
    """
    True during :45-:59 — entering here = first payment within 15 min.

    Example impact ($80 position, 0.20%/hr rate):
      Enter :05 → 55min wait → 1 payment = $0.16 gross - $0.09 fees = $0.07 net
      Enter :50 → 10min wait → 2 payments = $0.32 gross - $0.09 fees = $0.23 net
      → 3x better return for same hold time and same rate.
    """
    return datetime.utcnow().minute >= 45


def entry_efficiency_pct() -> float:
    """0-100%: how efficient is entering right now vs perfect :59 timing."""
    return round((1 - (minutes_to_funding() - 1) / 59) * 100, 1)
