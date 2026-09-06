"""The one entry point for running a pipeline.

    uv run python -m data_pipelines.cli --help
    uv run python -m data_pipelines.cli universe
    uv run python -m data_pipelines.cli option-greeks --year 2024 --workers 4
    uv run python -m data_pipelines.cli build --dry-run

Each `*_pipeline.py` owns its own flags through `add_arguments`; this file only
wires them into subcommands and adds `build`, which runs every step in the
right order.

**One step at a time, always.** ThetaData issues one session per account, so
two pipeline processes fight over it and the loser gets UNAUTHENTICATED on
every request. `build` runs steps sequentially and never in parallel;
concurrency belongs in each step's `--workers`, which shares one session across
threads.
"""

import argparse
import subprocess
import sys
import time
from datetime import date

from data_access_layer import paths
from data_pipelines import (
    corporate_actions_pipeline,
    earnings_pipeline,
    index_repair_pipeline,
    open_interest_pipeline,
    option_greeks_pipeline,
    reference_pipeline,
    symbology_pipeline,
    underlying_pipeline,
    universe_pipeline,
)

# Subcommand name -> the module that implements it. The order is the order they
# appear in `--help`, which is roughly dependency order.
PIPELINES = {
    "universe": universe_pipeline,
    "reference": reference_pipeline,
    "corporate-actions": corporate_actions_pipeline,
    "earnings": earnings_pipeline,
    "underlying": underlying_pipeline,
    "option-greeks": option_greeks_pipeline,
    "open-interest": open_interest_pipeline,
    "index-repair": index_repair_pipeline,
    "symbology": symbology_pipeline,
}

# The option store spans these years. 2016 is deliberately absent: ThetaData's
# greeks coverage effectively starts in 2017, and a 2016 pull returns only 122
# of 533 names — see data_store/README.md.
OPTION_YEARS = list(range(2017, 2026))
INDEX_ROOTS = "SPX,SPXW,XSP,VIX"

# Splits and dividends are needed as far back as the option data reaches.
ACTIONS_START = date(2017, 1, 1)


def build_plan() -> list[tuple[str, list[str]]]:
    """Every command needed to rebuild the store, in dependency order.

    The pipelines each default to the 2025 slice, so running them bare
    reproduces about a fifth of the store: every backfill year needs `--year`,
    the index roots need `--symbols` *and* `--output-dir`, and the pre-sample
    stock history and the multi-year universe each need a flag of their own.
    That list lived only in this repo's shell history, which is not a place a
    dataset should live.

    Every per-symbol step is resumable — a symbol already on disk is skipped —
    so re-running after an interruption costs only what was in flight, and
    running the whole thing against a full store is a cheap no-op that verifies
    coverage.

    Two things this cannot promise. `universe_pipeline` walks backwards from
    *today's* Wikipedia constituent list, and `corporate_actions_pipeline`
    always runs to `date.today()` against a Yahoo series back-adjusted to the
    present, so both produce slightly different answers as time passes. The
    option data itself is fixed; the reference tables around it drift.
    """
    steps: list[tuple[str, list[str]]] = []

    # Reference first: the universe is what every per-symbol pull iterates.
    steps.append(("universe (2025)", ["universe"]))
    steps.append(("universe history (2016-2024)", ["universe", "--history"]))
    steps.append(("indices, VIX complex, yield curve", ["reference"]))
    steps.append((
        "corporate actions + splits",
        ["corporate-actions", "--start", ACTIONS_START.isoformat()],
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
            ["option-greeks", "--year", str(year), "--workers", "4"],
        ))
        steps.append((
            f"open interest {year}",
            ["open-interest", "--year", str(year), "--workers", "4"],
        ))
        # --output-dir must be explicit and must beat --year, or the index
        # roots land in the constituent directory that research code globs.
        steps.append((
            f"index roots {year}",
            [
                "option-greeks", "--year", str(year),
                "--symbols", INDEX_ROOTS,
                "--output-dir", str(paths.option_dir("index_greeks", year)),
                "--workers", "4",
            ],
        ))

    # The EOD endpoint leaves index sessions unquoted, mostly 2020-2021;
    # splice in the 15:59 intraday bar. Reads the index files it repairs.
    steps.append(("index quote repair (all years)", ["index-repair"]))

    # Last, because it reads the chains rather than pulling any: which
    # symbol-years are the company the universe names.
    steps.append((
        "symbology check (all years)",
        ["symbology", "--years", *[str(y) for y in OPTION_YEARS]],
    ))
    return steps


def run_build(dry_run: bool, only: str | None, skip_to: str | None) -> int:
    steps = build_plan()
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
            print(f"  {label:<{width}}  uv run python -m data_pipelines.cli {' '.join(argv)}")
        return 0

    started = time.perf_counter()
    for number, (label, argv) in enumerate(steps, start=1):
        command = [sys.executable, "-m", "data_pipelines.cli", *argv]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data_pipelines.cli",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="pipeline", required=True)

    for name, module in PIPELINES.items():
        summary = module.__doc__.splitlines()[0]
        subparser = subparsers.add_parser(
            name,
            help=summary,
            description=module.__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        module.add_arguments(subparser)
        subparser.set_defaults(main=module.main)

    rebuild = subparsers.add_parser(
        "build",
        help="rebuild the whole store, one step at a time",
        description=build_plan.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rebuild.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    rebuild.add_argument("--only", default=None, help="run only steps whose label contains this")
    rebuild.add_argument("--skip-to", default=None, help="resume from the first step matching this")
    rebuild.set_defaults(
        main=lambda args: sys.exit(run_build(args.dry_run, args.only, args.skip_to))
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.main(args)


if __name__ == "__main__":
    main()
