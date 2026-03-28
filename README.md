# Loom — Git for Dataframes

Explore and clean a dataset in the browser instead of writing throwaway pandas
code, with every change recorded as a step you can branch, edit, version and
export as Python.

- **Look at the data**: a grid that pages through millions of rows, per-column
  profiles with data-quality warnings (and one-click fixes), and charts.
- **Change it**: 35 point-and-click steps, from filters and joins to one-hot
  encoding, scaling and train/test splits, plus a raw SQL step.
- **Version it**: branches, editable history, named versions with model
  metrics, and snapshots so a version always means the same rows.
- **Use it from code**: `loom.load("sales", "v2")` in a notebook, or export the
  pipeline as a Polars or pandas script.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
loom build-frontend          # needs Node.js; only once
```

## Quick start

```bash
loom data.csv                # opens the app in your browser
```

Or from a notebook:

```python
import loom

proj = loom.view(df)                    # a pandas/Polars DataFrame or a file path; renders inline in Jupyter
# ... clean it up in the UI, then "Save Version" as v1 ...
train = loom.load(proj, "v1", pandas=True)
loom.log_metrics(proj, "v1", val_auc=0.83)
proj.versions()                         # every version with its metrics, to find the best data
```

## Version control for datasets

| Idea | What it means in Loom |
|---|---|
| Step | One recorded change (a filter, an encoding, a cell edit). Data is never copied; steps are replayed from the source. |
| Branch | A line of steps. Applying a step to an older point starts a branch, so you can try alternatives in parallel. |
| Edit history | Change, remove or insert a step anywhere on a branch; later steps replay on top. If a later step can't replay (e.g. it uses a column you just dropped), nothing changes and you're told which step failed. |
| Copy / merge | Copy a step to another branch, or merge a branch: its new steps replay on top. Steps already copied are skipped. |
| Undo / redo | Undo reverts the last change to a branch, including edits and merges (⌘/Ctrl+Z). |
| Version | A fixed name for a step (`baseline`, `v2-no-outliers`) with a message, notes and metrics. Versions never move. |
| Snapshot | On open, Loom keeps a read-only copy of the file (content-addressed by SHA-256, up to `LOOM_SNAPSHOT_MAX_MB`, default 2048). Versions replay from it, so they never change when the file does. Without a snapshot, Loom notices when the file changes and asks before using the new data. |
| Cell edits | Remember the row's values, not its position, so they follow the row when earlier steps change. |

Steps that learn from the data (one-hot categories, scaler means, clip limits)
learn once and store what they learned, like a fitted scikit-learn
transformer. Recipes and exported code reuse the same values. Use
**"learn only from rows where split = train"** to keep test rows from leaking.

Work on a **sample** (status bar) for speed; exports, `loom.load()`,
recipes and learned settings always use every row.

## Python API

```python
loom.open("sales.xlsx", sheet_name="2024")   # new project from a file (reader options as keywords)
loom.project("sales")                        # by name, id prefix, or data file path
loom.projects()                              # table of projects

proj.df("v2")                                # Polars DataFrame at a version / branch / step id
proj.df("v2", pandas=True)
proj.save_version("v3", message="dropped outliers", val_auc=0.84)
proj.log_metrics("v3", f1=0.71)
proj.versions()                              # versions + metric:* columns
proj.history()                               # steps on the current branch
proj.code("v3", target="pandas")             # a script that rebuilds v3
proj.export("v3.parquet", "v3")
proj.url                                     # open in the browser
```

`loom.load()` refuses (raises `loom.SourceChangedError`) when an unpinned
source file changed, unless you pass `allow_changed_source=True`.

## Command line

```bash
loom data.csv [--sheet S] [--table T] [--query Q] [--separator ;] [--port 8000]
loom list                                  # projects, branches, versions
loom versions sales                        # versions and their metrics
loom export sales -v v2 -o train.parquet   # write a version to a file
loom code sales -v v2 --target pandas      # print the pipeline as Python
loom run my-recipe new-month.csv -o clean.parquet   # apply a recipe, no UI
loom recipes
```

## Formats

Read: CSV/TSV (separator, encoding, header, skip rows), Parquet, Arrow/Feather,
JSON, NDJSON, Excel (pick a sheet), SQLite and DuckDB (table or query), and
http/s3/gs URLs. Export: CSV, TSV, Parquet, Excel, JSON, NDJSON, Arrow.

## Where things live

`LOOM_HOME` (default `./.loom`) holds `projects/`, `recipes/`, `snapshots/`
and `inputs/` (DataFrames opened with `loom.view`). The web app and the
Python API share it. The server only accepts requests from localhost.

## Development

```bash
pytest                         # backend, Python API and CLI tests
cd frontend && npm run dev     # UI with hot reload, proxied to a running `loom` server on :8000
```

The tests check that, for every step type, the exported Polars **and**
pandas scripts produce exactly the same table as the app.
