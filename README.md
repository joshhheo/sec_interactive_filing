# edgarS

## Project goal

edgarS is a workspace for investigating SEC filings in place. The trial opens Apple and Microsoft’s FY2025 annual reports and third-quarter reports in their original filing layouts. Other prepared filings remain available to the comparison logic.

## Try the workspace

From the project root, with the virtual environment and configured PostgreSQL database available:

```sh
PYTHONPATH=src .venv/bin/python -m filing_reader.server
```

Open [the local workspace](http://127.0.0.1:8765/). If the database has no prepared reports, run `PYTHONPATH=src .venv/bin/python -m filing_reader.prepare` first. Filing questions and generated explanations use local Ollama with `qwen3.5:4b` by default.

- **Search** finds reports by company, form, and filing date. Use the filing’s own table of contents to jump to sections.
- **Inspect** opens beside a clicked tagged figure or highlighted text. Figures show the period, comparable amount, linked notes, and corrections where available. Selected text can be explained or attached to a follow-up.
- **Ask this filing** searches the active filing for passages and tagged facts. Citations jump to the evidence in the document. Answers can be exported with their evidence as JSON for another tool or agent.

## Potential future features

- **Compare filings.** Open reports from different periods side by side and compare sections or filing versions.
- **My Research.** Save figures, passages, and cited answers in a workspace for revisiting evidence.

## Planned improvements

- **Increase comparison coverage.** Find more comparable figures in prior statements and notes while requiring a match on the measure, period, unit, and reporting scope.
- **Improve amendment attribution.** Identify which sections an amendment revises and verify more figure-level corrections using structured filing data. Keep the filing-level link when a correction can’t be verified for a specific figure.
- **Expand context sources.** Follow references to exhibits, credit agreements, and other SEC filings that bear on the selected figure. Keep each detail traceable to its source.

## Developer context

See the [pipeline walkthrough](docs/pipeline-walkthrough.md) for how preparation, storage, and the viewer fit together.
