"""
GUCCI QUANT v2.0 — EMA/RSI Scalping Signal Agent
Fetches 5m candles from Hyperliquid, computes EMA 9/21 + RSI 14.

Entry:
  LONG  — EMA9 > EMA21 AND RSI > 50
  SHORT — EMA9 < EMA21 AND RSI < 50
  FLAT  — no actionable signal
"""
import time
import requests

BASE_URL   = "https://api.hyperliquid.xyz/info"
CANDLE_MS  = 300_000   # 5 minutes in milliseconds
EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14


def fetch_closes(asset: str, n: int = 100) -> list:
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - (n + 10) * CANDLE_MS
    res = requests.post(
        BASE_URL,
        json={"type": "candleSnapshot", "req": {
            "coin": asset, "interval": "5m",
            "startTime": start_ms, "endTime": now_ms,
        }},
        timeout=10,
    )
    candles = res.json()
    return [float(c["c"]) for c in candles[-n:]]


def _ema_series(closes: list, period: int) -> list:
    k   = 2 / (period + 1)
    ema = closes[0]
    out = [ema]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
        out.append(ema)
    return out


def _rsi(closes: list, period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g  = sum(gains[:period]) / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1 + avg_g / avg_l))


def get_signal(asset: str) -> dict:
    """
    Returns dict: {signal, ema_fast, ema_slow, rsi, price}
    signal: "LONG" | "SHORT" | "FLAT"
    """
    closes = fetch_closes(asset)
    if len(closes) < EMA_SLOW + RSI_PERIOD:
        return {"signal": "FLAT", "ema_fast": None, "ema_slow": None, "rsi": None, "price": None}

    fast_series = _ema_series(closes, EMA_FAST)
    slow_series = _ema_series(closes, EMA_SLOW)

    ema_fast = fast_series[-1]
    ema_slow = slow_series[-1]
    rsi      = _rsi(closes)
    price    = closes[-1]

    if ema_fast > ema_slow and rsi > 50:
        signal = "LONG"
    elif ema_fast < ema_slow and rsi < 50:
        signal = "SHORT"
    else:
        signal = "FLAT"

    return {
        "signal":   signal,
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "rsi":      round(rsi, 2),
        "price":    round(price, 2),
    }
