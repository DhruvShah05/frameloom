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


def generate_script(operations: list[Operation], target: str, source_path: str,
                    source_options: Optional[dict[str, Any]] = None) -> str:
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported export target '{target}'. Supported: {', '.join(SUPPORTED_TARGETS)}")
    imports = ["import polars as pl"]
    load_imports, load_expr = sources.load_statement(source_path, source_options or {})
    body = [f"df = {load_expr}"]
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
