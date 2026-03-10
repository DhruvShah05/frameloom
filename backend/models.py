from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional, Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Operation(BaseModel):
    id: str
    type: str
    params: dict[str, Any]
    label: str
    created_at: datetime = Field(default_factory=_now)
    engine_hint: Optional[str] = None


class Node(BaseModel):
    id: str
    parent_id: Optional[str]
    operation: Operation
    schema_hash: str
    row_count: Optional[int] = None
    # Free-text note on this step ("why I did this").
    note: Optional[str] = None


class Branch(BaseModel):
    name: str
    head_node_id: str
    forked_from_node_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Version(BaseModel):
    """A named, immutable pointer to a step - like a git tag."""
    name: str
    node_id: str
    message: str = ""
    branch: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    # What this version of the data produced, e.g. {"val_auc": 0.83}.
    metrics: dict[str, Any] = {}
    notes: str = ""


class SourceRecord(BaseModel):
    sha256: str
    signature: str
    recorded_at: datetime = Field(default_factory=_now)
    reason: str = "loaded"


class Project(BaseModel):
    id: str
    source_path: str
    source_hash: str
    # Reader options: format (csv/tsv/parquet/ipc/json/ndjson/excel/sqlite/duckdb),
    # separator, encoding, has_header, skip_rows, sheet_name, table, query, ...
    source_options: dict[str, Any] = {}
    # Fingerprint of the source the versions were built on.
    source_sha256: Optional[str] = None
    source_signature: Optional[str] = None
    source_history: list[SourceRecord] = []
    # An immutable copy of the source inside LOOM_HOME. When set, every
    # version replays from it, so versions never change under you.
    snapshot_path: Optional[str] = None
    snapshot_options: dict[str, Any] = {}
    # Interactive views work on a sample when set: {"rows": n, "method": "head"|"random", "seed": s}
    sample: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    nodes: dict[str, Node] = {}
    branches: dict[str, Branch] = {}
    # Branch name, or a node id when the head is detached.
    head: str
    # Per-branch history of previous head node ids (like git's reflog), used by undo.
    reflog: dict[str, list[str]] = {}
    # Per-branch stack of node ids that were undone (most recent last).
    redo_stack: dict[str, list[str]] = {}
    versions: dict[str, Version] = {}
    recipes: list[Any] = []

    @property
    def read_path(self) -> str:
        return self.snapshot_path or self.source_path

    @property
    def read_options(self) -> dict[str, Any]:
        return self.snapshot_options if self.snapshot_path else self.source_options


class Recipe(BaseModel):
    id: str
    name: str
    operations: list[Operation]
    expected_schema: dict[str, str]
    origin_hash: str = "unknown"
