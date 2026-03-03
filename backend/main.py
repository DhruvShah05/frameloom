import hashlib
import os
import re
import shutil
import tempfile
import uuid
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
from .engine import ExecutionEngine
from .models import Branch, Node, Operation, Project, Recipe


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_persisted_state()
    yield


app = FastAPI(title="Loom backend", lifespan=lifespan)


@app.exception_handler(pl.exceptions.PolarsError)
async def polars_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(duckdb.Error)
async def duckdb_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ValueError)
@app.exception_handler(FileNotFoundError)
async def user_error_handler(request: Request, exc: Exception):
    # Bad input (unknown column, bad value, unsupported format...): a 400, not a crash.
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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

engine = ExecutionEngine()
projects: dict[str, Project] = {}
recipes_db: dict[str, Recipe] = {}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOM_DIR = os.environ.get("LOOM_HOME") or os.path.join(BASE_DIR, ".loom")
PROJECTS_DIR = os.path.join(LOOM_DIR, "projects")
RECIPES_DIR = os.path.join(LOOM_DIR, "recipes")


def _compute_source_hash(file_path: str) -> str:
    if sources.is_remote(file_path):
        return "unknown"
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return "unknown"


def _compute_schema_hash(schema: dict) -> str:
    schema_str = str(sorted((k, str(v)) for k, v in schema.items()))
    return hashlib.md5(schema_str.encode()).hexdigest()[:16]


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _save_project(project: Project):
    _atomic_write(os.path.join(PROJECTS_DIR, project.id, "project.json"), project.model_dump_json())


def _save_recipe(recipe: Recipe):
    _atomic_write(os.path.join(RECIPES_DIR, f"{recipe.id}.json"), recipe.model_dump_json())


def _load_persisted_state():
    if os.path.exists(PROJECTS_DIR):
        for pid in os.listdir(PROJECTS_DIR):
            p_path = os.path.join(PROJECTS_DIR, pid, "project.json")
            if os.path.exists(p_path):
                with open(p_path, "r") as f:
                    try:
                        project = Project.model_validate_json(f.read())
                        projects[project.id] = project
                    except Exception as e:
                        print(f"Failed to load project {pid}: {e}")

    if os.path.exists(RECIPES_DIR):
        for r_file in os.listdir(RECIPES_DIR):
            if r_file.endswith(".json"):
                with open(os.path.join(RECIPES_DIR, r_file), "r") as f:
                    try:
                        recipe = Recipe.model_validate_json(f.read())
                        recipes_db[recipe.id] = recipe
                    except Exception as e:
                        print(f"Failed to load recipe {r_file}: {e}")


def _get_project(project_id: str) -> Project:
    project = projects.get(project_id)
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
    path_nodes = []
    current_id = node_id
    while current_id:
        node = project.nodes.get(current_id)
        if not node:
            break
        path_nodes.insert(0, node)
        current_id = node.parent_id
    return [n.operation for n in path_nodes]


def _head_node_id(project: Project) -> str:
    branch = project.branches.get(project.head)
    return branch.head_node_id if branch else project.head


# --------------------------------------------------------------------- models
class LoadRequest(BaseModel):
    path: str
    options: dict[str, Any] = {}
    name: Optional[str] = None


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
    target: Literal["polars"] = "polars"


class ExportDataRequest(BaseModel):
    node_id: str
    format: Literal["csv", "tsv", "parquet", "xlsx", "json", "ndjson", "ipc"] = "csv"


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
    return {"path": path, "format": fmt, "tables": tables, "needs_table": fmt in ("sqlite", "duckdb")}


# ------------------------------------------------------------------- projects
def _create_project(path: str, options: dict[str, Any], name: Optional[str] = None) -> Project:
    path = os.path.expanduser(path.strip())
    if not sources.source_exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    options = {k: v for k, v in (options or {}).items() if v not in (None, "")}
    sources.infer_format(path, options)  # raises a clear error for unknown formats

    lf = engine.load_source(path, options)
    schema_dict = {n: str(d) for n, d in lf.collect_schema().items()}

    root_node_id = str(uuid.uuid4())
    root_node = Node(
        id=root_node_id,
        parent_id=None,
        operation=Operation(id=str(uuid.uuid4()), type="ingest", params={}, label="Load Data"),
        schema_hash=_compute_schema_hash(schema_dict),
    )
    return Project(
        id=str(uuid.uuid4()),
        source_path=path,
        source_hash=_compute_source_hash(path),
        source_options=options,
        name=name or os.path.basename(path.split("?", 1)[0]) or path,
        nodes={root_node_id: root_node},
        branches={"main": Branch(name="main", head_node_id=root_node_id)},
        head="main",
    )


