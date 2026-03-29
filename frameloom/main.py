import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

import duckdb
import polars as pl
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import ops, sources
from .codegen import generate_script
from .core import HistoryError, Loom, SourceChangedError, file_digest, new_id, schema_hash
from .engine import ExecutionEngine
from .models import Branch, Node, Operation, Project, Recipe
from .store import Store


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_persisted_state()
    yield


app = FastAPI(title="FrameLoom", lifespan=lifespan)


@app.exception_handler(pl.exceptions.PolarsError)
async def polars_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(duckdb.Error)
async def duckdb_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ValueError)
@app.exception_handler(FileNotFoundError)
@app.exception_handler(SourceChangedError)
async def user_error_handler(request: Request, exc: Exception):
    # Bad input (unknown column, bad value, unsupported format...): a 400, not a crash.
    return JSONResponse(status_code=409 if isinstance(exc, SourceChangedError) else 400, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def not_found_handler(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc.args[0]) if exc.args else "Not found"})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# The server reads any file path it is given, so only the local UI may call it.
LOCAL_ORIGIN_RE = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver")


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0]


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Blocks DNS-rebinding and drive-by requests from other websites.
    host = _hostname(request.headers.get("host") or "")
    if host and host not in _LOCAL_HOSTS and os.environ.get("LOOM_ALLOW_REMOTE") != "1":
        return JSONResponse(status_code=403, content={"detail": "Loom only accepts requests addressed to localhost"})
    origin = request.headers.get("origin")
    if origin and not re.match(LOCAL_ORIGIN_RE, origin) and os.environ.get("LOOM_ALLOW_REMOTE") != "1":
        return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not allowed"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_RE,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

store = Store()
engine = ExecutionEngine()
loom = Loom(store, engine)

# Kept for tests and backwards compatibility.
projects: dict[str, Project] = store.projects
recipes_db: dict[str, Recipe] = store.recipes
LOOM_DIR = store.home
PROJECTS_DIR = store.projects_dir
RECIPES_DIR = store.recipes_dir


def _load_persisted_state():
    store.all()
    store.load_recipes()


def _save_project(project: Project):
    store.save(project)


def _get_project(project_id: str) -> Project:
    project = store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _require_node(project: Project, node_id: str) -> Node:
    node = project.nodes.get(node_id)
    if not node:
        raise HTTPException(status_code=400, detail="Node not found")
    return node


def _path_ops(project: Project, node_id: str) -> list[Operation]:
    _require_node(project, node_id)
    return [n.operation for n in loom.path_nodes(project, node_id)]


def _root_id(project: Project) -> str:
    return loom.root_id(project)


# --------------------------------------------------------------------- models
class LoadRequest(BaseModel):
    path: str
    options: dict[str, Any] = {}
    name: Optional[str] = None
    snapshot: Optional[bool] = None


class InspectRequest(BaseModel):
    path: str
    options: dict[str, Any] = {}


class OpRequest(BaseModel):
    parent_node_id: str
    operation: dict[str, Any]


class CheckoutRequest(BaseModel):
    target: str


class BranchRequest(BaseModel):
    name: str
    from_node_id: str


class SaveRecipeRequest(BaseModel):
    name: str
    node_id: str


class ApplyRecipeRequest(BaseModel):
    recipe_id: str
    new_source_path: str
    options: dict[str, Any] = {}
    force: bool = False


class AuditRequest(BaseModel):
    node_a: str
    node_b: str
    key_column: str


class ViewRequest(BaseModel):
    node_id: str
    offset: int = 0
    limit: int = 100
    sort: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []


class ChartRequest(BaseModel):
    node_id: str
    type: Literal["histogram", "bar", "line", "scatter"]
    x: str
    y: Optional[str] = None
    agg: Optional[str] = None
    bins: Optional[int] = None
    limit: Optional[int] = None
    sort: Optional[str] = None


class ExportRequest(BaseModel):
    node_id: str
    target: Literal["polars", "pandas"] = "polars"


