# CLAUDE.md

Working notes for this repo. Read this before touching anything; append to it
when you learn something that would have saved you time.

## The one architectural rule

Data flows `data_pipelines/` → `data_store/` → `data_access_layer/` →
`tools/` → `research/`.

- Pipelines are the **only** writers. Research is **never** a writer.
- `tools/` is reusable machinery, `research/` is studies. A tool answers "how
  do you backtest an option strategy"; a study answers one question once.
  Studies import tools; **a tool must never import a study**. `tools/` holds
  `backtest/` (the option-strategy engine), `vol_models.py` (the forecasters)
  and `scoring.py` (MZ, the two robust losses, Diebold-Mariano, the covariance
  helpers).
- Nothing outside `data_access_layer/paths.py` hardcodes a path under
  `data_store/`. If a new dataset appears, register it in `paths.py` and give it
  a loader in `loaders.py` — do not `pl.read_parquet` from research code.
- Run everything as a module from the repo root: `uv run python -m
  data_pipelines.reference`. Plain `python data_pipelines/reference.py` breaks
  the `data_access_layer` import.

## Style

User-level `~/.claude/CLAUDE.md` governs: no leading-underscore names, verb
names for functions, noun names for variables, `_df` suffix for real DataFrames
only (not LazyFrames, numpy arrays, or scalars). polars everywhere; pandas only
where a dependency forces it (`arch`, `read_html`).

## Where things are documented

`data_store/README.md` is the data catalog: per-dataset schema, coverage,
owning pipeline, loader and gotchas. Add a new dataset's quirks there, not
here. This file keeps only the quirks that change how you *work*, and the
findings index at the bottom.

## Data quirks worth remembering

- **Holiday rows in `indices.parquet`.** VIX prints on market holidays where SPX
  does not (MLK, Juneteenth, the Jan 9 2025 day of mourning, …). 15 such dates in
  2024-2025. Always drop rows where the SPX level is null before computing
  returns, or you get a spurious zero-return day.
- **SPX vs SPXW.** The `SPX` root holds only third-Friday monthlies; every weekly
  and end-of-month expiration is under `SPXW`. Asking `SPX` for a 30-dte chain
  returns empty on most days. Use `SPXW` for anything dte-targeted.
- **The account is on Options STANDARD** as of 2026-09-02, so greeks, IV and
  open interest all answer. `option_greeks/` holds all of 2025 (114.4M
  contract-days, 8.5 GB) and supersedes `options_2025/` — use
  `load_option_greeks`, whose rows carry `underlying_price` so no spot join is
  needed. The note below is what the free tier looked like, kept because the
  probe is still the way to check.
- **Free tier has no greeks, no IV and no open interest.** Implied vol has to be
  either taken from the VIX complex (free, EOD) or inverted from option mids
  yourself. Server-confirmed: greeks and IV need STANDARD, open interest needs
  VALUE. `research/data_quality/analysis.py` re-probes this in a few seconds.
- **Prices are raw, never adjusted, and the client has no splits endpoint.**
  Fixed by `data_pipelines/corporate_actions.py` (splits + dividends from
  Yahoo). Use `load_underlying(with_actions=True, in_universe=True)` with
  `split_adjusted_return()`, never a bare `close.diff()`. The split session
  itself can still be corrupt: NVDA's 2024-06-10 high is 195.95 against a real
  range near 117-123.
- **Delisted names get a zero row, not a missing row.** HES 2025-07-18, JNPR
  2025-07-02, K 2025-12-11 each end with open=high=low=close=0 (HES with real
  volume attached). Zero, not null, so nothing downstream flags it.
  `load_underlying` drops these by default.
- **Yahoo back-adjusts to today, not to the window end.** A corporate-actions
  pull that stops at the end of the price panel misses later splits and the
  whole name then disagrees (BKNG 25:1 on 2026-04-06 makes 2025 look 25x high).
  `corporate_actions.py` always runs to the present; do not add an `--end`.
- **Never take Yahoo prices as spot.** The one property worth protecting is
  that the option quotes and the stock close are the same 17:15 ET snapshot.
  Yahoo is the split/dividend calendar only, and every name is verified with
  `dal.trusted_symbols()` because Yahoo answers almost any symbol with
  something (VMRK returns a price that does not match the name it reports).
- **Untraded option contracts have close=0, not null.** 53% of contract-days
  never trade, and their OHLC is 0.0. Filter on `volume > 0` or use the mid.
  The EOD chain also has no `date` and no underlying price - the session date
  comes from `created.dt.date()`.
