from __future__ import annotations

import builtins
import hashlib
import io
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from typing import Any, Optional, Union

import polars as pl

from backend.core import SourceChangedError  # noqa: F401 - re-exported

_server_lock = threading.Lock()
_server: dict[str, Any] = {}


def _core():
    # Imported lazily so LOOM_HOME can be set before the first call.
    from backend.main import loom as core

    return core


def _in_notebook() -> bool:
    try:
        from IPython import get_ipython  # type: ignore

        shell = get_ipython()
        return shell is not None and shell.__class__.__name__ in ("ZMQInteractiveShell", "Shell")
    except Exception:
        return False


# ------------------------------------------------------------------ server
def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            return r.status == 200
    except OSError:
        return False


def _free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found for the Loom server")


def start_server(port: Optional[int] = None) -> str:
    """Start the Loom web app in a background thread (once) and return its URL.

    Reuses a Loom server that is already running on the port.
    """
    with _server_lock:
        if _server.get("url"):
            return _server["url"]
        port = port or int(os.environ.get("LOOM_PORT", "8765"))
        if not _healthy(port):
            import uvicorn

            from backend.main import app

            port = _free_port(port)
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, name="loom-server", daemon=True)
            thread.start()
            deadline = time.time() + 15
            while not _healthy(port):
                if time.time() > deadline:
                    raise RuntimeError("The Loom server did not start")
                time.sleep(0.05)
            _server["server"] = server
        _server["url"] = f"http://localhost:{port}"
        return _server["url"]


def server_url() -> Optional[str]:
    return _server.get("url")


# ----------------------------------------------------------------- project
class LoomProject:
    """A handle on a Loom project. Cheap to create; always reads fresh state."""

    def __init__(self, project_id: str):
        self.id = project_id

    # -- state
    @property
    def _p(self):
        p = _core().store.get(self.id)
        if p is None:
            raise KeyError(f"Project '{self.id}' no longer exists")
        return p

    @property
    def name(self) -> str:
        return self._p.name or self.id

    @property
    def branches(self) -> list[str]:
        return list(self._p.branches)

    @property
    def current_branch(self) -> str:
        return self._p.head

    @property
    def url(self) -> str:
        return f"{start_server()}/?project={self.id}"

    def source_status(self) -> dict:
        return _core().source_status(self._p)

    # -- data
    def df(self, version: Optional[str] = None, pandas: bool = False, allow_changed_source: bool = False):
        """The data at a version, branch, step id, or (default) the current step."""
        out = _core().dataframe(self._p, version, allow_changed_source=allow_changed_source)
        return out.to_pandas() if pandas else out

    def history(self, branch: Optional[str] = None) -> list[dict]:
        core = _core()
        p = self._p
        head = p.branches[branch].head_node_id if branch else core.head_node_id(p)
        out = []
        for i, n in enumerate(core.path_nodes(p, head)):
            out.append({"step": i + 1, "id": n.id, "label": n.operation.label, "type": n.operation.type,
                        "params": n.operation.params, "note": n.note})
        return out

    def code(self, version: Optional[str] = None, target: str = "polars") -> str:
        from backend.codegen import generate_script

        core = _core()
        p = self._p
        ops = [n.operation for n in core.path_nodes(p, core.resolve(p, version))]
        return generate_script(ops, target, p.source_path, p.source_options, snapshot=p.snapshot_path, sha256=p.source_sha256)

    def export(self, path: str, version: Optional[str] = None, format: Optional[str] = None,
               allow_changed_source: bool = False) -> str:
        """Write a version to a file (format from the extension: csv, parquet, xlsx, json...)."""
        from backend import sources

        fmt = format or {".csv": "csv", ".tsv": "tsv", ".parquet": "parquet", ".pq": "parquet", ".xlsx": "xlsx",
                         ".json": "json", ".ndjson": "ndjson", ".jsonl": "ndjson", ".arrow": "ipc",
                         ".feather": "ipc"}.get(os.path.splitext(path)[1].lower())
        if not fmt:
            raise ValueError("Can't tell the format from the file name; pass format='csv'/'parquet'/...")
        core = _core()
        p = self._p
        status = core.source_status(p)
        if status["status"] == "changed" and not allow_changed_source:
            raise SourceChangedError(status["message"])
        sources.write_export(core.engine.build_plan(p, core.resolve(p, version)), fmt, path)
        return path

    # -- versions
    def save_version(self, name: str, version: Optional[str] = None, message: str = "", **metrics) -> dict:
        """Name the current step (or `version`) so you can load it later."""
        return _core().create_version(self._p, name, version, message, metrics or None).model_dump()

    def log_metrics(self, version: str, **metrics) -> dict:
        """Record model results for a version, e.g. log_metrics("v2", val_auc=0.83)."""
        return _core().update_version(self._p, version, metrics=metrics).model_dump()

    def versions(self, as_frame: bool = True):
        rows = []
        for v in sorted(self._p.versions.values(), key=lambda v: v.created_at):
            rows.append({"version": v.name, "message": v.message, "branch": v.branch, "created_at": v.created_at,
                         **{f"metric:{k}": val for k, val in v.metrics.items()}})
        if not as_frame:
            return rows
        return pl.DataFrame(rows) if rows else pl.DataFrame(schema={"version": pl.String, "message": pl.String})

    # -- display
    def show(self, open_browser: Optional[bool] = None) -> str:
        url = self.url
        if open_browser if open_browser is not None else not _in_notebook():
            webbrowser.open(url)
        return url

    def _repr_html_(self) -> str:
        url = self.url
        return (f'<iframe src="{url}" style="width:100%;height:640px;border:1px solid #e5e2e1;border-radius:6px"></iframe>'
                f'<div style="font:12px sans-serif;color:#504534">Loom project <b>{self.name}</b> · '
                f'<a href="{url}" target="_blank">open in a new tab</a></div>')

    def __repr__(self) -> str:
        try:
            p = self._p
            return (f"<LoomProject {p.name!r} id={self.id[:8]} steps={len(p.nodes) - 1} "
                    f"branches={len(p.branches)} versions={len(p.versions)}>")
        except KeyError:
            return f"<LoomProject {self.id} (deleted)>"


