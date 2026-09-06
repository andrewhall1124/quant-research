import datetime as dt

import numpy as np
import polars as pl

from voltium.risk_model.constructor import FactorRiskModelConstructor
from voltium.risk_model.factor import build_factor_returns, ledoit_wolf
from voltium.risk_model.spec import FactorSpec


def simulate_panel(n_days: int = 400, seed: int = 0):
    rng = np.random.default_rng(seed)
    # 15 names per sector, so the sector factor (a vega-weighted mean of its
    # members) is not dominated by any one name's idiosyncratic noise
    names = [f"T{i}" for i in range(15)] + [f"E{i}" for i in range(15)]
    sectors = {n: ("Tech" if n.startswith("T") else "Energy") for n in names}
    true_beta = {n: float(rng.uniform(0.5, 1.5)) for n in names}
    true_sector = {n: float(rng.uniform(0.5, 1.5)) for n in names}
    idio = {n: float(rng.uniform(0.2, 0.6)) for n in names}
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n_days)]
    market = rng.normal(0, 1.0, n_days)
    tech = rng.normal(0, 0.5, n_days)
    energy = rng.normal(0, 0.7, n_days)
    rows = []
    for i, d in enumerate(dates):
        for s, sector in sectors.items():
            f = tech[i] if sector == "Tech" else energy[i]
            r = true_beta[s] * market[i] + true_sector[s] * f + rng.normal(0, idio[s])
            rows.append({"date": d, "symbol": s, "pnl_per_vega": r, "dollar_vega": 50.0})
    returns_df = pl.DataFrame(rows)
    market_df = pl.DataFrame({"date": dates, "pnl_per_vega": market})
    sectors_df = pl.DataFrame({"symbol": list(sectors), "sector": list(sectors.values())})
    return returns_df, market_df, sectors_df, true_beta, idio


def test_loadings_and_idio_vol_are_recovered():
    returns_df, market_df, sectors_df, true_beta, idio = simulate_panel()
    spec = FactorSpec(window=400, min_observations=100)
    constructor = FactorRiskModelConstructor(returns_df, sectors_df, spec, market_df)
    model = constructor.get_risk_model(returns_df["date"].max())
    assert model.factors == ["market", "Energy", "Tech"]
    b = model.loadings(model.symbols)
    for i, s in enumerate(model.symbols):
        assert abs(b[i, 0] - true_beta[s]) < 0.15
        assert abs(model.idio_vol([s])[0] - idio[s]) < 0.1
    sigma = model.covariance(model.symbols)
    assert np.allclose(sigma, sigma.T)
    assert np.linalg.eigvalsh(sigma).min() > 0


def test_ledoit_wolf_shrinks_toward_identity_and_stays_psd():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(30, 8))
    sample = np.cov(x, rowvar=False)
    shrunk = ledoit_wolf(sample, 30)
    assert np.linalg.eigvalsh(shrunk).min() > 0
    # off-diagonal mass shrinks
    off = lambda m: np.sum(np.abs(m - np.diag(np.diag(m))))  # noqa: E731
    assert off(shrunk) < off(sample)
    assert abs(np.trace(shrunk) - np.trace(sample)) < 1e-9


def test_sector_factors_are_orthogonal_to_market():
    returns_df, market_df, sectors_df, *_ = simulate_panel()
    factors_df = build_factor_returns(returns_df, sectors_df, FactorSpec(), market_df)
    wide = factors_df.pivot(index="date", on="factor", values="ret")
    for sector in ("Tech", "Energy"):
        corr = wide.select(pl.corr("market", sector)).item()
        assert abs(corr) < 1e-8
