import datetime as dt

import numpy as np
import polars as pl

from voltium.providers.earnings import JumpConfig, TermStructureEarningsAdjuster, count_events_spanned, estimate_jump_variance
from voltium.providers.realized_vol import RealizedVolConfig, compute_close_to_close_panel

DAYS = 365.0


def make_pillars(date_: dt.date, dtes: list[int], diffusive: float, event_dte: int, jump_var: float) -> pl.DataFrame:
    rows = []
    for dte in dtes:
        w = diffusive**2 * dte / DAYS + (jump_var if dte >= event_dte else 0.0)
        rows.append({"date": date_, "symbol": "X", "expiration": date_ + dt.timedelta(days=dte), "dte": dte, "atm_iv": float(np.sqrt(w * DAYS / dte))})
    return pl.DataFrame(rows).with_columns(pl.col("dte").cast(pl.Int64))


def test_term_structure_recovers_the_jump_and_flat_diffusive_vol():
    date_ = dt.date(2024, 3, 1)
    events_df = pl.DataFrame({"symbol": ["X"], "event_date": [date_ + dt.timedelta(days=20)], "session": ["amc"],
                              "event_session": [date_ + dt.timedelta(days=21)]})
    pillars_df = make_pillars(date_, [10, 17, 24, 45, 80, 108], diffusive=0.30, event_dte=21, jump_var=0.0036)
    jump_df = estimate_jump_variance(pillars_df, events_df)
    row = jump_df.row(0, named=True)
    assert row["method"] == "term" and row["days_to_event"] == 21
    assert abs(row["jump_var"] - 0.0036) < 1e-12  # sqrt = 6% implied move

    adjusted = TermStructureEarningsAdjuster(events_df).adjust(pillars_df.lazy()).collect().sort("dte")
    assert (adjusted["atm_iv"] - 0.30).abs().max() < 1e-9  # every pillar back to the diffusive vol


def test_two_events_spanned_are_both_removed():
    date_ = dt.date(2024, 3, 1)
    e1, e2 = date_ + dt.timedelta(days=21), date_ + dt.timedelta(days=112)
    events_df = pl.DataFrame({"symbol": ["X", "X"], "event_date": [e1, e2], "session": ["bmo", "bmo"], "event_session": [e1, e2]})
    rows = []
    for dte in [10, 17, 45, 80, 130]:
        n = int(dte >= 21) + int(dte >= 112)
        w = 0.25**2 * dte / DAYS + n * 0.0025
        rows.append({"date": date_, "symbol": "X", "expiration": date_ + dt.timedelta(days=dte), "dte": dte, "atm_iv": float(np.sqrt(w * DAYS / dte))})
    pillars_df = pl.DataFrame(rows).with_columns(pl.col("dte").cast(pl.Int64))
    spanned = count_events_spanned(pillars_df, events_df).sort("dte")
    assert spanned["n_events"].to_list() == [0, 0, 1, 1, 2]
    adjusted = TermStructureEarningsAdjuster(events_df).adjust(pillars_df.lazy()).collect()
    assert (adjusted["atm_iv"] - 0.25).abs().max() < 1e-9


def test_history_fallback_when_no_pillar_expires_before_the_event():
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(40)]
    events = [dt.date(2024, 1, 25), dt.date(2024, 2, 8), dt.date(2024, 2, 20)]
    events_df = pl.DataFrame({"symbol": ["X"] * 3, "event_date": events, "session": ["bmo"] * 3, "event_session": events})
    pieces = []
    for d in dates:
        nxt = min((e for e in events if e >= d), default=None)
        if nxt is None:
            continue
        edte = (nxt - d).days
        # the first 30 days have a pillar before the event; afterwards only pillars past it
        dtes = [7, 14, 30, 60] if len(pieces) < 30 else [edte + 3, edte + 30]
        pieces.append(make_pillars(d, dtes, 0.30, edte, 0.0049))
    pillars_df = pl.concat(pieces)
    jump_df = estimate_jump_variance(pillars_df, events_df, JumpConfig(history_window=250, history_min=5))
    methods = jump_df.sort("date")["method"].to_list()
    assert "term" in methods and "history" in methods
    hist = jump_df.filter(pl.col("method") == "history")
    assert (hist["jump_var"] - 0.0049).abs().max() < 1e-12


def test_close_to_close_excludes_event_sessions():
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(30)]
    closes = [100.0]
    for i in range(1, 30):
        closes.append(closes[-1] * (1.10 if i == 10 else 1.01))
    closes_df = pl.DataFrame({"date": dates, "symbol": ["X"] * 30, "close": closes})
    cfg = RealizedVolConfig(windows=(1, 5, 22), forward_window=5)
    raw = compute_close_to_close_panel(closes_df.lazy(), cfg).collect().sort("date")
    ex = compute_close_to_close_panel(closes_df.lazy(), cfg, exclude_df=pl.DataFrame({"date": [dates[10]], "symbol": ["X"]})).collect().sort("date")
    steady = np.sqrt(np.log(1.01) ** 2 * 252)
    assert ex["rv_1"][10] is None and raw["rv_1"][10] > steady
    assert abs(ex["rv_5"][12] - steady) < 1e-9 and raw["rv_5"][12] > steady  # window spans the jump
    assert abs(ex["rv_fwd"][7] - steady) < 1e-9 and raw["rv_fwd"][7] > steady
    assert abs(ex["rv_5"][29] - raw["rv_5"][29]) < 1e-9  # window past the jump: identical