- **History depth is quoted per request, by tier.** Index history: 2024-01-01 is
  free, 2023 asks for VALUE, 2022 STANDARD, 2020 PROFESSIONAL. Stocks and options
  reach 2023-06-01 on free.
- **365-day cap per request** (`INVALID_ARGUMENT`). `reference.date_chunks`
  stitches longer windows; anything new that spans years needs the same.
- **One ThetaClient per process.** A second one invalidates the first with
  "Invalid session ID". Always go through `data_pipelines.common.make_client`.
- **Free tier is 1 server thread**, so 2 workers is the practical ceiling; more
  returns `RESOURCE_EXHAUSTED` and the retry backoff eats the gain.

## Cost of a re-pull

Do not casually re-run the expensive pulls. Full-year 2025 at 2 workers:
option chains 3.5 hr / 1.6 GB, underlying 17 min, universe and reference
seconds-to-a-minute. `options` is resumable — it skips symbols already on disk,
so an interrupted pull can just be re-run.

## Research conventions

`research/<topic>/` holds `README.md` (what and how to run), `analysis.py`
(regenerates every figure from scratch), `figures/*.png`, `results/*.csv`, and
`REPORT.md` (the findings, referencing those pngs). Anything shared by more
than one study lives in `tools/`, not at the `research/` root:
`tools/vol_models.py` (the forecasters), `tools/scoring.py` (Mincer-Zarnowitz,
the two robust losses, Diebold-Mariano, and the covariance helpers) and
`tools/backtest/` (the option-strategy engine).

A study may cache an expensive intermediate under its own `results/` — as
`single_name_vol/panel.py` does, ~15 minutes of chain inversion — but
`data_store/` still belongs to the pipelines alone.

- `research/` and each topic folder need an `__init__.py`, because studies are
  run as modules: `uv run python -m research.volatility.analysis`.
- Overlapping horizons are everywhere in this kind of work. Always use
  Newey-West errors with `h` lags; without them t-statistics run about
  `sqrt(h)` times too large.
- **Never judge a forecast by the t-statistic on β=0** in `outcome ~ a + b·f`.
  It tests "carries some information", which anything vol-shaped passes, and it
  ranks badly — in `research/volatility/` it puts ARCH (t=3.45, R²=0.03) above
  trailing RV (t=1.88, R²=0.10). Use the layered scoring that study sets up:
  MZ **joint** α=0/β=1 Wald for calibration, RMSE **and** QLIKE for accuracy
  (the two losses robust to a noisy vol proxy — MAE and correlation are not,
  and must not rank), Diebold-Mariano on the loss differential for pairwise
  significance, encompassing for information. Always include a constant
  benchmark; "beats a level guess" is a real hurdle and several models here
  fail it.
- **Pooled panels need Driscoll-Kraay, not Newey-West.** ~500 names on the same
  dates share a market factor, so residuals correlate across the panel every
  day, and NW — which only prices the time dimension — overstates t-statistics
  by 3-7x in `research/single_name_vol/`. Use
  `tools.scoring.driscoll_kraay_kwargs`; statsmodels calls it
  `cov_type="hac-groupsum"`, and its Wald test needs the chi-square form
  because the F form returns NaN there. The correction bites hardest on
  Diebold-Mariano tests, where serial correlation is nearly irrelevant and
  cross-sectional correlation is everything.
- Report what the sample cannot establish, not just what it shows. The
  2024-2025 window has one dominant shock (April 2025), so point estimates are
  clean and pairwise significance usually is not.

### Findings so far

