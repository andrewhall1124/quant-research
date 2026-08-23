"""Pull EOD chains for the cash-settled index options used in the rate study.

SPX and SPXW are European and cash-settled, which is what makes a box spread a
clean read on the risk-free rate: no early exercise, and the index level (and
therefore the dividend stream) cancels out of the box algebra entirely.
"""

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
from common import make_client, with_retries

SYMBOLS = ["SPX", "SPXW", "XSP"]
OUTPUT_DIR = Path("data/index_options_2025")


def fetch(symbol: str) -> None:
    client = make_client()
    output_path = OUTPUT_DIR / f"{symbol}.parquet"
    if output_path.exists():
        print(f"  {symbol}: already on disk")
        return
    started = time.perf_counter()
    chain_df = client.option_history_eod(date(2025, 1, 1), date(2025, 12, 31), symbol, "*")
    chain_df.write_parquet(output_path)
    print(
        f"  {symbol}: {chain_df.height:,} rows in {time.perf_counter() - started:.1f}s"
        f" ({output_path.stat().st_size / 1e6:.0f} MB)"
    )


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        with_retries(fetch, symbol)
