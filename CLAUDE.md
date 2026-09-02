# CLAUDE.md

Working notes for this repo. Read this before touching anything; append to it
when you learn something that would have saved you time.

## The one architectural rule

Data flows `data_pipelines/` → `data_store/` → `data_access_layer/` → `research/`.

- Pipelines are the **only** writers. Research is **never** a writer.
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

## Data quirks worth remembering

- **Holiday rows in `indices.parquet`.** VIX prints on market holidays where SPX
  does not (MLK, Juneteenth, the Jan 9 2025 day of mourning, …). 15 such dates in
  2024-2025. Always drop rows where the SPX level is null before computing
  returns, or you get a spurious zero-return day.
- **SPX vs SPXW.** The `SPX` root holds only third-Friday monthlies; every weekly
  and end-of-month expiration is under `SPXW`. Asking `SPX` for a 30-dte chain
  returns empty on most days. Use `SPXW` for anything dte-targeted.
- **Free tier has no greeks, no IV and no open interest.** Implied vol has to be
  either taken from the VIX complex (free, EOD) or inverted from option mids
  yourself. Server-confirmed: greeks and IV need STANDARD, open interest needs
  VALUE. `research/data_quality/analysis.py` re-probes this in a few seconds.
- **Prices are raw, never adjusted.** `underlying_2025.parquet` holds six
  unadjusted splits (ORLY 15:1, NFLX 10:1, NOW 5:1, IBKR 4:1, TPL 3:1, FAST
  2:1), so a return off `close` books a -93% day on ORLY. The split session can
  also be corrupt: NVDA's 2024-06-10 high is 195.95 against a real range near
  117-123. The client exposes no splits endpoint despite the tier table listing
  one.
- **Delisted names get a zero row, not a missing row.** HES 2025-07-18, JNPR
  2025-07-02, K 2025-12-11 each end with open=high=low=close=0 (HES with real
  volume attached). Zero, not null, so nothing downstream flags it.
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
`REPORT.md` (the findings, referencing those pngs).

- `research/` and each topic folder need an `__init__.py`, because studies are
  run as modules: `uv run python -m research.volatility.analysis`.
- Overlapping horizons are everywhere in this kind of work. Always use
  Newey-West errors with `h` lags; without them t-statistics run about
  `sqrt(h)` times too large.
- Report what the sample cannot establish, not just what it shows. The
  2024-2025 window has one dominant shock (April 2025), so point estimates are
  clean and pairwise significance usually is not.

### Findings so far

`research/data_quality/` — the feed itself is near-spotless: over 114M stored
contract-days, 0.008% crossed quotes, 0.004% missing quotes, no nulls, no
duplicates, no negative prices. Carry-adjusted put-call parity on ATM pairs
prices back to the cash close within 3-8 bp for liquid names, so the quote legs
and the close are genuinely simultaneous. Every real defect is a corporate
action (see the quirks above) or a reused ticker (SOLS returns four Jan-Apr 2025
rows at $0.0001 before its actual 2025-10-30 listing - the BNY problem again).

`research/volatility/` — implied vol beats trailing RV, ARCH(5) and GARCH(1,1)
at forecasting both forward realized and forward implied vol, at 5 and 21 days,
and encompasses all three. It is well-scaled (MZ slope ≈ 1) but biased high by
~3.3 vol points, the variance risk premium. Nothing forecasts forward implied
vol at a month; ARCH/GARCH have ~zero explanatory power there. A 30-day ATM IV
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
- **Open interest is cheap.** `/v3/option/history/open_interest` is badged
  Value/Standard/Pro (the subscription table claims FREE); it is not what
  forces the tier.
- **Concurrency scales with tier**: FREE 30 reqs/min, VALUE 2, STANDARD 4, PRO
  8 concurrent. STANDARD doubles the 2-worker ceiling noted above.
- **`expiration=*` must be requested day by day** on the EOD greeks endpoint,
  so an SP500 pull is symbols x trading days, not symbols x expirations.