`research/vrp_cross_section/` — the first strategy study, and the first user of
`tools/backtest/`. Ranking single names daily on how rich their 30-day ATM
straddle is (each name's IV against its own 60-day history), buying the
cheapest decile against the richest, vega-weighted and delta-hedged, produces a
clean gross signal — all ten deciles split on sign, and the raw-IV control
loses (Sharpe -0.75), so the normalisation rather than the vol level is doing
the work. It is not tradeable: break-even is 25.4% of the quoted spread at a
5-day hold and 14.6% at 21 days, against the 50% an ordinary half-spread-each-way
assumption charges. The textbook `IV - E[RV]` definition is the *worst* real
signal in the race, because with GARCH badly calibrated on these names the sort
ranks mostly on forecast error. Two structural findings: the gross edge
concentrates in illiquid names, so the OI screen and the signal pull against
each other; and a decile sort is incompatible with a real liquidity screen at
all, because the median day only offers 15 eligible names at an OI floor of 500.

`research/data_quality/` — audit behind the corporate-actions work. The feed
itself is near-spotless: over 114M stored
contract-days, 0.008% crossed quotes, 0.004% missing quotes, no nulls, no
duplicates, no negative prices. Carry-adjusted put-call parity on ATM pairs
prices back to the cash close within 3-8 bp for liquid names, so the quote legs
and the close are genuinely simultaneous. Every real defect is a corporate
action (see the quirks above) or a reused ticker (SOLS returns four Jan-Apr 2025
rows at $0.0001 before its actual 2025-10-30 listing - the BNY problem again).

`research/single_name_vol/` — the same horse race across 469 S&P 500 names,
2025. Implied vol wins again and this time significantly: it beats every rival
on both losses at both horizons, 29 of 32 pairwise DM tests clear 5% under
Driscoll-Kraay, and it encompasses all three (coefficient ~0.85, t of 14-16).
It is the only near-calibrated forecast (MZ slope 0.86-0.90 vs 0.23-0.45, and
the only zero intercept). It does not win everywhere: lowest QLIKE in 343/469
names at h=5 and 293 at h=21, with a per-name level guess taking 82-108.
Trailing RV is the worst forecast in the study under QLIKE while looking
competitive on RMSE. The variance risk premium is positive in 86-97% of names
but proportionally smaller than the index's at a month (1.14x vs 1.21x) and
spans -22 to +14 points. The inverted 30-day ATM IV matches ThetaData's own
`implied_vol` to a median 0.074 vol points over 469 names and 51,965 name-days
— the check used to run on 71 names, and now covers the whole 2025 greeks
store, with the same answer — so the measure
is not carrying the result.

`research/volatility/` — implied vol beats trailing RV, ARCH(5) and GARCH(1,1)
at forecasting both forward realized and forward implied vol, at 5 and 21 days,
on both RMSE and QLIKE, and encompasses all three. It is well-scaled (MZ slope
≈ 1) but biased high by ~3.3 vol points, the variance risk premium, and that
bias makes the joint calibration test reject at h=5 even though the slope test
passes. Significance is thin: 61 of 80 DM tests fail to reject, and the only
accuracy win on forward realized vol is IV over trailing RV at h=5 under QLIKE
(t=-2.01), which squared error misses. Nothing forecasts forward implied vol at
a month; ARCH/GARCH have ~zero explanatory power there and the expanding-mean
constant has the lowest RMSE of anything. A 30-day ATM IV
rebuilt from the SPXW chain correlates 0.991 with VIX and sits 3.7 points below
it (put skew), so VIX is a safe stand-in for information questions.

## Subscription tiers (checked 2026-09-02)

Subscriptions are sold per asset class; there is no bundle. Annual billing is
20% off the monthly price.

Options — Value $40, Standard $80, Pro $160 /mo. Stocks — Value $30, Standard
$80, Pro $160 /mo. First access date by tier: options FREE 2023-06-01, VALUE
2020-01-01, STANDARD 2016-01-01, PRO 2012-06-01; stocks FREE 2023-06-01, VALUE
2021-01-01, STANDARD 2016-01-01, PRO 2012-06-01.

- **EOD greeks + IV need Options STANDARD.** The tier table on the
  Subscriptions page shows "Greeks 1st Order" and "Implied Volatility" from
  VALUE up, but that row refers to the intraday endpoints, and even those carry
  a Standard/Pro badge on their own doc pages. The one endpoint that gives
  greeks and IV off the EOD report — `/v3/option/history/greeks/eod` — is
  badged Standard/Pro. Do not buy VALUE expecting EOD greeks.
- **The open-interest endpoint takes a date range**, unlike the EOD greeks one,
  so a symbol-year is a single request rather than ~250 and a 500-name year is
  ~3 hours rather than a day. It is also stamped pre-open (~06:30 ET) and
  reports the position standing after the *previous* close, which is the number
  a trader forming at today's close actually knows — so it joins onto the same
  date with no shift, but it is settled and one day stale.
- **Single-name option liquidity is thinner than index intuition suggests.**
  The median 30-day ATM straddle in the S&P 500 carries 19 contracts of open
  interest on its thinner leg, and quotes at 19.8% of mid (36.8% at the 75th
  percentile). Any strategy study has to price execution before it reports a
  Sharpe, and any cross-sectional sort has to check how many names survive its
  screen — see `research/vrp_cross_section/`.
- **Open interest is cheap.** `/v3/option/history/open_interest` is badged
  Value/Standard/Pro (the subscription table claims FREE); it is not what
  forces the tier.
- **Concurrency scales with tier**: FREE 30 reqs/min, VALUE 2, STANDARD 4, PRO
  8 concurrent. STANDARD doubles the 2-worker ceiling noted above.
- **`expiration=*` must be requested day by day** on the EOD greeks endpoint,
  so an SP500 pull is symbols x trading days, not symbols x expirations.