class ExportDataRequest(BaseModel):
    node_id: str
    format: Literal["csv", "tsv", "parquet", "xlsx", "json", "ndjson", "ipc"] = "csv"


class AcceptSourceRequest(BaseModel):
    snapshot: bool = False


class SampleRequest(BaseModel):
    rows: Optional[int] = None  # None or 0 = work on all rows
    method: Literal["head", "random"] = "head"
    seed: int = 0


class EditStepRequest(BaseModel):
    params: dict[str, Any]
    branch: Optional[str] = None


class InsertStepRequest(BaseModel):
    operation: dict[str, Any]
    branch: Optional[str] = None


class BranchOnlyRequest(BaseModel):
    branch: Optional[str] = None


class NoteRequest(BaseModel):
    note: str = ""


class CherryPickRequest(BaseModel):
    node_ids: list[str]
    branch: Optional[str] = None


class MergeRequest(BaseModel):
    source: str
    into: Optional[str] = None


class VersionRequest(BaseModel):
    name: str
    node_id: Optional[str] = None
    message: str = ""
    notes: str = ""
    metrics: dict[str, Any] = {}


class VersionUpdateRequest(BaseModel):
    message: Optional[str] = None
    notes: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None
    replace_metrics: bool = False


# ------------------------------------------------------------------- general
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/formats")
def formats():
    return {
        "read": sources.SUPPORTED_FORMATS,
        "export": list(sources.EXPORT_FORMATS),
        "operations": ops.catalogue(),
        "aggregations": list(ops.AGGREGATIONS),
        "cast_types": list(ops.CAST_TYPES),
        "filter_operators": list(ops.FILTER_OPERATORS),
        "window_functions": list(ops.WINDOW_FUNCS),
    }


@app.post("/source/inspect")
def inspect_source(req: InspectRequest):
    """Tell the UI what a path is before loading it (format, sheets, tables)."""
    path = os.path.expanduser(req.path.strip())
    if not sources.source_exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    fmt = sources.infer_format(path, req.options)
    tables = sources.list_tables(path, fmt) if not sources.is_remote(path) else []
    size = os.path.getsize(path) if not sources.is_remote(path) else None
    return {"path": path, "format": fmt, "tables": tables, "needs_table": fmt in ("sqlite", "duckdb"),
            "size_bytes": size, "snapshot_by_default": loom._should_snapshot(path, req.options, None)}


# ------------------------------------------------------------------- projects
def _create_project(path: str, options: dict[str, Any], name: Optional[str] = None,
                    snapshot: Optional[bool] = None, save: bool = True) -> Project:
    path = os.path.expanduser(path.strip())
    if not sources.source_exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return loom.create_project(path, options, name=name, snapshot=snapshot, save=save)


@app.post("/project/load")
def load_project(req: LoadRequest):
    return _create_project(req.path, req.options, req.name, req.snapshot)


@app.get("/projects")
def list_projects():
    out = []
    for p in store.all():
        out.append({
            "id": p.id,
            "name": p.name or os.path.basename(p.source_path),
            "source_path": p.source_path,
            "created_at": p.created_at,
            "steps": len(loom.path_nodes(p, loom.head_node_id(p))) - 1,
            "branches": len(p.branches),
            "versions": len(p.versions),
            "pinned": bool(p.snapshot_path),
            "source_exists": bool(p.snapshot_path and os.path.exists(p.snapshot_path)) or sources.source_exists(p.source_path),
        })
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


@app.delete("/project/{id}")
def delete_project(id: str):
    _get_project(id)
    engine.invalidate_project(id)
    store.delete(id)
    return {"status": "deleted"}


@app.get("/project/{id}/tree")
def get_tree(id: str):
    return _get_project(id)


@app.get("/project/{id}/source")
def source_status(id: str):
    return loom.source_status(_get_project(id))


