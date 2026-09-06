"""Transaction costs.

atium's `CostModel.compute_costs(old_weights, new_weights, capital)` works on
a weight vector because every equity trade is the same kind of thing. An
option book trades two kinds: contracts, priced off a quote, and hedge
shares, priced per share. So the ABC here is per *fill*: `compute(quantity,
bid, ask, price)` returns dollars for one trade, and the backtester holds
one model for options and one for the hedge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from voltium.loaders import CONTRACT_MULTIPLIER


class CostModel(ABC):
    @abstractmethod
    def compute(self, quantity: float, bid: float, ask: float, price: float) -> float:
        """Dollar cost of trading |quantity| units at the given quote."""


class NoCost(CostModel):
    def compute(self, quantity: float, bid: float, ask: float, price: float) -> float:
        return 0.0


@dataclass(frozen=True)
class HalfSpreadCost(CostModel):
    """Option fill: cross `fraction` of the half-spread per share, plus a fee.

    `quantity` is contracts; the per-share spread is scaled by the contract
    multiplier. `fraction=1.0` pays the full half-spread (mid to touch).
    """

    fraction: float = 1.0
    fee_per_contract: float = 0.0

    def compute(self, quantity: float, bid: float, ask: float, price: float) -> float:
        contracts = abs(quantity)
        half_spread = max(ask - bid, 0.0) / 2.0
        return contracts * (half_spread * self.fraction * CONTRACT_MULTIPLIER + self.fee_per_contract)


@dataclass(frozen=True)
class PerShareCost(CostModel):
    """Stock hedge fill: a fixed number of dollars per share traded."""

    per_share: float = 0.005

    def compute(self, quantity: float, bid: float, ask: float, price: float) -> float:
        return abs(quantity) * self.per_share
