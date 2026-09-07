"""Portfolio state: option units and a stock hedge per symbol.

The state is plain data so a backtest can be stopped, saved and resumed:
`Portfolio.save` / `Portfolio.load` round-trip through JSON. Nothing here
prices anything; marks are stamped on by the backtester.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from voltium.instruments.base import LegSpec, UnitSpec


@dataclass
class Position:
    """`contracts` units of `unit` (signed: long > 0), hedged with `hedge_shares`.

    `last_mid` / `last_underlying` are the marks used for yesterday's
    valuation, so a day with no quote can carry forward without a jump.
    `stale_days` counts consecutive unmarkable sessions.
    """

    unit: UnitSpec
    contracts: float  # integral unless the trade generator is fractional
    hedge_shares: float
    entry_date: dt.date
    last_mid: float
    last_underlying: float
    last_vega: float
    last_delta: float
    stale_days: int = 0

    @property
    def symbol(self) -> str:
        return self.unit.symbol

    def to_dict(self) -> dict:
        return {
            "symbol": self.unit.symbol,
            "legs": [
                {"expiration": leg.expiration.isoformat(), "strike": leg.strike, "right": leg.right}
                for leg in self.unit.legs
            ],
            "ratios": list(self.unit.ratios),
            "contracts": self.contracts,
            "hedge_shares": self.hedge_shares,
            "entry_date": self.entry_date.isoformat(),
            "last_mid": self.last_mid,
            "last_underlying": self.last_underlying,
            "last_vega": self.last_vega,
            "last_delta": self.last_delta,
            "stale_days": self.stale_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        legs = tuple(
            LegSpec(dt.date.fromisoformat(leg["expiration"]), float(leg["strike"]), leg["right"])
            for leg in data["legs"]
        )
        unit = UnitSpec(symbol=data["symbol"], legs=legs, ratios=tuple(data["ratios"]))
        return cls(
            unit=unit,
            contracts=float(data["contracts"]),
            hedge_shares=float(data["hedge_shares"]),
            entry_date=dt.date.fromisoformat(data["entry_date"]),
            last_mid=float(data["last_mid"]),
            last_underlying=float(data["last_underlying"]),
            last_vega=float(data["last_vega"]),
            last_delta=float(data["last_delta"]),
            stale_days=int(data.get("stale_days", 0)),
        )


@dataclass
class Portfolio:
    """The book as of the close of `date`."""

    date: dt.date | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    cumulative_pnl: float = 0.0
    cumulative_cost: float = 0.0

    def symbols(self) -> list[str]:
        return sorted(self.positions)

    def dollar_vega(self, symbol: str) -> float:
        position = self.positions.get(symbol)
        if position is None:
            return 0.0
        return position.last_vega * 100 * position.contracts

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat() if self.date else None,
            "cumulative_pnl": self.cumulative_pnl,
            "cumulative_cost": self.cumulative_cost,
            "positions": [p.to_dict() for p in self.positions.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        positions = {p["symbol"]: Position.from_dict(p) for p in data["positions"]}
        return cls(
            date=dt.date.fromisoformat(data["date"]) if data.get("date") else None,
            positions=positions,
            cumulative_pnl=float(data.get("cumulative_pnl", 0.0)),
            cumulative_cost=float(data.get("cumulative_cost", 0.0)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "Portfolio":
        return cls.from_dict(json.loads(Path(path).read_text()))
