"""
GUCCI QUANT — Risk Agent with Kelly Criterion
Half-Kelly formula for mathematically optimal position sizing.
Hard limits that the bot cannot override.
"""
import os, time
from dotenv import load_dotenv
load_dotenv()


class RiskAgent:
    def __init__(self):
        self.capital         = float(os.getenv("STARTING_CAPITAL", 100))
        self.daily_pnl       = 0.0
        self.daily_trades    = 0
        self.best_rate_today = 0.0

        # Paper mode auto-enables trading so paper simulation actually runs.
        # In live mode, TRADING_ENABLED must be explicitly set to "true".
        paper = os.getenv("PAPER_MODE", "true").lower() == "true"
        self.trading_on = paper or (os.getenv("TRADING_ENABLED", "false") == "true")

        # Hard limits
        self.MAX_DAILY_LOSS  = -float(os.getenv("DAILY_LOSS_LIMIT", 5))
        self.MAX_DEPLOY_PCT  = float(os.getenv("MAX_CAPITAL_DEPLOYED", 80)) / 100
        self.MAX_POSITIONS   = int(os.getenv("MAX_POSITIONS", 3))
        self.MIN_RATE        = 0.0015   # 0.15%/hr minimum
        self.MAX_SPREAD      = 0.0005   # 0.05% max spread
        self.MIN_PREDICTED   = 0.0005   # next period must be positive
        # Real fee breakdown for this account (from HL portfolio page):
        #   Perp entry (ALO maker 0.015%) + Perp exit (taker 0.045%) = 0.060% perp leg
        #   Spot entry (maker ~0.040%)    + Spot exit (taker ~0.100%) = 0.140% spot leg
        #   Total on $10/leg ($20 notional): ($0.006 + $0.014) / $20 = 0.100%
        self.FEE_RATE        = 0.0010   # 0.10% round-trip (verified against actual fee tier)
        self.KELLY_FRACTION  = 0.5      # half-Kelly for safety
        self.MIN_HOLD_HRS    = 1.0      # must hold 1 full funding period before rate-based exit

    def kelly_size(self, funding_rate: float) -> float:
        """
        Half-Kelly criterion: f* = (edge / rate) * 0.5
        Sizes each position based on measured edge after fees.
        """
        edge = funding_rate - self.FEE_RATE
        if edge <= 0:
            return 0
        full_kelly  = edge / funding_rate
        half_kelly  = full_kelly * self.KELLY_FRACTION
        kelly_pct   = min(half_kelly, self.MAX_DEPLOY_PCT)
        per_position = (self.capital * kelly_pct) / self.MAX_POSITIONS
        return round(per_position, 2)

    def _kelly_drawdown_factor(self) -> float:
        """
        Automatically reduce position size during a drawdown.
        Full Kelly above -$1, 75% at half the daily limit, 50% near the limit.
        Protects capital from a bad streak without stopping trading entirely.
        """
        half_limit = self.MAX_DAILY_LOSS / 2   # e.g. -$2.50
        if self.daily_pnl >= -1.0:
            return 1.00
        elif self.daily_pnl >= half_limit:
            return 0.75
        else:
            return 0.50

    def position_size(self, funding_rate: float = 0.002) -> float:
        """Returns USDC size per leg, scaled by Kelly × drawdown factor."""
        raw_size = self.kelly_size(funding_rate)
        scaled   = raw_size * self._kelly_drawdown_factor()
        return max(scaled, 5.0) if scaled > 0 else 0

    def can_enter(self, rate, spread, predicted, n_open=0, verbose=True, trend="stable") -> bool:
        checks = {
            "Trading enabled":     self.trading_on,
            "Daily loss OK":       self.daily_pnl > self.MAX_DAILY_LOSS,
            "Rate above minimum":  rate >= self.MIN_RATE,
            "Spread tight enough": spread <= self.MAX_SPREAD,
            "Next rate positive":  predicted >= self.MIN_PREDICTED,
            "Position slots open": n_open < self.MAX_POSITIONS,
            "Rate not falling":    trend != "falling",
        }
        if verbose:
            for k, v in checks.items():
                print(f"    {'✅' if v else '❌'} {k}")
        return all(checks.values())

    def should_exit(self, pos: dict, rate_now: float) -> tuple:
        """
        Dynamic trailing exit threshold = 33% of entry rate (min 0.03%/hr).

        Examples:
          Entry 0.50%/hr → exit threshold 0.165%/hr  (holds high-rate positions longer)
          Entry 0.20%/hr → exit threshold 0.066%/hr
          Entry 0.15%/hr → exit threshold 0.050%/hr

        Exit rules (priority order):
          1. Rate negative  → exit immediately
          2. Rate < threshold AND fees covered  → take the profit
          3. Rate < threshold AND min hold elapsed  → cut and move on
          4. Otherwise → hold
        """
        held_hrs   = (time.time() - pos["entry_time"]) / 3600
        notional   = pos["size_usd"] * 2
        fees_cost  = notional * self.FEE_RATE
        gross_earn = notional * pos.get("rate", 0) * held_hrs
        fees_covered = gross_earn >= fees_cost

        # Dynamic threshold: trail at 33% of entry rate, floor at 0.03%/hr
        exit_threshold = max(pos.get("rate", self.MIN_RATE) * 0.33, 0.0003)

        if rate_now < 0:
            return True, f"Rate went negative ({rate_now*100:.4f}%)"
        if rate_now < exit_threshold and fees_covered:
            net = gross_earn - fees_cost
            return True, (f"Rate below trail ({rate_now*100:.4f}% < "
                          f"{exit_threshold*100:.4f}%), net +${net:.4f}")
        if rate_now < exit_threshold and held_hrs >= self.MIN_HOLD_HRS:
            return True, (f"Rate below trail ({rate_now*100:.4f}% < "
                          f"{exit_threshold*100:.4f}%), min hold elapsed")
        return False, ""

    def record_trade(self, pnl: float, rate: float):
        self.daily_pnl       += pnl
        self.capital         += pnl
        self.daily_trades    += 1
        self.best_rate_today  = max(self.best_rate_today, rate)
        print(f"    PnL: {pnl:+.4f} | Day: {self.daily_pnl:+.4f} | Capital: ${self.capital:.2f}")

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.best_rate_today = 0.0

    def is_daily_limit_hit(self) -> bool:
        return self.daily_pnl <= self.MAX_DAILY_LOSS
