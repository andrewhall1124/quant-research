"""Write side of the repo: the pulls that populate `data_store/`.

One module per dataset, named `*_pipeline.py`, run through a single CLI:

    uv run python -m data_pipelines.cli --help
    uv run python -m data_pipelines.cli reference
    uv run python -m data_pipelines.cli option-greeks --year 2024
    uv run python -m data_pipelines.cli build --dry-run

Run from the repo root, so `data_access_layer` resolves.

A pipeline writes to a `data_access_layer.paths` constant and reads *only*
through `data_access_layer` — no module here opens a path under `data_store/`
for reading. `universe_pipeline` is the one exception it cannot avoid: it
writes the membership table the rest of them read.

Shared machinery — the ThetaData session, the ticker spellings, the exchange
calendar, the Yahoo download, atomic writes — lives in `utils/`, never in a
pipeline that another pipeline then imports.
"""