# ------------------------------------------------------------ module API
def project(ref: Union[str, LoomProject]) -> LoomProject:
    """Find a project by id, id prefix or name (most recent wins), or by data file path."""
    if isinstance(ref, LoomProject):
        return ref
    core = _core()
    try:
        return LoomProject(core.store.find(ref).id)
    except KeyError:
        path = os.path.abspath(os.path.expanduser(ref))
        matches = [p for p in core.store.all() if p.source_path == path]
        if not matches:
            raise
        return LoomProject(max(matches, key=lambda p: p.created_at).id)


def projects(as_frame: bool = True):
    rows = [{"name": p.name, "id": p.id, "source": p.source_path, "steps": len(p.nodes) - 1,
             "branches": len(p.branches), "versions": len(p.versions), "pinned": bool(p.snapshot_path),
             "created_at": p.created_at} for p in sorted(_core().store.all(), key=lambda p: p.created_at, reverse=True)]
    if not as_frame:
        return rows
    return pl.DataFrame(rows) if rows else pl.DataFrame(schema={"name": pl.String, "id": pl.String})


def open(path: str, name: Optional[str] = None, snapshot: Optional[bool] = None, **options) -> LoomProject:  # noqa: A001
    """Start a new project from a file or URL. Reader options as keyword args,
    e.g. loom.open("book.xlsx", sheet_name="2024") or loom.open("x.txt", separator="|")."""
    p = _core().create_project(path, options, name=name, snapshot=snapshot)
    return LoomProject(p.id)


def _frame_to_parquet(data, name: Optional[str]) -> tuple[str, str]:
    """Freeze an in-memory DataFrame to LOOM_HOME/inputs/<hash>.parquet."""
    if not isinstance(data, (pl.DataFrame, pl.LazyFrame)):
        try:
            import pandas as pd  # type: ignore
        except ImportError:
            pd = None
        if pd is not None and isinstance(data, pd.DataFrame):
            index_named = any(n is not None for n in data.index.names)
            data = pl.from_pandas(data.reset_index() if index_named else data)
        else:
            raise TypeError("loom.view() takes a pandas or Polars DataFrame, a file path, or a LoomProject")
    if isinstance(data, pl.LazyFrame):
        data = data.collect()
    buf = io.BytesIO()
    data.write_parquet(buf)
    raw = buf.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    folder = os.path.join(_core().store.home, "inputs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{digest[:16]}.parquet")
    if not os.path.exists(path):
        with builtins.open(path, "wb") as f:
            f.write(raw)
    return path, name or f"dataframe-{digest[:8]}"


def view(data, name: Optional[str] = None, open_browser: Optional[bool] = None) -> LoomProject:
    """Open data in the Loom UI and return the project.

    `data` can be a pandas/Polars DataFrame, a file path, a project name/id, or
    a LoomProject. In Jupyter the returned project renders inline.
    """
    if isinstance(data, LoomProject):
        proj = data
    elif isinstance(data, str):
        try:
            proj = project(data)
        except KeyError:
            proj = open(data, name=name)
    else:
        path, default_name = _frame_to_parquet(data, name)
        proj = LoomProject(_core().create_project(path, {}, name=default_name).id)
    if not _in_notebook():
        proj.show(open_browser)
    else:
        start_server()
    return proj


def load(project_ref: Union[str, LoomProject], version: Optional[str] = None, pandas: bool = False,
         allow_changed_source: bool = False):
    """Load a project's data at a version / branch / step (default: current) as a DataFrame."""
    return project(project_ref).df(version, pandas=pandas, allow_changed_source=allow_changed_source)


def log_metrics(project_ref: Union[str, LoomProject], version: str, **metrics) -> dict:
    """Record what a version of the data produced: loom.log_metrics("sales", "v2", val_auc=0.83)."""
    return project(project_ref).log_metrics(version, **metrics)
