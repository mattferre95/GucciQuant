"""
GUCCI QUANT — Shared trading constants
Single source of truth. Previously FEE_RATE lived in 4 files with two
different values (0.0010 vs 0.0011), so the risk gate evaluated trades
against cheaper fees than the P&L log recorded. Unified on the
conservative value.

Fee breakdown (verified against the account's actual HL fee tier):
  Perp entry (ALO maker 0.015%) + perp exit (taker 0.045%) = 0.060% of leg
  Spot entry (maker ~0.040%)    + spot exit (taker ~0.100%) = 0.140% of leg
  Total ≈ 0.20% of one leg = 0.10% of combined (2-leg) notional,
  plus slippage margin → 0.11% of combined notional.

Fees are charged as: size_usd × 2 × FEE_RATE  (both legs, round trip).
"""

# Round-trip fee rate applied to combined (2×leg) notional — conservative,
# includes slippage margin on top of the verified 0.10% fee floor.
FEE_RATE = 0.0011

# Hyperliquid rejects orders below $10 notional. Orders sized under this
# floor would be silently rejected in live mode — on a delta-neutral pair
# that can leave one leg filled and the other rejected (directional
# exposure). Slight buffer over the exchange's $10 minimum.
MIN_ORDER_USD = 10.50
