"""Risk model read from the store, atium-style.

atium's `FactorRiskModelConstructor` is handed three providers — factor
loadings, factor covariances, idio vol — each answering `get(date)`, and
assembles a `FactorRiskModel` per date. This is that shape over the tables
`data_pipelines.cli vol-risk-model` writes:

    vol_factor_loadings.parquet      (date, symbol, factor, loading)
    vol_factor_covariances.parquet   (date, factor_1, factor_2, covariance)
    vol_idio_vol.parquet             (date, symbol, idio_vol)
    vol_factor_returns.parquet       (date, factor, ret)

Everything is scanned lazily and collected for the requested window once;
`get_risk_model(date)` is then a filter. The in-process
`FactorRiskModelConstructor` (`constructor.py`) estimates the same thing
from reference returns on the fly and stays for tests and for instruments
that have not been pipelined.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from voltium.loaders import (
    DataPaths,
    scan_factor_covariances,
    scan_factor_loadings,
    scan_factor_returns,
    scan_idio_vol,
)
from voltium.providers.base import PanelProvider
from voltium.risk_model.base import RiskModelConstructor
from voltium.risk_model.factor import FactorRiskModel
from voltium.risk_model.spec import MARKET


class FactorLoadingsProvider(PanelProvider):
    """`get(date) -> (date, symbol, factor, loading)`."""

    def __init__(self, paths: DataPaths, start: dt.date | None, end: dt.date | None) -> None:
        PanelProvider.__init__(self, scan_factor_loadings(paths, start, end).collect())


class FactorCovariancesProvider(PanelProvider):
    """`get(date) -> (date, factor_1, factor_2, covariance)`."""

    def __init__(self, paths: DataPaths, start: dt.date | None, end: dt.date | None) -> None:
        PanelProvider.__init__(self, scan_factor_covariances(paths, start, end).collect())


class IdioVolProvider(PanelProvider):
    """`get(date) -> (date, symbol, idio_vol)`."""

    def __init__(self, paths: DataPaths, start: dt.date | None, end: dt.date | None) -> None:
        PanelProvider.__init__(self, scan_idio_vol(paths, start, end).collect())


class StoredFactorRiskModelConstructor(RiskModelConstructor):
    def __init__(
        self,
        paths: DataPaths,
        start: dt.date | None = None,
        end: dt.date | None = None,
        loadings: FactorLoadingsProvider | None = None,
        covariances: FactorCovariancesProvider | None = None,
        idio_vol: IdioVolProvider | None = None,
    ) -> None:
        self.paths = paths
        self.loadings = loadings or FactorLoadingsProvider(paths, start, end)
        self.covariances = covariances or FactorCovariancesProvider(paths, start, end)
        self.idio_vol = idio_vol or IdioVolProvider(paths, start, end)
        self.factor_returns_df = scan_factor_returns(paths, None, end).collect()
        self.cache: dict[dt.date, FactorRiskModel] = {}

    def get_risk_model(self, date_: dt.date) -> FactorRiskModel:
        if date_ in self.cache:
            return self.cache[date_]
        loadings_df = self.loadings.get(date_).select("symbol", "factor", "loading")
        idio_df = self.idio_vol.get(date_).select("symbol", "idio_vol")
        cov_df = self.covariances.get(date_)
        if loadings_df.is_empty() or cov_df.is_empty():
            raise ValueError(f"no stored risk model for {date_}; run data_pipelines.cli vol-risk-model")
        names = sorted(cov_df["factor_1"].unique().to_list(), key=lambda f: (f != MARKET, f))
        wide = cov_df.pivot(index="factor_1", on="factor_2", values="covariance")
        aligned = pl.DataFrame({"factor_1": names}).join(wide, on="factor_1", how="left")
        matrix = aligned.select(names).fill_null(0.0).to_numpy()
        model = FactorRiskModel(loadings_df, np.asarray(matrix, dtype=float), names, idio_df)
        self.cache[date_] = model
        return model

    def factor_returns(self, date_: dt.date) -> pl.DataFrame:
        """Stored factor returns up to and including `date_`."""
        return self.factor_returns_df.filter(pl.col("date") <= date_)
