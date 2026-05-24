"""
GUCCI QUANT — Startup Preflight Validator
5 checks before bot starts. Fails loudly in live mode.
"""
import os, requests
from dotenv import load_dotenv
load_dotenv()

BASE_URL = "https://api.hyperliquid.xyz/info"


def _check_env_vars():
    always  = ["HYPERLIQUID_PRIVATE_KEY", "STARTING_CAPITAL"]
    live    = ["HYPERLIQUID_WALLET_ADDRESS"]
    paper   = os.getenv("PAPER_MODE", "true").lower() == "true"
    missing = [v for v in always if not os.getenv(v)]
    if not paper:
        missing += [v for v in live if not os.getenv(v)]
    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, "All required vars present"


def _check_private_key():
    key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    if not key or key == "0x_your_64char_hex_private_key":
        return False, "Private key is placeholder — update .env"
    if not key.startswith("0x") or len(key) < 60:
        return False, "Key format wrong (needs 0x + 64 hex chars)"
    return True, "Private key format OK"


def _check_api():
    try:
        res = requests.post(BASE_URL, json={"type": "meta"}, timeout=6)
        res.raise_for_status()
        return True, "Hyperliquid API reachable"
    except Exception as e:
        return False, f"API unreachable: {e}"


def _check_spot_assets():
    """
    Hyperliquid spot market hosts native tokens (HYPE, PURR, etc.), not BTC/ETH/SOL.
    We verify at least one tradeable token exists with both spot + perp markets.
    """
    try:
        # Get spot tokens
        res_spot = requests.post(BASE_URL, json={"type": "spotMeta"}, timeout=6)
        spot_names = {t["name"] for t in res_spot.json().get("tokens", [])}
        # Get perp assets
        res_perp = requests.post(BASE_URL, json={"type": "metaAndAssetCtxs"}, timeout=6)
        perp_names = {m["name"] for m in res_perp.json()[0]["universe"]}
        overlap = spot_names & perp_names
        if not overlap:
            return False, "No assets found with both spot + perp markets"
        sample = sorted(overlap)[:5]
        return True, f"Spot+perp markets confirmed: {sample}"
    except Exception as e:
        return False, f"spotMeta check failed: {e}"


def _check_balance():
    if os.getenv("PAPER_MODE", "true").lower() == "true":
        return True, "Paper mode — skipping live balance check"
    try:
        wallet = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "")
        if not wallet:
            return False, "HYPERLIQUID_WALLET_ADDRESS not set"
        res = requests.post(BASE_URL,
            json={"type": "clearinghouseState", "user": wallet}, timeout=6)
        bal  = float(res.json().get("marginSummary", {}).get("accountValue", 0))
        need = float(os.getenv("STARTING_CAPITAL", 100)) * 0.5
        if bal < need:
            return False, f"Insufficient USDC: ${bal:.2f} (need ${need:.2f})"
        return True, f"USDC balance: ${bal:.2f} ✓"
    except Exception as e:
        return False, f"Balance check failed: {e}"


def run_preflight(abort_on_fail=True) -> bool:
    print("\n" + "─"*44)
    print("  🔍  GUCCI QUANT PREFLIGHT CHECKS")
    print("─"*44)
    checks = [
        ("Env vars",       _check_env_vars),
        ("Private key",    _check_private_key),
        ("API",            _check_api),
        ("Spot markets",   _check_spot_assets),
        ("Balance",        _check_balance),
    ]
    all_pass = True
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, str(e)
        print(f"  {'✅' if ok else '❌'}  {name}: {msg}")
        if not ok:
            all_pass = False
    print("─"*44)
    if all_pass:
        print("  🟢  All checks passed — safe to start\n")
    else:
        print("  🔴  Fix issues above before going live\n")
        if abort_on_fail and os.getenv("PAPER_MODE", "true").lower() != "true":
            raise SystemExit("Preflight failed. Fix .env issues before going live.")
    return all_pass
