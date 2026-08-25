# data_store

Everything here is pulled data, gitignored except this file. Nothing is
hand-edited: any file can be rebuilt by re-running the pipeline that owns it.

| File | Owner pipeline | Contents |
|---|---|---|
| `universe.parquet` | `universe` | point-in-time S&P 500 membership, one row per (date, ticker) |
| `underlying_2025.parquet` | `underlying` | EOD stock OHLC + NBBO for the 2025 universe |
| `options_2025/<SYM>.parquet` | `options` | EOD option chains, one file per single-name symbol (519 files, ~1.6 GB) |
| `index_options_2025/<ROOT>.parquet` | `options --symbols` | EOD index chains: SPX (monthlies), SPXW (weeklies), XSP |
| `indices.parquet` | `reference` | SPX, RUT, OEX, XSP + VIX1D/9D/VIX/3M/1Y, VVIX, SKEW |
| `yields.parquet` | `reference` | CBOE treasury yield indices (13w, 5y, 10y, 30y), decimal |
| `rates.parquet` | `reference` | SOFR overnight, decimal |
| `fred_rates.parquet` | `reference` | FRED treasury + SOFR series, decimal |

Year-stamped names mark the expensive per-symbol pulls, which are pinned to the
window they were pulled for. Reference tables are cheap to re-pull in full and
carry no year.

Coverage: options and underlying are calendar 2025; the reference tables run
2024-01-02 to 2025-12-31, the free tier's index history floor.

Read these through `data_access_layer`, not by path.
