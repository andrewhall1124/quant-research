"""Trading calendar.

atium's `CalendarProvider.get(start, end) -> list[date]` is kept verbatim;
`TradingCalendar` adds the session arithmetic a daily option loop needs
(next / previous session, shift by n sessions) that an equity backtester
never asks for.
"""

from __future__ import annotations

import bisect
import datetime as dt
from abc import ABC, abstractmethod

import polars as pl

from voltium.loaders import DataPaths


class CalendarProvider(ABC):
    @abstractmethod
    def get(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Trading sessions in the inclusive window."""


class TradingCalendar(CalendarProvider):
    def __init__(self, sessions: list[dt.date]) -> None:
        self.sessions = sorted(set(sessions))
        if not self.sessions:
            raise ValueError("empty calendar")

    @classmethod
    def from_data(cls, paths: DataPaths) -> "TradingCalendar":
        """Sessions are the dates the universe table is stamped on (2017-)."""
        dates = (
            pl.scan_parquet(paths.universe_files)
            .select(pl.col("date").cast(pl.Date))
            .unique()
            .collect()["date"]
            .sort()
            .to_list()
        )
        return cls(dates)

    def get(self, start: dt.date, end: dt.date) -> list[dt.date]:
        lo = bisect.bisect_left(self.sessions, start)
        hi = bisect.bisect_right(self.sessions, end)
        return self.sessions[lo:hi]

    def is_session(self, date_: dt.date) -> bool:
        i = bisect.bisect_left(self.sessions, date_)
        return i < len(self.sessions) and self.sessions[i] == date_

    def shift(self, date_: dt.date, n: int) -> dt.date:
        """The session `n` steps from `date_` (n may be negative).

        If `date_` is not itself a session, step 0 is the next session on or
        after it.
        """
        i = bisect.bisect_left(self.sessions, date_)
        j = i + n
        if j < 0 or j >= len(self.sessions):
            raise IndexError(f"{date_} shifted by {n} leaves the calendar")
        return self.sessions[j]

    def next(self, date_: dt.date) -> dt.date:
        return self.shift(date_, 1) if self.is_session(date_) else self.shift(date_, 0)

    def previous(self, date_: dt.date) -> dt.date:
        i = bisect.bisect_left(self.sessions, date_)
        if i == 0:
            raise IndexError(f"no session before {date_}")
        return self.sessions[i - 1]

    def sessions_between(self, start: dt.date, end: dt.date) -> int:
        """Number of sessions strictly after `start` up to and including `end`."""
        return len(self.get(start, end)) - (1 if self.is_session(start) else 0)
