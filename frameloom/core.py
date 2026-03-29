"""Project logic shared by the web API (main.py) and the Python API (loom/).

Everything that changes a project goes through here so the two front doors
behave identically.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from typing import Any, Optional

import polars as pl

from . import ops, sources
from .engine import ExecutionEngine
from .models import Branch, Node, Operation, Project, SourceRecord
from .store import Store

DEFAULT_SNAPSHOT_MAX_MB = 2048


class SourceChangedError(RuntimeError):
    """The data file changed since the project's versions were made."""


class HistoryError(ValueError):
    """A history rewrite (edit, delete, merge...) could not be replayed."""


def _snapshot_max_bytes() -> int:
    try:
        return int(float(os.environ.get("LOOM_SNAPSHOT_MAX_MB", DEFAULT_SNAPSHOT_MAX_MB)) * 1024 * 1024)
    except ValueError:
        return DEFAULT_SNAPSHOT_MAX_MB * 1024 * 1024


def file_digest(path: str, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_hash(schema: dict) -> str:
    schema_str = str(sorted((k, str(v)) for k, v in schema.items()))
    return hashlib.md5(schema_str.encode()).hexdigest()[:16]


def new_id() -> str:
    return str(uuid.uuid4())


class Loom:
    def __init__(self, store: Optional[Store] = None, engine: Optional[ExecutionEngine] = None):
        self.store = store or Store()
        self.engine = engine or ExecutionEngine()

    # ================================================================ sources
    def _materialize_to_parquet(self, path: str, options: dict) -> tuple[str, str]:
        """Read any source and freeze it as a Parquet file. Returns (tmp path, sha256)."""
        os.makedirs(self.store.snapshots_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.store.snapshots_dir, suffix=".parquet.tmp")
        os.close(fd)
        sources.load_source(path, options).collect().write_parquet(tmp)
        return tmp, file_digest(tmp)

    def snapshot_source(self, path: str, options: dict) -> tuple[str, dict, str]:
        """Store an immutable, content-addressed copy of the source.

        Returns (snapshot path, reader options for it, sha256 of the data).
        Identical files share one snapshot.
        """
        fmt = sources.infer_format(path, options)
        os.makedirs(self.store.snapshots_dir, exist_ok=True)
        if sources.is_remote(path) or fmt in ("sqlite", "duckdb"):
            tmp, digest = self._materialize_to_parquet(path, options)
            final = os.path.join(self.store.snapshots_dir, f"{digest}.parquet")
            snap_options = {"format": "parquet"}
        else:
            fd, tmp = tempfile.mkstemp(dir=self.store.snapshots_dir, suffix=".tmp")
            h = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
                    out.write(chunk)
            digest = h.hexdigest()
            # Keep the original name so the extension (and format detection) survives.
            final = os.path.join(self.store.snapshots_dir, f"{digest}__{os.path.basename(path)}")
            snap_options = {k: v for k, v in options.items()}
        if os.path.exists(final):
            os.remove(tmp)
        else:
            os.replace(tmp, final)
            os.chmod(final, 0o444)
        return final, snap_options, digest

    def _should_snapshot(self, path: str, options: dict, requested: Optional[bool]) -> bool:
        if requested is not None:
            return requested
        if sources.is_remote(path) or sources.infer_format(path, options) in ("sqlite", "duckdb"):
            return True
        try:
            return os.path.getsize(path) <= _snapshot_max_bytes()
        except OSError:
            return False

    def create_project(self, path: str, options: Optional[dict] = None, name: Optional[str] = None,
                       snapshot: Optional[bool] = None, save: bool = True) -> Project:
        path = os.path.expanduser(path.strip())
        if not sources.source_exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        options = {k: v for k, v in (options or {}).items() if v not in (None, "")}
        sources.infer_format(path, options)  # clear error for unknown formats

        schema = {n: str(d) for n, d in sources.load_source(path, options).collect_schema().items()}
        project_kwargs: dict[str, Any] = {}
        if self._should_snapshot(path, options, snapshot):
            snap_path, snap_options, digest = self.snapshot_source(path, options)
            project_kwargs.update(snapshot_path=snap_path, snapshot_options=snap_options, source_sha256=digest)
        elif not sources.is_remote(path):
            project_kwargs["source_sha256"] = file_digest(path)
        signature = sources.source_signature(path)
        project_kwargs["source_signature"] = signature
        if project_kwargs.get("source_sha256"):
            project_kwargs["source_history"] = [SourceRecord(sha256=project_kwargs["source_sha256"], signature=signature)]

        root_id = new_id()
        root = Node(
            id=root_id,
            parent_id=None,
            operation=Operation(id=new_id(), type="ingest", params={}, label="Load Data"),
            schema_hash=schema_hash(schema),
        )
        project = Project(
            id=new_id(),
            source_path=path,
            source_hash=file_digest(path, "md5") if not sources.is_remote(path) else "unknown",
            source_options=options,
            name=name or os.path.basename(path.split("?", 1)[0]) or path,
            nodes={root_id: root},
            branches={"main": Branch(name="main", head_node_id=root_id)},
            head="main",
            **project_kwargs,
        )
        if save:
            self.store.save(project)
        return project

    def source_status(self, project: Project, record_baseline: bool = True) -> dict:
        """Is the data behind this project still the data its versions were made from?

        status: "pinned"   - replays from an immutable snapshot (always reproducible)
                "ok"       - the original file is unchanged
                "changed"  - the original file changed; versions now show different data
                "missing"  - the file is gone
                "unverified" - remote/database source without a snapshot
        """
        path = project.source_path
        original_changed = False
        if not sources.is_remote(path) and os.path.exists(path):
            sig = sources.source_signature(path)
            if project.source_sha256 is None:
                if record_baseline:  # projects from before fingerprints existed
                    project.source_sha256 = file_digest(path)
                    project.source_signature = sig
                    project.source_history.append(SourceRecord(sha256=project.source_sha256, signature=sig, reason="first check"))
                    self.store.save(project)
            elif sig != project.source_signature:
                if file_digest(path) == project.source_sha256:
                    project.source_signature = sig  # touched, not changed
                    self.store.save(project)
                else:
                    original_changed = True

        if project.snapshot_path:
            if not os.path.exists(project.snapshot_path):
                return {"status": "missing", "pinned": True,
                        "message": f"The snapshot for this project is missing: {project.snapshot_path}"}
            msg = "Versions replay from a frozen copy of the data, so they never change."
            if original_changed:
                msg += " The original file has changed since; open it as a new project to work with the new data."
            return {"status": "pinned", "pinned": True, "original_changed": original_changed, "message": msg,
                    "sha256": project.source_sha256}
        if sources.is_remote(path):
            return {"status": "unverified", "pinned": False,
                    "message": "This remote source isn't pinned, so versions may change if it changes."}
        if not os.path.exists(path):
            return {"status": "missing", "pinned": False, "message": f"The data file is missing: {path}"}
        if original_changed:
            return {"status": "changed", "pinned": False, "sha256": project.source_sha256,
                    "message": "The data file changed since these versions were made. Every step now replays on the NEW data."}
        return {"status": "ok", "pinned": False, "sha256": project.source_sha256,
                "message": "The data file is unchanged since these versions were made."}

    def accept_source(self, project: Project, snapshot: bool = False) -> dict:
        """Make the current file the new baseline (optionally pinning it)."""
        path = project.source_path
        if not sources.source_exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        if snapshot:
            snap_path, snap_options, digest = self.snapshot_source(path, project.source_options)
            project.snapshot_path, project.snapshot_options = snap_path, snap_options
        else:
            digest = file_digest(path) if not sources.is_remote(path) else None
            project.snapshot_path, project.snapshot_options = None, {}
        project.source_sha256 = digest
        project.source_signature = sources.source_signature(path)
        if digest:
            project.source_history.append(SourceRecord(sha256=digest, signature=project.source_signature,
                                                       reason="pinned" if snapshot else "accepted new file"))
        self.engine.invalidate_project(project.id)
        self.store.save(project)
        return self.source_status(project)

    # ================================================================ queries
    def root_id(self, project: Project) -> str:
        return next(n.id for n in project.nodes.values() if n.parent_id is None)

    def head_node_id(self, project: Project) -> str:
        branch = project.branches.get(project.head)
        return branch.head_node_id if branch else project.head

    def path_nodes(self, project: Project, node_id: str) -> list[Node]:
        if node_id not in project.nodes:
            raise KeyError(f"Step '{node_id}' not found")
        out = []
        cur: Optional[str] = node_id
        while cur:
            node = project.nodes[cur]
            out.insert(0, node)
            cur = node.parent_id
        return out

    def resolve(self, project: Project, ref: Optional[str] = None) -> str:
        """Turn a version name, branch name, node id (or prefix), or None (current) into a node id."""
        if ref is None or ref in ("", "HEAD", "head"):
            return self.head_node_id(project)
        if ref in project.versions:
            return project.versions[ref].node_id
        if ref in project.branches:
            return project.branches[ref].head_node_id
        if ref in project.nodes:
            return ref
        matches = [n for n in project.nodes if n.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"'{ref}' is not a version, branch or step of project '{project.name}'")

    def dataframe(self, project: Project, ref: Optional[str] = None, allow_changed_source: bool = False) -> pl.DataFrame:
        status = self.source_status(project)
        if status["status"] == "changed" and not allow_changed_source:
            raise SourceChangedError(
                f"{status['message']} Pass allow_changed_source=True to load anyway, "
                "or accept the new file as the baseline."
            )
        if status["status"] == "missing":
            raise FileNotFoundError(status["message"])
        return self.engine.collect(project, self.resolve(project, ref))

    # ================================================================== steps
    def _make_node(self, project: Project, parent_id: str, op: Operation) -> Node:
        parent_lf = self.engine.build_plan(project, parent_id)
        op = self.engine.normalize_operation(parent_lf, op)
        if op.type == "edit_cell" and op.params.get("match") is not None:
            hits = parent_lf.filter(ops.edit_match_expr(op.params)).select(pl.len()).collect().item()
            if hits == 0:
                key, val = next(iter(op.params["match"].items()))
                raise ValueError(f"Edit of '{op.params['column']}': the row ({key}={val}, ...) isn't in the data at this step")
        new_lf = self.engine._apply_operation(parent_lf, op)
        schema = {name: str(dtype) for name, dtype in new_lf.collect_schema().items()}
        # Run a small slice now so data errors surface before the step is saved.
        new_lf.head(50).collect()
        return Node(id=new_id(), parent_id=parent_id, operation=op, schema_hash=schema_hash(schema))

    @staticmethod
    def build_operation(raw: dict) -> Operation:
        raw = dict(raw)
        if not raw.get("type"):
            raise ValueError("Operation type is required")
        if raw["type"] == "ingest":
            raise ValueError("'ingest' can only be the first step")
        ops.get_spec(raw["type"])
        raw["id"] = raw.get("id") or new_id()
        raw.setdefault("params", {})
        raw.setdefault("label", raw.get("type", ""))
        return Operation(**raw)

    def _move_branch(self, project: Project, branch: str, node_id: str) -> None:
        """Point a branch at a new step, remembering where it was (for undo)."""
        b = project.branches[branch]
        if b.head_node_id != node_id:
            project.reflog.setdefault(branch, []).append(b.head_node_id)
            b.head_node_id = node_id
        project.redo_stack.pop(branch, None)

    def add_step(self, project: Project, parent_id: str, operation: dict) -> Node:
        if parent_id not in project.nodes:
            raise KeyError("Node not found")
        node = self._make_node(project, parent_id, self.build_operation(operation))
        project.nodes[node.id] = node

        matching = [name for name, b in project.branches.items() if b.head_node_id == parent_id]
        if matching:
            branch = project.head if project.head in matching else matching[0]
        else:
            branch = f"branch-{new_id()[:8]}"
            project.branches[branch] = Branch(name=branch, head_node_id=parent_id, forked_from_node_id=parent_id)
        self._move_branch(project, branch, node.id)
        project.head = branch
        self.store.save(project)
        return node

    # ============================================================== history
    def _branch_for_rewrite(self, project: Project, branch: Optional[str]) -> str:
        branch = branch or project.head
        if branch not in project.branches:
            raise HistoryError("Switch to a branch first: history can only be rewritten on a branch")
        return branch

    def _tail_after(self, project: Project, branch: str, node_id: str) -> list[Node]:
        path = self.path_nodes(project, project.branches[branch].head_node_id)
        ids = [n.id for n in path]
        if node_id not in ids:
            raise HistoryError(f"That step isn't part of branch '{branch}'")
        return path[ids.index(node_id) + 1:]

    def _replay(self, project: Project, base_id: str, operations: list, fresh_labels: int = 0) -> str:
        """Re-run steps on top of base_id. All-or-nothing.

        ``operations`` holds Nodes (copied steps, which keep a link to where
        they came from) or Operations (new steps). The first ``fresh_labels``
        get a newly generated label (they were just edited).
        """
        created: list[str] = []
        parent = base_id
        try:
            for i, item in enumerate(operations):
                origin = None
                if isinstance(item, Node):
                    origin = item.origin_id or item.id
                    op = item.operation
                else:
                    op = item
                clone = Operation(id=new_id(), type=op.type, params=dict(op.params),
                                  label="" if i < fresh_labels else op.label, engine_hint=op.engine_hint)
                try:
                    node = self._make_node(project, parent, clone)
                except Exception as e:
                    raise HistoryError(f"Step {i + 1} of the replay ('{op.label or op.type}') failed: {e}") from e
                if isinstance(item, Node):
                    node.note = item.note
                if origin and i >= fresh_labels:
                    node.origin_id = origin
                project.nodes[node.id] = node
                created.append(node.id)
                parent = node.id
        except BaseException:
            for nid in created:
                project.nodes.pop(nid, None)
            raise
        return parent

    def _finish(self, project: Project, branch: str, tip: str) -> dict:
        self._move_branch(project, branch, tip)
        project.head = branch
        self.store.save(project)
        return {"node_id": tip, "branch": branch, "project": project}

    def edit_step(self, project: Project, node_id: str, params: dict, branch: Optional[str] = None) -> dict:
        """Change a step's settings and replay everything after it."""
        branch = self._branch_for_rewrite(project, branch)
        node = project.nodes.get(node_id)
        if not node or node.parent_id is None:
            raise HistoryError("The first step (loading the data) can't be edited")
        tail = self._tail_after(project, branch, node_id)
        edited = node.operation.model_copy(update={"params": dict(params)})
        tip = self._replay(project, node.parent_id, [edited] + tail, fresh_labels=1)
        return self._finish(project, branch, tip)

    def delete_step(self, project: Project, node_id: str, branch: Optional[str] = None) -> dict:
        """Remove a step and replay everything after it."""
        branch = self._branch_for_rewrite(project, branch)
        node = project.nodes.get(node_id)
        if not node or node.parent_id is None:
            raise HistoryError("The first step (loading the data) can't be deleted")
        tail = self._tail_after(project, branch, node_id)
        tip = self._replay(project, node.parent_id, tail)
        return self._finish(project, branch, tip)

    def insert_step(self, project: Project, after_id: str, operation: dict, branch: Optional[str] = None) -> dict:
        """Add a step in the middle of a branch and replay what follows."""
        branch = self._branch_for_rewrite(project, branch)
        tail = self._tail_after(project, branch, after_id)
        op = self.build_operation(operation)
        tip = self._replay(project, after_id, [op] + tail, fresh_labels=1)
        return self._finish(project, branch, tip)

    def cherry_pick(self, project: Project, node_ids: list[str], branch: Optional[str] = None) -> dict:
        """Copy steps (from any branch) onto the end of a branch."""
        branch = self._branch_for_rewrite(project, branch)
        if not node_ids:
            raise HistoryError("Choose at least one step to copy")
        operations = []
        for nid in node_ids:
            node = project.nodes.get(nid)
            if not node:
                raise KeyError(f"Step '{nid}' not found")
            if node.parent_id is None:
                raise HistoryError("The load step can't be copied")
            operations.append(node)
        tip = self._replay(project, project.branches[branch].head_node_id, operations)
        return self._finish(project, branch, tip)

    def merge_base(self, project: Project, a: str, b: str) -> str:
        ancestors = {n.id for n in self.path_nodes(project, a)}
        for node in reversed(self.path_nodes(project, b)):
            if node.id in ancestors:
                return node.id
        raise HistoryError("These steps share no history")

    def merge(self, project: Project, source: str, into: Optional[str] = None) -> dict:
        """Bring another branch's steps into this one by replaying them on top."""
        into = self._branch_for_rewrite(project, into)
        if source not in project.branches:
            raise KeyError(f"Branch '{source}' not found")
        if source == into:
            raise HistoryError("Can't merge a branch into itself")
        src_head = project.branches[source].head_node_id
        dst_head = project.branches[into].head_node_id
        base = self.merge_base(project, src_head, dst_head)
        if base == src_head:
            raise HistoryError(f"'{into}' already has every step from '{source}'")
        if base == dst_head:  # nothing new on the target: just move it forward
            result = self._finish(project, into, src_head)
            result.update(fast_forward=True, replayed=0)
            return result
        # Skip steps the target already has (copied earlier by a merge or cherry-pick).
        have = set()
        for n in self.path_nodes(project, dst_head):
            have.add(n.id)
            if n.origin_id:
                have.add(n.origin_id)
        new_steps = [n for n in self._tail_after(project, source, base)
                     if n.id not in have and (n.origin_id or n.id) not in have]
        if not new_steps:
            raise HistoryError(f"'{into}' already has every step from '{source}'")
        tip = self._replay(project, dst_head, new_steps)
        result = self._finish(project, into, tip)
        result.update(fast_forward=False, replayed=len(new_steps))
        return result

    def set_note(self, project: Project, node_id: str, note: str) -> Node:
        node = project.nodes.get(node_id)
        if not node:
            raise KeyError(f"Step '{node_id}' not found")
        node.note = note.strip() or None
        self.store.save(project)
        return node

    def delete_branch(self, project: Project, name: str) -> None:
        if name not in project.branches:
            raise KeyError(f"Branch '{name}' not found")
        if name == project.head:
            raise HistoryError("Switch to another branch before deleting this one")
        if len(project.branches) == 1:
            raise HistoryError("A project needs at least one branch")
        del project.branches[name]
        project.reflog.pop(name, None)
        project.redo_stack.pop(name, None)
        self.store.save(project)

    # ============================================================= versions
    VERSION_NAME_RE = r"^[\w.\-]{1,64}$"

    def create_version(self, project: Project, name: str, ref: Optional[str] = None, message: str = "",
                       metrics: Optional[dict] = None, notes: str = "") -> "Version":
        import re

        from .models import Version

        name = (name or "").strip()
        if not re.match(self.VERSION_NAME_RE, name):
            raise ValueError("Version names may use letters, numbers, '.', '-' and '_' (max 64), e.g. v1 or baseline")
        if name in project.versions:
            raise ValueError(f"Version '{name}' already exists - versions never move; pick a new name")
        if name in project.branches:
            raise ValueError(f"'{name}' is already a branch name")
        node_id = self.resolve(project, ref)
        branch = project.head if project.head in project.branches and project.branches[project.head].head_node_id == node_id else None
        version = Version(name=name, node_id=node_id, message=message.strip(), branch=branch,
                          metrics=dict(metrics or {}), notes=notes)
        project.versions[name] = version
        self.store.save(project)
        return version

    def update_version(self, project: Project, name: str, message: Optional[str] = None,
                       notes: Optional[str] = None, metrics: Optional[dict] = None, replace_metrics: bool = False):
        if name not in project.versions:
            raise KeyError(f"Version '{name}' not found")
        v = project.versions[name]
        if message is not None:
            v.message = message.strip()
        if notes is not None:
            v.notes = notes
        if metrics is not None:
            bad = [k for k, val in metrics.items() if not isinstance(val, (int, float, str, bool)) and val is not None]
            if bad:
                raise ValueError(f"Metric values must be numbers, text or true/false: {', '.join(bad)}")
            v.metrics = dict(metrics) if replace_metrics else {**v.metrics, **metrics}
        self.store.save(project)
        return v

    def delete_version(self, project: Project, name: str) -> None:
        if name not in project.versions:
            raise KeyError(f"Version '{name}' not found")
        del project.versions[name]
        self.store.save(project)

    # ============================================================== recipes
    def recipe_warnings(self, recipe, path: str, options: dict) -> list[str]:
        actual = {n: str(d) for n, d in sources.load_source(path, options).collect_schema().items()}
        warnings = []
        source_hash = file_digest(path, "md5") if not sources.is_remote(path) else "unknown"
        if recipe.origin_hash != "unknown" and source_hash != recipe.origin_hash:
            warnings.append("This file is different from the one the recipe was built on.")
        for col, dtype in recipe.expected_schema.items():
            if col not in actual:
                warnings.append(f"Missing column '{col}'.")
            elif actual[col] != dtype:
                warnings.append(f"Column '{col}' expected {dtype} but found {actual[col]} in the new file.")
        return warnings

    def apply_recipe(self, recipe, path: str, options: Optional[dict] = None, force: bool = False,
                     save: bool = True, snapshot: Optional[bool] = None) -> tuple[Optional[Project], list[str]]:
        """Replay a recipe on a new file. Returns (project, warnings); project is
        None when there are warnings and force is False."""
        options = options or {}
        path = os.path.expanduser(path.strip())
        if not sources.source_exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        warnings = self.recipe_warnings(recipe, path, options)
        if warnings and not force:
            return None, warnings
        project = self.create_project(path, options, snapshot=snapshot, save=False)
        parent = self.root_id(project)
        lf = self.engine.build_plan(project, parent)
        for i, op in enumerate(recipe.operations):
            new_op = Operation(id=new_id(), type=op.type, params=op.params, label=op.label, engine_hint=op.engine_hint)
            try:
                lf = self.engine._apply_operation(lf, new_op)
                schema = {name: str(dtype) for name, dtype in lf.collect_schema().items()}
                lf.head(10).collect()
            except Exception as e:
                raise HistoryError(f"Step {i + 1} ('{op.label or op.type}') failed: {e}") from e
            node = Node(id=new_id(), parent_id=parent, operation=new_op, schema_hash=schema_hash(schema))
            project.nodes[node.id] = node
            parent = node.id
        project.branches["main"].head_node_id = parent
        if save:
            self.store.save(project)
        return project, warnings

    # =================================================================== gc
    def gc(self, dry_run: bool = False, keep_undo: bool = True) -> dict:
        """Remove what nothing points at any more.

        * steps no branch or version can reach (with keep_undo, steps that
          undo/redo could bring back are kept too);
        * snapshot and DataFrame-input files no project uses;
        * leftover temporary files.
        """
        report = {"projects": 0, "steps_removed": 0, "files_removed": [], "bytes_freed": 0, "dry_run": dry_run}
        projects = self.store.all()
        for p in projects:
            roots = [b.head_node_id for b in p.branches.values()] + [v.node_id for v in p.versions.values()]
            if keep_undo:
                roots += [n for log in p.reflog.values() for n in log]
                roots += [n for stack in p.redo_stack.values() for n in stack]
                if p.head not in p.branches:
                    roots.append(p.head)
            keep: set[str] = set()
            for nid in roots:
                while nid and nid in p.nodes and nid not in keep:
                    keep.add(nid)
                    nid = p.nodes[nid].parent_id
            drop = [nid for nid in p.nodes if nid not in keep]
            if drop or (not keep_undo and (p.reflog or p.redo_stack)):
                report["projects"] += 1
                report["steps_removed"] += len(drop)
                if not dry_run:
                    for nid in drop:
                        del p.nodes[nid]
                    if not keep_undo:
                        p.reflog, p.redo_stack = {}, {}
                    self.engine.invalidate_project(p.id)
                    self.store.save(p)

        used = {os.path.abspath(p.snapshot_path) for p in projects if p.snapshot_path}
        used |= {os.path.abspath(p.source_path) for p in projects}
        for folder in (self.store.snapshots_dir, os.path.join(self.store.home, "inputs")):
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                path = os.path.abspath(os.path.join(folder, name))
                if path in used or not os.path.isfile(path):
                    continue
                report["files_removed"].append(path)
                report["bytes_freed"] += os.path.getsize(path)
                if not dry_run:
                    os.remove(path)
        return report