@app.post("/project/load")
def load_project(req: LoadRequest):
    project = _create_project(req.path, req.options, req.name)
    projects[project.id] = project
    _save_project(project)
    return project


@app.get("/projects")
def list_projects():
    out = []
    for p in projects.values():
        out.append({
            "id": p.id,
            "name": p.name or os.path.basename(p.source_path),
            "source_path": p.source_path,
            "created_at": p.created_at,
            "steps": len(p.nodes) - 1,
            "branches": len(p.branches),
            "source_exists": sources.source_exists(p.source_path),
        })
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


@app.delete("/project/{id}")
def delete_project(id: str):
    _get_project(id)
    projects.pop(id, None)
    engine.invalidate_project(id)
    project_dir = os.path.join(PROJECTS_DIR, id)
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir)
    return {"status": "deleted"}


@app.get("/project/{id}/tree")
def get_tree(id: str):
    return _get_project(id)


@app.post("/project/{id}/op")
def add_op(id: str, req: OpRequest):
    project = _get_project(id)
    _require_node(project, req.parent_node_id)

    raw = dict(req.operation)
    if not raw.get("id"):
        raw["id"] = str(uuid.uuid4())
    raw.setdefault("params", {})
    raw.setdefault("label", raw.get("type", ""))
    if not raw.get("type"):
        raise HTTPException(status_code=400, detail="Operation type is required")
    if raw["type"] == "ingest":
        raise HTTPException(status_code=400, detail="'ingest' can only be the first step")
    op = Operation(**raw)

    parent_lf = engine.build_plan(project, req.parent_node_id)
    op = engine.normalize_operation(parent_lf, op)
    new_lf = engine._apply_operation(parent_lf, op)
    schema_dict = {name: str(dtype) for name, dtype in new_lf.collect_schema().items()}
    # Run a small slice now so data errors surface before the step is saved.
    new_lf.head(50).collect()

    new_node = Node(
        id=str(uuid.uuid4()),
        parent_id=req.parent_node_id,
        operation=op,
        schema_hash=_compute_schema_hash(schema_dict),
    )
    project.nodes[new_node.id] = new_node

    # Resolve which branch is being advanced
    matching_branches = [name for name, b in project.branches.items() if b.head_node_id == req.parent_node_id]
    if matching_branches:
        branch_to_advance = project.head if project.head in matching_branches else matching_branches[0]
        project.branches[branch_to_advance].head_node_id = new_node.id
        project.head = branch_to_advance
    else:
        branch_to_advance = f"branch-{str(uuid.uuid4())[:8]}"
        project.branches[branch_to_advance] = Branch(
            name=branch_to_advance,
            head_node_id=new_node.id,
            forked_from_node_id=req.parent_node_id,
        )
        project.head = branch_to_advance
    # A new step starts a new future: nothing left to redo on this branch.
    project.redo_stack.pop(branch_to_advance, None)

    _save_project(project)
    return new_node


@app.get("/project/{id}/data")
def get_data(id: str, node_id: str, offset: int = 0, limit: int = 100):
    project = _get_project(id)
    _require_node(project, node_id)
    view = engine.get_view(project, node_id, offset=max(offset, 0), limit=min(max(limit, 1), 10000))
    for row in view["data"]:
        row.pop("__row_nr", None)
    return view


