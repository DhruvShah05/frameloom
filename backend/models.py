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


class Branch(BaseModel):
    name: str
    head_node_id: str
    forked_from_node_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Project(BaseModel):
    id: str
    source_path: str
    source_hash: str
    # Reader options: format (csv/tsv/parquet/ipc/json/ndjson/excel/sqlite/duckdb),
    # separator, encoding, has_header, skip_rows, sheet_name, table, query, ...
    source_options: dict[str, Any] = {}
    name: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    nodes: dict[str, Node] = {}
    branches: dict[str, Branch] = {}
    # Branch name, or a node id when the head is detached.
    head: str
    # Per-branch stack of node ids that were undone (most recent last).
    redo_stack: dict[str, list[str]] = {}
    recipes: list[Any] = []


class Recipe(BaseModel):
    id: str
    name: str
    operations: list[Operation]
    expected_schema: dict[str, str]
    origin_hash: str = "unknown"
