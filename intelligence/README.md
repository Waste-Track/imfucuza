# Intelligence

Waste verification classifier. The backend calls it over HTTP: see the verification boundary in [`../spec`](../spec/README.md).

## What CI expects

- A `pyproject.toml` (with `uv.lock` for reproducible installs) or a `requirements.txt` in this folder.
- Tests under `tests/`, run with `pytest`. Code must pass `ruff check`.
- No model weights or datasets in Git. Store them outside the repo and download them at build time.

Until one of those files exists, CI skips this folder.