@app.post("/project/{id}/view")
def get_view(id: str, req: ViewRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    return engine.get_view(project, req.node_id, offset=max(req.offset, 0), limit=min(max(req.limit, 1), 10000),
                           sort=req.sort, filters=req.filters)


@app.get("/project/{id}/profile")
def get_profile(id: str, node_id: str, bins: int = 20):
    project = _get_project(id)
    _require_node(project, node_id)
    return engine.profile(project, node_id, bins=min(max(bins, 2), 100))


@app.post("/project/{id}/chart")
def get_chart(id: str, req: ChartRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    return engine.chart(project, req.node_id, req.model_dump(exclude={"node_id"}))


@app.post("/project/{id}/export/code")
def export_code(id: str, req: ExportRequest):
    project = _get_project(id)
    code = generate_script(_path_ops(project, req.node_id), req.target, project.source_path, project.source_options)
    return {"code": code}


@app.post("/project/{id}/export/data")
def export_data(id: str, req: ExportDataRequest):
    project = _get_project(id)
    _require_node(project, req.node_id)
    lf = engine.build_plan(project, req.node_id)
    media_type, ext = sources.EXPORT_FORMATS[req.format]
    tmp_dir = tempfile.mkdtemp(prefix="loom-export-")
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


@app.post("/project/{id}/checkout")
def checkout(id: str, req: CheckoutRequest):
    project = _get_project(id)
    if req.target not in project.branches and req.target not in project.nodes:
        raise HTTPException(400, "Target not found")
    project.head = req.target
    _save_project(project)
    return {"status": "ok", "head": project.head}


def _undo_state(project: Project) -> dict:
    head_node = project.nodes.get(_head_node_id(project))
    return {
        "head": project.head,
        "node_id": _head_node_id(project),
        "can_undo": bool(head_node and head_node.parent_id),
        "can_redo": bool(project.redo_stack.get(project.head)),
        "project": project,
    }


@app.post("/project/{id}/undo")
def undo(id: str):
    project = _get_project(id)
    current = _head_node_id(project)
    node = _require_node(project, current)
    if not node.parent_id:
        raise HTTPException(400, "Nothing to undo")
    if project.head in project.branches:
        project.branches[project.head].head_node_id = node.parent_id
    else:
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
    current = _head_node_id(project)
    node = project.nodes.get(target)
    if not node or node.parent_id != current:
        project.redo_stack.pop(project.head, None)
        _save_project(project)
        raise HTTPException(400, "Nothing to redo")
    if project.head in project.branches:
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
    return list(recipes_db.values())


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
        id=str(uuid.uuid4()),
        name=name,
        operations=ops_list,
        # The recipe's input contract: the columns of the file it was built on.
        expected_schema=engine.get_schema(project, _root_id(project)),
        origin_hash=project.source_hash,
    )
    recipes_db[recipe.id] = recipe
    _save_recipe(recipe)
    return recipe


def _root_id(project: Project) -> str:
    return next(n.id for n in project.nodes.values() if n.parent_id is None)


@app.delete("/recipe/{recipe_id}")
def delete_recipe(recipe_id: str):
    if recipe_id not in recipes_db:
        raise HTTPException(404, "Recipe not found")
    recipes_db.pop(recipe_id)
    path = os.path.join(RECIPES_DIR, f"{recipe_id}.json")
    if os.path.exists(path):
        os.remove(path)
    return {"status": "deleted"}


@app.post("/recipe/apply")
def apply_recipe(req: ApplyRecipeRequest):
    path = os.path.expanduser(req.new_source_path.strip())
    if not sources.source_exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    recipe = recipes_db.get(req.recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Validate the new file before creating anything.
    actual_schema = {n: str(d) for n, d in engine.load_source(path, req.options).collect_schema().items()}
    warnings = []
    source_hash = _compute_source_hash(path)
    if recipe.origin_hash != "unknown" and source_hash != recipe.origin_hash:
        warnings.append("This file is different from the one the recipe was built on.")
    for col, dtype in recipe.expected_schema.items():
        if col not in actual_schema:
            warnings.append(f"Missing column '{col}'.")
        elif actual_schema[col] != dtype:
            warnings.append(f"Column '{col}' expected {dtype} but found {actual_schema[col]} in the new file.")
    if warnings and not req.force:
        return {"status": "warning", "warnings": warnings}

    project = _create_project(path, req.options)
    current_parent_id = _root_id(project)
    lf = engine.build_plan(project, current_parent_id)
    for i, op in enumerate(recipe.operations):
        new_op = Operation(id=str(uuid.uuid4()), type=op.type, params=op.params, label=op.label, engine_hint=op.engine_hint)
        try:
            lf = engine._apply_operation(lf, new_op)
            schema_dict = {name: str(dtype) for name, dtype in lf.collect_schema().items()}
            lf.head(10).collect()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Step {i + 1} ('{op.label or op.type}') failed: {e}")
        new_node = Node(id=str(uuid.uuid4()), parent_id=current_parent_id, operation=new_op,
                        schema_hash=_compute_schema_hash(schema_dict))
        project.nodes[new_node.id] = new_node
        current_parent_id = new_node.id

    project.branches["main"].head_node_id = current_parent_id
    projects[project.id] = project
    _save_project(project)
    return {"status": "success", "project": project, "warnings": warnings}


# ------------------------------------------------------------------- compare
@app.post("/project/{id}/audit/compare")
def audit_compare_endpoint(id: str, req: AuditRequest):
    project = _get_project(id)
    _require_node(project, req.node_a)
    _require_node(project, req.node_b)
    data = engine.audit_compare(project, req.node_a, req.node_b, req.key_column)
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
    return engine.diff(project, node_a, node_b)


frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
