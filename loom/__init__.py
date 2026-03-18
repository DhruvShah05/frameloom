"""Loom from Python: open data in the UI and pull any version back into code.

    import loom

    proj = loom.view(df)                   # open a DataFrame (pandas or Polars) in the UI
    proj = loom.open("sales.csv")          # or a file
    clean = loom.load("sales.csv", "v2")   # a named version, as a Polars DataFrame
    clean = proj.df("v2", pandas=True)     # ... or as pandas
    proj.save_version("v3", message="dropped outliers")
    proj.log_metrics("v3", val_auc=0.83)   # record what this data produced
    proj.versions()                        # compare versions by their metrics

Projects live in LOOM_HOME (default: ./.loom next to the Loom source), the
same place the web app uses, so both always see the same projects.
"""
from .api import (
    LoomProject,
    SourceChangedError,
    load,
    log_metrics,
    open,  # noqa: A004 - mirrors the built-in on purpose: loom.open(path)
    project,
    projects,
    server_url,
    start_server,
    view,
)

__all__ = [
    "LoomProject",
    "SourceChangedError",
    "load",
    "log_metrics",
    "open",
    "project",
    "projects",
    "server_url",
    "start_server",
    "view",
]
