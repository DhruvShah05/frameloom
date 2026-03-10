from typing import Any, Literal, Optional

from . import ops, sources
from .models import Operation

SUPPORTED_TARGETS = ("polars",)


def render_op(op: Operation, target: Literal["polars"] = "polars") -> str:
    if target not in SUPPORTED_TARGETS:
        return f"# NotImplemented: target {target}"
    spec = ops.OPS.get(op.type)
    if spec is None:
        return f"# NotImplemented: {op.type}"
    lines = spec.code(op.params)
    return "\n".join(lines) if lines else "# Source loaded"


def _provenance(snapshot: Optional[str], sha256: Optional[str]) -> list[str]:
    lines = []
    if sha256:
        lines.append(f"# Built from data with sha256 {sha256}")
    if snapshot:
        lines.append(f"# Loom's frozen copy of that data: {snapshot}")
        lines.append("# (point the path below at it to reproduce these versions exactly)")
    return lines


def generate_script(operations: list[Operation], target: str, source_path: str,
                    source_options: Optional[dict[str, Any]] = None,
                    snapshot: Optional[str] = None, sha256: Optional[str] = None) -> str:
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported export target '{target}'. Supported: {', '.join(SUPPORTED_TARGETS)}")
    imports = ["import polars as pl"]
    load_imports, load_expr = sources.load_statement(source_path, source_options or {})
    body = [*_provenance(snapshot, sha256), f"df = {load_expr}"]
    for op in operations:
        if op.type == "ingest":
            continue
        spec = ops.OPS.get(op.type)
        if spec is not None:
            for imp in spec.imports:
                if imp not in imports:
                    imports.append(imp)
        if op.label:
            body.append(f"# {' '.join(op.label.splitlines())}")
        body.append(render_op(op, "polars"))
    for imp in load_imports:
        if imp not in imports:
            imports.append(imp)
    return "\n".join(imports + [""] + body) + "\n"
