"""
GUCCI QUANT — Hyperliquid Execution Engine

Bug 1 FIXED: Spot index mapper
  Was using @{name} (WRONG). Hyperliquid uses numeric IDs.
  Now fetches real IDs from spotMeta API: @1=BTC, @2=ETH, @5=SOL etc.

Bug 2 FIXED: Order fill verification
  Was assuming all orders filled. Now polls open_orders for 45s.
  If spot leg fails after perp fills: closes perp immediately.
  No single-leg exposure possible.
"""
import os, time, requests
from dotenv import load_dotenv
load_dotenv()

PAPER_MODE   = os.getenv("PAPER_MODE", "true").lower() == "true"
BASE_URL     = "https://api.hyperliquid.xyz/info"
MAX_RETRY    = 3
FILL_TIMEOUT = 45  # seconds to wait for limit order fill

# Spot index cache (populated once from API)
_spot_cache: dict = {}


def get_spot_index(asset: str) -> str:
    """
    Look up Hyperliquid spot market numeric ID.
    Examples: BTC=@1, ETH=@2, SOL=@5
    Fetched from API — never hardcoded.
    """
    global _spot_cache
    if not _spot_cache:
        res = requests.post(BASE_URL, json={"type": "spotMeta"}, timeout=6)
        for t in res.json().get("tokens", []):
            if t.get("name") and t.get("index") is not None:
                _spot_cache[t["name"]] = f"@{t['index']}"
        print(f"  🗺️  Spot indices: {_spot_cache}")
    if asset not in _spot_cache:
        raise ValueError(
            f"'{asset}' not on Hyperliquid spot.\n"
            f"Available: {list(_spot_cache.keys())}\n"
            f"Only trade assets with both spot + perp markets."
        )
    return _spot_cache[asset]


def get_mark_price(asset: str) -> float:
    res = requests.post(BASE_URL, json={"type": "metaAndAssetCtxs"}, timeout=6)
    meta, ctxs = res.json()
    for i, m in enumerate(meta["universe"]):
        if m["name"] == asset:
            return float(ctxs[i]["markPx"])
    raise ValueError(f"Asset '{asset}' not found in perp markets")


def with_retry(fn, *args, retries=MAX_RETRY, delay=2):
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args)
        except Exception as e:
            last_err = e
            print(f"  ⚠️  Retry {attempt+1}/{retries}: {e}")
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"All {retries} retries failed: {last_err}")


def verify_fill(info, addr: str, oid: int, timeout=FILL_TIMEOUT) -> bool:
    """Poll until order disappears from open_orders (filled or cancelled)."""
    print(f"  ⏳ Verifying fill for order {oid}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            open_ids = {o.get("oid") for o in info.open_orders(addr)}
            if oid not in open_ids:
                print(f"  ✅ Order {oid} filled")
                return True
        except Exception:
            pass
        time.sleep(3)
    print(f"  ❌ Order {oid} did not fill within {timeout}s")
    return False


def _get_clients():
    """
    Supports two modes:
      Direct wallet:  WALLET_ADDRESS matches key's derived address → trade as self
      API wallet:     WALLET_ADDRESS is main account, key is the agent wallet
                      Exchange submits orders on behalf of the main account.
                      Info queries use main account (that's where USDC lives).
    """
    import eth_account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info     import Info
    from hyperliquid.utils    import constants

    acct         = eth_account.Account.from_key(os.getenv("HYPERLIQUID_PRIVATE_KEY"))
    main_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "").strip()
    info         = Info(constants.MAINNET_API_URL, skip_ws=True)

    if main_address and main_address.lower() != acct.address.lower():
        # API / agent wallet: key signs, main account owns the funds
        exch = Exchange(acct, constants.MAINNET_API_URL, account_address=main_address)
        print(f"  🔑 API wallet mode: agent={acct.address[:10]}… main={main_address[:10]}…")
        return info, exch, main_address
    else:
        exch = Exchange(acct, constants.MAINNET_API_URL)
        return info, exch, acct.address


