"""Rebuild `data_store/` from nothing, in the right order, with the right flags.

The pipelines each default to the 2025 slice, so running them bare reproduces
about a fifth of the store: every backfill year needs `--year`, the index roots
need `--symbols` *and* `--output-dir`, and the pre-sample stock history and the
multi-year universe each need a flag of their own. That list lived only in this
repo's shell history, which is not a place a dataset should live.

    uv run python -m data_pipelines.build --dry-run    # print the plan
    uv run python -m data_pipelines.build              # run it

**One step at a time, always.** ThetaData issues one session per account, so two
pipeline processes fight over it and the loser gets UNAUTHENTICATED on every
request. This runs steps sequentially and never in parallel; concurrency belongs
in each step's `--workers`, which shares one session across threads.

Every per-symbol step is resumable — a symbol already on disk is skipped — so
re-running after an interruption costs only what was in flight, and running the
whole thing against a full store is a cheap no-op that verifies coverage.

Two things this cannot promise. `universe.py` walks backwards from *today's*
Wikipedia constituent list, and `corporate_actions.py` always runs to
`date.today()` against a Yahoo series back-adjusted to the present, so both
produce slightly different answers as time passes. The option data itself is
fixed; the reference tables around it drift.
"""

import argparse
import subprocess
import sys
import time
from datetime import date

# The option store spans these years. 2016 is deliberately absent: ThetaData's
# greeks coverage effectively starts in 2017, and a 2016 pull returns only 122
# of 533 names — see data_store/README.md.
OPTION_YEARS = list(range(2017, 2026))
SAMPLE_YEAR = 2025
INDEX_ROOTS = "SPX,SPXW,XSP,VIX"

# Splits and dividends are needed as far back as the option data reaches.
ACTIONS_START = date(2017, 1, 1)


def option_dir(dataset: str, year: int) -> str:
    """Mirrors paths.option_dir: 2025 keeps the bare names it was built with."""
    if year == SAMPLE_YEAR and dataset in ("option_greeks", "open_interest"):
        return f"data_store/{dataset}"
    return f"data_store/{dataset}_{year}"


def plan() -> list[tuple[str, list[str]]]:
    """Every command needed to rebuild the store, in dependency order."""
    steps: list[tuple[str, list[str]]] = []

    # Reference first: the universe is what every per-symbol pull iterates.
    steps.append(("universe (2025)", ["universe"]))
    steps.append(("universe history (2017-2024)", ["universe", "--history"]))
    steps.append(("indices, VIX complex, yield curve", ["reference"]))
    steps.append((
        "corporate actions + splits",
        ["corporate_actions", "--start", ACTIONS_START.isoformat()],
    ))
    steps.append(("earnings dates", ["earnings"]))

    # Stock prices: the 2025 sample, then the pre-sample burn-in window.
    steps.append(("underlying (2025)", ["underlying"]))
    steps.append(("underlying history (2023-06 to 2024-12)", ["underlying", "--history"]))

    # The expensive part. Newest first, so an interrupted rebuild leaves the
    # most useful years on disk.
    for year in sorted(OPTION_YEARS, reverse=True):
        steps.append((
            f"option chains + greeks {year}",
            ["option_greeks", "--year", str(year), "--workers", "4"],
        ))
        steps.append((
            f"open interest {year}",
            ["open_interest", "--year", str(year), "--workers", "4"],
        ))
        # --output-dir must be explicit and must beat --year, or the index
        # roots land in the constituent directory that research code globs.
        steps.append((
            f"index roots {year}",
            [
                "option_greeks", "--year", str(year),
                "--symbols", INDEX_ROOTS,
                "--output-dir", option_dir("index_greeks", year),
                "--workers", "4",
            ],
        ))

    # Last, because it reads the chains rather than pulling any: which
    # symbol-years are the company the universe names.
    steps.append((
        "symbology check (all years)",
        ["symbology", "--years", *[str(y) for y in OPTION_YEARS]],
    ))
    return steps


def run(dry_run: bool, only: str | None, skip_to: str | None) -> int:
    steps = plan()
    if skip_to is not None:
        matches = [i for i, (label, _) in enumerate(steps) if skip_to in label]
        if not matches:
            print(f"no step matching {skip_to!r}")
            return 1
        steps = steps[matches[0]:]
    if only is not None:
        steps = [step for step in steps if only in step[0]]
        if not steps:
            print(f"no step matching {only!r}")
            return 1

    width = max(len(label) for label, _ in steps)
    print(f"{len(steps)} steps\n")
    if dry_run:
        for label, argv in steps:
            print(f"  {label:<{width}}  uv run python -m data_pipelines.{' '.join(argv)}")
        return 0

    started = time.perf_counter()
    for number, (label, argv) in enumerate(steps, start=1):
        command = [sys.executable, "-m", f"data_pipelines.{argv[0]}", *argv[1:]]
        print(f"\n=== [{number}/{len(steps)}] {label} ===", flush=True)
        step_started = time.perf_counter()
        result = subprocess.run(command)
        elapsed = time.perf_counter() - step_started
        if result.returncode != 0:
            print(
                f"\n{label} exited {result.returncode} after {elapsed / 60:.1f} min."
                f"\nEvery per-symbol step is resumable — fix the cause and re-run with"
                f"\n  --skip-to {label.split()[0]!r}",
            )
            return result.returncode
        print(f"--- {label}: {elapsed / 60:.1f} min", flush=True)

    print(f"\nstore rebuilt in {(time.perf_counter() - started) / 3600:.1f} hr")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    parser.add_argument("--only", default=None, help="run only steps whose label contains this")
    parser.add_argument("--skip-to", default=None, help="resume from the first step matching this")
    args = parser.parse_args()
    raise SystemExit(run(args.dry_run, args.only, args.skip_to))


if __name__ == "__main__":
    main()
