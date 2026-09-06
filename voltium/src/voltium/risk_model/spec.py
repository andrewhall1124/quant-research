"""The one factor definition the risk model and the signal both read.

The signal residualises the variance premium against the same factors the
risk model prices, so that what the optimizer is told is alpha is orthogonal
to what it is told is risk. Both modules take a `FactorSpec` and call
`factor_names` rather than spelling their own list.

Factors:

* `market`: the per-unit-vega P&L of the SPX reference straddle when the
  index chain is on disk (`market_source="spx"`), otherwise the vega-weighted
  mean across the universe (`"universe"`).
* one factor per GICS sector (`use_sectors=True`): the vega-weighted mean of
  the sector's per-unit-vega P&L, orthogonalised to the market factor.

A name loads on the market and on its own sector only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MARKET = "market"


@dataclass(frozen=True)
class FactorSpec:
    market_source: Literal["spx", "universe"] = "spx"
    use_sectors: bool = True
    window: int = 250
    min_observations: int = 120
    shrinkage: Literal["ledoit_wolf", "none"] = "ledoit_wolf"

    def factor_names(self, sectors: list[str]) -> list[str]:
        names = [MARKET]
        if self.use_sectors:
            names.extend(sorted(sectors))
        return names