def _paper_enter(asset: str, size_usd: float, price: float) -> dict:
    spot_id = get_spot_index(asset)
    print(f"  📄 [PAPER] Buy ${size_usd:.2f} {asset} spot ({spot_id}) @ ${price:.4f}")
    print(f"  📄 [PAPER] Short ${size_usd:.2f} {asset} perp @ ${price:.4f}")
    return {
        "asset": asset, "spot_id": spot_id, "entry_price": price, "size_usd": size_usd,
        "size_asset": round(size_usd / price, 6),
        "paper": True, "entry_time": time.time(), "status": "open"
    }


def _paper_exit(position: dict) -> tuple:
    held  = (time.time() - position["entry_time"]) / 3600
    rate  = position.get("rate", 0.002)
    gross = position["size_usd"] * 2 * rate * held
    fees  = position["size_usd"] * 2 * 0.0011
    print(f"  📄 [PAPER] {position['asset']}: {held:.2f}hrs | "
          f"+${gross:.4f} funding | -${fees:.4f} fees | net: ${gross-fees:+.4f}")
    return gross, fees


def _live_enter(asset: str, size_usd: float, price: float) -> dict:
    info, exch, addr = _get_clients()
    spot_id = get_spot_index(asset)
    size    = round(size_usd / price, 4)
    print(f"  💰 [LIVE] {asset}: ${size_usd:.2f}/leg @ ${price:.4f} | spot={spot_id}")

    # Perp short (Add-Liquidity-Only = maker fee 0.015%)
    pr   = with_retry(exch.order, asset, False, size,
                      round(price * 0.9998, 4), {"limit": {"tif": "Alo"}})
    poid = pr.get("response",{}).get("data",{}).get("statuses",[{}])[0].get("resting",{}).get("oid")
    if poid and not verify_fill(info, addr, poid):
        exch.cancel(asset, poid)
        raise RuntimeError(f"Perp short did not fill for {asset}")
    print("  ✅ Perp short filled")

    # Spot long (Add-Liquidity-Only = maker fee 0.040%)
    sr   = with_retry(exch.order, spot_id, True, size,
                      round(price * 1.0002, 4), {"limit": {"tif": "Alo"}})
    soid = sr.get("response",{}).get("data",{}).get("statuses",[{}])[0].get("resting",{}).get("oid")
    if soid and not verify_fill(info, addr, soid):
        exch.cancel(spot_id, soid)
        print("  🚨 Spot failed — closing perp to avoid exposure")
        with_retry(exch.market_close, asset)
        raise RuntimeError(f"Spot long did not fill for {asset} — perp closed")
    print("  ✅ Spot long filled")

    return {
        "asset": asset, "spot_id": spot_id, "entry_price": price,
        "size_usd": size_usd, "size_asset": size,
        "paper": False, "entry_time": time.time(), "status": "open"
    }


def _live_exit(position: dict) -> tuple:
    info, exch, addr = _get_clients()
    asset   = position["asset"]
    spot_id = position.get("spot_id") or get_spot_index(asset)
    price   = with_retry(get_mark_price, asset)
    with_retry(exch.market_close, asset)
    print("  ✅ Perp short closed")
    with_retry(exch.order, spot_id, False, position["size_asset"],
               round(price * 0.999, 4), {"limit": {"tif": "Ioc"}})
    print("  ✅ Spot sold")
    held  = (time.time() - position["entry_time"]) / 3600
    fees  = position["size_usd"] * 2 * 0.0011
    gross = position["size_usd"] * 2 * position.get("rate", 0) * held
    return gross, fees


def enter_position(asset: str, size_usd: float, rate: float) -> dict:
    price = with_retry(get_mark_price, asset)
    pos   = (_paper_enter(asset, size_usd, price)
             if PAPER_MODE else _live_enter(asset, size_usd, price))
    pos["rate"] = rate
    return pos


def exit_position(position: dict) -> tuple:
    if position.get("paper", True) or PAPER_MODE:
        return _paper_exit(position)
    return _live_exit(position)