@app.post("/project/{id}/source/accept")
def accept_source(id: str, req: AcceptSourceRequest):
    """Make the file as it is now the new baseline, optionally pinning a snapshot of it."""
    return loom.accept_source(_get_project(id), snapshot=req.snapshot)


@app.post("/project/{id}/sample")
def set_sample(id: str, req: SampleRequest):
    """Make the grid, profile, charts, compare and audit work on a sample.

    Exports, loom.load(), recipes and code always use all rows, and steps
    that learn settings (encoders, scalers) learn from all rows.
    """
    project = _get_project(id)
    if req.rows is not None and req.rows < 0:
        raise HTTPException(400, "Sample size must be positive")
    project.sample = {"rows": req.rows, "method": req.method, "seed": req.seed} if req.rows else None
    _save_project(project)
    return {"sample": project.sample}


@app.post("/project/{id}/op")
def add_op(id: str, req: OpRequest):
    project = _get_project(id)
    _require_node(project, req.parent_node_id)
    return loom.add_step(project, req.parent_node_id, req.operation)


@app.get("/project/{id}/data")
def get_data(id: str, node_id: str, offset: int = 0, limit: int = 100):
    project = _get_project(id)
    _require_node(project, node_id)
    view = engine.get_view(project, node_id, offset=max(offset, 0), limit=min(max(limit, 1), 10000), sampled=True)
    for row in view["data"]:
        row.pop("__row_nr", None)
    return view


