# Interactive filing reader

The prototype supports Apple and Microsoft, fiscal 2024 and 2025, using their original 10-K filings. The earlier financial-comparison project remains in the separate `../sp500_finance/` folder.

## Run locally

Use the existing virtual environment and `.env`. The preparation command needs `SEC_user_agent` and a PostgreSQL `db_connection_string`. Model explanations use `SEC_OLLAMA_MODEL` and `SEC_OLLAMA_NUM_CTX` from the same environment.

```sh
PYTHONPATH=src .venv/bin/python -m filing_reader.prepare
PYTHONPATH=src .venv/bin/python -m filing_reader.server
```

Open http://127.0.0.1:8765. Preparation reuses downloaded files on later runs. `--files-only` prepares documents without writing to PostgreSQL.

Select a company/year, then click an underlined number or search for an item. The panel shows the printed amount, period, units, sourced taxonomy/company definition when available, and candidate disclosure excerpts. “Go to passage” jumps within the filing. SEC links open the original source; where the original has no anchor, the link opens the document.

“Explain connections” explicitly calls the local Ollama model. No model runs during preparation or ordinary clicks. Successful explanations persist in PostgreSQL and are reused across reloads. Simultaneous requests for the same item share one generation. Cache keys include the filing revision, model name, and explanation version.

## Code flow

- `src/filing_reader/prepare.py`: EdgarTools downloads documents; lxml prepares clickable HTML, facts, definitions, and passage excerpts.
- `src/filing_reader/storage.py`: dedicated `reader_filings` and `reader_explanations` tables.
- `src/filing_reader/server.py`: local HTTP API, passage search, and optional model explanation using `model.py`, the local Ollama client retained from the earlier project.
- `src/filing_reader/static/`: browser interface and filing click handling.

Downloaded originals and prepared HTML live in `data/filing_reader/`, excluded from Git. PostgreSQL contains fact/passage metadata and generated explanations. Keep both for a working reader. To rebuild a filing after changing preparation code, remove only that filing's generated folder and rerun preparation; restart the server after updating prepared data. Increment `VERSION` when changing preparation behavior and the explanation version when changing its prompt.

## Prototype limits

Only visible inline numeric facts are selectable. Search uses concept names. Disclosure retrieval is keyword ranking, not a verified accounting relationship or exhaustive disclosure search. Model explanations cite supplied excerpts, but valid citations do not prove that the explanation is correct. The model receives up to three excerpts, each limited to 900 characters to fit the local context budget. Full passages remain accessible in the interface.

Missing definitions are shown as missing rather than invented. Standard definitions may be technical. Original filing layouts can be wide on small screens. This is a local development server, not a public deployment.

The initial verification used the four real filings and replayed model responses; it did not run real model inference or establish disclosure-matching accuracy.
