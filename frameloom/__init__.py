"""FrameLoom: explore, clean and version dataframes - and pull any version back into code.

    import frameloom as fl

    proj = fl.view(df)                     # open a DataFrame (pandas or Polars) in the UI
    proj = fl.open("sales.csv")            # or a file
    clean = fl.load("sales.csv", "v2")     # a named version, as a Polars DataFrame
    clean = proj.df("v2", pandas=True)     # ... or as pandas
    proj.save_version("v3", message="dropped outliers")
    proj.log_metrics("v3", val_auc=0.83)   # record what this data produced
    proj.versions()                        # compare versions by their metrics

Projects live in FRAMELOOM_HOME (default ~/.frameloom), the same place the web
app uses, so both always see the same projects.
"""
from .api import (
    LoomProject,
    SourceChangedError,
    gc,
    load,
    log_metrics,
    open,  # noqa: A004 - mirrors the built-in on purpose: frameloom.open(path)
    project,
    projects,
    server_url,
    start_server,
    view,
)

__version__ = "0.1.0"

# A friendlier name for the project handle.
Project = LoomProject

__all__ = [
    "LoomProject",
    "Project",
    "SourceChangedError",
    "__version__",
    "gc",
    "load",
    "log_metrics",
    "open",
    "project",
    "projects",
    "server_url",
    "start_server",
    "view",
]