@app.post("/project/{id}/view")
def get_view(id: str, req: ViewRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    return engine.get_view(project, req.node_id, offset=max(req.offset, 0), limit=min(max(req.limit, 1), 10000),
                           sort=req.sort, filters=req.filters, sampled=True)


@app.get("/project/{id}/profile")
def get_profile(id: str, node_id: str, bins: int = 20):
    project = _get_project(id)
    _require_node(project, node_id)
    return engine.profile(project, node_id, bins=min(max(bins, 2), 100), sampled=True)


@app.post("/project/{id}/chart")
def get_chart(id: str, req: ChartRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    return engine.chart(project, req.node_id, req.model_dump(exclude={"node_id"}), sampled=True)


@app.post("/project/{id}/export/code")
def export_code(id: str, req: ExportRequest):
    project = _get_project(id)
    code = generate_script(_path_ops(project, req.node_id), req.target, project.source_path, project.source_options,
                           snapshot=project.snapshot_path, sha256=project.source_sha256,
                           root_schema=engine.get_schema(project, _root_id(project)))
    return {"code": code}


@app.post("/project/{id}/export/data")
def export_data(id: str, req: ExportDataRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    lf = engine.build_plan(project, req.node_id)
    media_type, ext = sources.EXPORT_FORMATS[req.format]
    tmp_dir = tempfile.mkdtemp(prefix="frameloom-export-")
    base = os.path.splitext(project.name or "export")[0] or "export"
    filename = re.sub(r"[^\w.\-]+", "_", base) + ext
    out_path = os.path.join(tmp_dir, filename)
    try:
        sources.write_export(lf, req.format, out_path)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return FileResponse(out_path, media_type=media_type, filename=filename,
                        background=BackgroundTask(shutil.rmtree, tmp_dir, True))


BRANCH_NAME_RE = re.compile(r"^[\w.\-/]{1,64}$")


@app.post("/project/{id}/branch")
def create_branch(id: str, req: BranchRequest):
    project = _get_project(id)
    _require_node(project, req.from_node_id)
    name = req.name.strip()
    if not BRANCH_NAME_RE.match(name):
        raise HTTPException(400, "Branch names may use letters, numbers, '.', '-', '_' and '/' (max 64)")
    if name in project.branches:
        raise HTTPException(409, f"Branch '{name}' already exists")

    branch = Branch(name=name, head_node_id=req.from_node_id, forked_from_node_id=req.from_node_id)
    project.branches[name] = branch
    project.head = name
    _save_project(project)
    return branch


# ------------------------------------------------------------ history edits
@app.post("/project/{id}/step/{node_id}/edit")
def edit_step(id: str, node_id: str, req: EditStepRequest):
    """Change a step's settings; later steps on the branch are replayed."""
    return loom.edit_step(_get_project(id), node_id, req.params, req.branch)


@app.post("/project/{id}/step/{node_id}/delete")
def delete_step(id: str, node_id: str, req: BranchOnlyRequest):
    return loom.delete_step(_get_project(id), node_id, req.branch)


@app.post("/project/{id}/step/{node_id}/insert")
def insert_step(id: str, node_id: str, req: InsertStepRequest):
    """Insert a step after node_id (instead of branching) and replay the rest."""
    return loom.insert_step(_get_project(id), node_id, req.operation, req.branch)


@app.post("/project/{id}/step/{node_id}/note")
def note_step(id: str, node_id: str, req: NoteRequest):
    return loom.set_note(_get_project(id), node_id, req.note)


@app.post("/project/{id}/cherry-pick")
def cherry_pick(id: str, req: CherryPickRequest):
    return loom.cherry_pick(_get_project(id), req.node_ids, req.branch)


@app.post("/project/{id}/merge")
def merge(id: str, req: MergeRequest):
    return loom.merge(_get_project(id), req.source, req.into)


# ------------------------------------------------------------------ versions
@app.get("/project/{id}/versions")
def list_versions(id: str):
    project = _get_project(id)
    return sorted(project.versions.values(), key=lambda v: v.created_at)


@app.post("/project/{id}/versions")
def create_version(id: str, req: VersionRequest):
    """Name a step (default: the current one) so code and people can refer to it."""
    return loom.create_version(_get_project(id), req.name, req.node_id, req.message, req.metrics, req.notes)


@app.patch("/project/{id}/versions/{name}")
def update_version(id: str, name: str, req: VersionUpdateRequest):
    return loom.update_version(_get_project(id), name, req.message, req.notes, req.metrics, req.replace_metrics)


@app.delete("/project/{id}/versions/{name}")
def delete_version(id: str, name: str):
    loom.delete_version(_get_project(id), name)
    return {"status": "deleted"}


@app.delete("/project/{id}/branch/{name:path}")
def delete_branch(id: str, name: str):
    loom.delete_branch(_get_project(id), name)
    return {"status": "deleted"}


@app.post("/project/{id}/checkout")
def checkout(id: str, req: CheckoutRequest):
    project = _get_project(id)
    if req.target not in project.branches and req.target not in project.nodes:
        raise HTTPException(400, "Target not found")
    project.head = req.target
    _save_project(project)
    return {"status": "ok", "head": project.head}


def _undo_state(project: Project) -> dict:
    head_node = project.nodes.get(loom.head_node_id(project))
    return {
        "head": project.head,
        "node_id": loom.head_node_id(project),
        "can_undo": bool(project.reflog.get(project.head)) or bool(head_node and head_node.parent_id),
        "can_redo": bool(project.redo_stack.get(project.head)),
        "project": project,
    }


@app.post("/project/{id}/undo")
def undo(id: str):
    project = _get_project(id)
    current = loom.head_node_id(project)
    node = _require_node(project, current)
    if project.head in project.branches:
        log = project.reflog.get(project.head) or []
        # Undo restores where the branch pointed before its last change (add,
        # edit, delete, merge...). Older projects without a log step back one.
        previous = log.pop() if log else node.parent_id
        if not previous:
            raise HTTPException(400, "Nothing to undo")
        project.branches[project.head].head_node_id = previous
    else:
        if not node.parent_id:
            raise HTTPException(400, "Nothing to undo")
        project.head = node.parent_id
    project.redo_stack.setdefault(project.head, []).append(current)
    _save_project(project)
    return _undo_state(project)


@app.post("/project/{id}/redo")
def redo(id: str):
    project = _get_project(id)
    stack = project.redo_stack.get(project.head) or []
    if not stack:
        raise HTTPException(400, "Nothing to redo")
    target = stack.pop()
    current = loom.head_node_id(project)
    if target not in project.nodes:
        project.redo_stack.pop(project.head, None)
        _save_project(project)
        raise HTTPException(400, "Nothing to redo")
    if project.head in project.branches:
        project.reflog.setdefault(project.head, []).append(current)
        project.branches[project.head].head_node_id = target
        if not stack:
            project.redo_stack.pop(project.head, None)
    else:
        # Detached head: the redo stack is keyed by the node id, so move it along.
        project.redo_stack.pop(project.head, None)
        project.head = target
        if stack:
            project.redo_stack[target] = stack
    _save_project(project)
    return _undo_state(project)


# -------------------------------------------------------------------- recipes
@app.get("/recipes")
def list_recipes():
    return store.load_recipes()


@app.post("/project/{id}/recipe/save")
def save_recipe(id: str, req: SaveRecipeRequest):
    project = _get_project(id)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Recipe name is required")
    ops_list = [op for op in _path_ops(project, req.node_id) if op.type != "ingest"]
    if not ops_list:
        raise HTTPException(400, "This step has no transformations to save")
    recipe = Recipe(
        id=new_id(),
        name=name,
        operations=ops_list,
        # The recipe's input contract: the columns of the file it was built on.
        expected_schema=engine.get_schema(project, _root_id(project)),
        origin_hash=project.source_hash,
    )
    store.save_recipe(recipe)
    return recipe


@app.delete("/recipe/{recipe_id}")
def delete_recipe(recipe_id: str):
    if recipe_id not in store.recipes:
        raise HTTPException(404, "Recipe not found")
    store.delete_recipe(recipe_id)
    return {"status": "deleted"}


@app.post("/recipe/apply")
def apply_recipe(req: ApplyRecipeRequest):
    path = os.path.expanduser(req.new_source_path.strip())
    if not sources.source_exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    recipe = store.recipes.get(req.recipe_id) or next((r for r in store.load_recipes() if r.id == req.recipe_id), None)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    project, warnings = loom.apply_recipe(recipe, path, req.options, force=req.force)
    if project is None:
        return {"status": "warning", "warnings": warnings}
    return {"status": "success", "project": project, "warnings": warnings}


# ------------------------------------------------------------------- compare
@app.post("/project/{id}/audit/compare")
def audit_compare_endpoint(id: str, req: AuditRequest):
    project = _get_project(id)
    _require_node(project, req.node_a)
    _require_node(project, req.node_b)
    data = engine.audit_compare(project, req.node_a, req.node_b, req.key_column, sampled=True)
    schema_a = engine.get_schema(project, req.node_a)
    schema_b = engine.get_schema(project, req.node_b)
    schema = {req.key_column: schema_a.get(req.key_column, "String")}
    for col in schema_a:
        if col in schema_b and col != req.key_column:
            schema[f"{col}_a"] = schema_a[col]
            schema[f"{col}_b"] = schema_b[col]
    schema["_audit_status"] = "String"
    summary = {s: 0 for s in ("match", "mismatch", "missing_in_a", "missing_in_b")}
    for row in data:
        summary[row["_audit_status"]] = summary.get(row["_audit_status"], 0) + 1
    return {"data": data, "schema": schema, "summary": summary}


@app.get("/project/{id}/diff")
def diff_nodes(id: str, node_a: str, node_b: str):
    project = _get_project(id)
    if node_a not in project.nodes or node_b not in project.nodes:
        raise HTTPException(400, "Node not found")
    return engine.diff(project, node_a, node_b, sampled=True)


# The built UI ships inside the package (frameloom/static). In a source checkout
# it appears after `npm run build` in frontend/.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(os.path.join(STATIC_DIR, "index.html")):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def ui_missing():
        return JSONResponse(status_code=503, content={
            "detail": "The web UI isn't built. In a source checkout run: frameloom build-frontend"})
