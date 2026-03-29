# Changelog

## 0.1.0

First public release.

- Open CSV/TSV, Parquet, Arrow, JSON/NDJSON, Excel, SQLite/DuckDB and http/s3/gs sources.
- Grid with server-side paging, sorting and filtering; inline cell edits recorded as steps.
- Column profiles with data-quality warnings and one-click fixes; histogram, bar, line and scatter charts.
- 35 transformation steps, including one-hot/label encoding, scaling, outlier clipping,
  train/validation/test splits and a SQL step.
- Version control: branches, editable history (edit/insert/remove a step and replay),
  cherry-pick, merge, undo/redo, named versions with notes and model metrics.
- Reproducible versions: sources are snapshotted; changed sources are detected.
- Python API (`frameloom.view/open/load/log_metrics`) and CLI
  (`frameloom FILE | list | versions | export | code | run | gc`).
- Export any version as a Polars or pandas script, or as CSV/Parquet/Excel/JSON/Arrow.
- Sample mode for fast interactive work on large files.
