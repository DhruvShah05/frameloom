"""The transformation catalogue.

Every operation is defined once here with:
  * ``apply(lf, params, schema)``  -> LazyFrame   (what the app runs)
  * ``code(params)``               -> list[str]   (what "Export code" writes)
  * ``describe(params)``           -> str         (the default history label)
  * ``normalize(params, schema)``  -> dict        (validation + value casting,
                                                    run once when a step is added)

Keeping all four side by side is what guarantees the exported script does the
same thing as the app.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import polars as pl

from . import sources

# --------------------------------------------------------------- helpers

AGGREGATIONS = ("sum", "mean", "median", "min", "max", "count", "n_unique", "first", "last", "std", "var")
FILL_STRATEGIES = ("value", "forward", "backward", "mean", "median", "min", "max", "zero")
JOIN_TYPES = ("inner", "left", "right", "full", "semi", "anti", "cross")
CAST_TYPES = {
    "Int64": pl.Int64,
    "Int32": pl.Int32,
    "Float64": pl.Float64,
    "Float32": pl.Float32,
    "String": pl.String,
    "Boolean": pl.Boolean,
    "Date": pl.Date,
    "Datetime": pl.Datetime,
    "Time": pl.Time,
    "Categorical": pl.Categorical,
}
TRUE_STRINGS = ("true", "t", "yes", "y", "1")
FALSE_STRINGS = ("false", "f", "no", "n", "0")


def _c(name: str) -> str:
    """Code for pl.col(name)."""
    return f"pl.col({name!r})"


def _as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None and v != ""]
    return [value]


def _require(params: dict, *keys: str, op: str) -> None:
    missing = [k for k in keys if params.get(k) in (None, "", [])]
    if missing:
        raise ValueError(f"{op} needs: {', '.join(missing)}")


def _check_columns(schema: dict | None, cols: list, op: str) -> None:
    if schema is None:
        return
    missing = [c for c in cols if c not in schema]
    if missing:
        raise ValueError(f"{op}: column(s) not found: {', '.join(map(str, missing))}")


def _check_agg(agg: str) -> str:
    if agg not in AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation '{agg}'. Use one of: {', '.join(AGGREGATIONS)}")
    return agg


def _is_numeric(dtype) -> bool:
    return dtype is not None and dtype.is_numeric()


def _is_temporal(dtype) -> bool:
    return dtype is not None and dtype in (pl.Date, pl.Datetime)


def _is_string(dtype) -> bool:
    return dtype is not None and dtype in (pl.String, pl.Categorical)


def cast_value(value: Any, dtype) -> Any:
    """Convert a value typed in the UI into the Python type of a column."""
    if value is None or dtype is None:
        return value
    if isinstance(value, str) and value.strip() == "" and not _is_string(dtype):
        return None
    try:
        if dtype.is_integer():
            if isinstance(value, float) or (isinstance(value, str) and not value.strip().lstrip("+-").isdigit()):
                as_float = float(value)
                if not as_float.is_integer():
                    raise ValueError
                return int(as_float)
            return int(value)
        if dtype.is_float() or dtype.is_decimal():
            return float(value)
        if dtype == pl.Boolean:
            if isinstance(value, str):
                low = value.strip().lower()
                if low in TRUE_STRINGS:
                    return True
                if low in FALSE_STRINGS:
                    return False
                raise ValueError
            return bool(value)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot convert '{value}' to {dtype}")
    if _is_temporal(dtype) or dtype == pl.Time:
        return str(value)
    if _is_string(dtype):
        return str(value)
    return value


def _lit_expr(value: Any, dtype) -> pl.Expr:
    """A literal matching a column's type (dates arrive as strings)."""
    if value is None:
        return pl.lit(None)
    if dtype == pl.Date and isinstance(value, str):
        return pl.lit(value).str.to_date(strict=True)
    if dtype == pl.Datetime and isinstance(value, str):
        return pl.lit(value).str.to_datetime(strict=True)
    return pl.lit(value)


def _lit_code(value: Any, dtype_name: str | None) -> str:
    if value is None:
        return "None"
    if dtype_name == "Date" and isinstance(value, str):
        return f"pl.lit({value!r}).str.to_date()"
    if dtype_name and dtype_name.startswith("Datetime") and isinstance(value, str):
        return f"pl.lit({value!r}).str.to_datetime()"
    return repr(value)


def _dtype_name(dtype) -> str | None:
    if dtype is None:
        return None
    if dtype == pl.Datetime:
        return "Datetime"
    return str(dtype)


# ------------------------------------------------------- filter conditions

FILTER_OPERATORS = (
    "eq", "ne", "gt", "lt", "ge", "le",
    "contains", "not_contains", "starts_with", "ends_with", "regex",
    "in", "not_in", "between", "is_null", "is_not_null",
)
_OP_ALIASES = {"==": "eq", "!=": "ne", ">": "gt", "<": "lt", ">=": "ge", "<=": "le"}
_NO_VALUE_OPS = ("is_null", "is_not_null")
_TEXT_OPS = ("contains", "not_contains", "starts_with", "ends_with", "regex")


def _conditions(params: dict) -> tuple[list[dict], str]:
    """filter_rows accepts either a single condition or a list of them."""
    if params.get("conditions"):
        conds = [dict(c) for c in params["conditions"]]
    else:
        conds = [{k: params.get(k) for k in ("column", "operator", "value", "value2", "ignore_case", "dtype")}]
    combine = (params.get("combine") or "and").lower()
    if combine not in ("and", "or"):
        raise ValueError("filter_rows 'combine' must be 'and' or 'or'")
    for c in conds:
        c["operator"] = _OP_ALIASES.get(c.get("operator") or "eq", c.get("operator") or "eq")
        if c["operator"] not in FILTER_OPERATORS:
            raise ValueError(f"Unsupported filter operator: {c['operator']}")
        if not c.get("column"):
            raise ValueError("filter_rows requires 'column' parameter")
        if c["operator"] not in _NO_VALUE_OPS and c.get("value") in (None, ""):
            if not (c["operator"] in ("eq", "ne") and c.get("value") == ""):
                raise ValueError(f"filter_rows operator '{c['operator']}' requires 'value' parameter")
    return conds, combine


def _cast_filter_value(value: Any, dtype) -> Any:
    """Like cast_value, but lets integer columns compare against decimals."""
    try:
        return cast_value(value, dtype)
    except ValueError:
        if dtype is not None and dtype.is_integer():
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        raise ValueError(f"Cannot convert '{value}' to {dtype}")


def _split_list(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [v.strip() for v in str(value).split(",") if v.strip() != ""]


def _normalize_condition(cond: dict, schema: dict | None) -> dict:
    dtype = schema.get(cond["column"]) if schema else None
    op = cond["operator"]
    out = dict(cond)
    if op in _NO_VALUE_OPS:
        out.pop("value", None)
        out.pop("value2", None)
    elif op in _TEXT_OPS:
        out["value"] = str(cond["value"])
    elif op in ("in", "not_in"):
        out["value"] = [cast_value(v, dtype) for v in _split_list(cond["value"])]
    else:
        out["value"] = _cast_filter_value(cond["value"], dtype)
        if op == "between":
            if cond.get("value2") in (None, ""):
                raise ValueError("'between' needs a second value")
            out["value2"] = _cast_filter_value(cond["value2"], dtype)
    out["dtype"] = _dtype_name(dtype)
    return out


def _condition_expr(cond: dict, schema: dict | None) -> pl.Expr:
    col = cond["column"]
    dtype = schema.get(col) if schema else None
    op = cond["operator"]
    e = pl.col(col)
    if op in _NO_VALUE_OPS:
        return e.is_null() if op == "is_null" else e.is_not_null()
    if op in _TEXT_OPS:
        text = str(cond["value"])
        s = e.cast(pl.String)
        if cond.get("ignore_case"):
            s = s.str.to_lowercase()
            text = text.lower()
        if op == "contains":
            return s.str.contains(text, literal=True)
        if op == "not_contains":
            return ~s.str.contains(text, literal=True)
        if op == "starts_with":
            return s.str.starts_with(text)
        if op == "ends_with":
            return s.str.ends_with(text)
        return s.str.contains(text)
    if op in ("in", "not_in"):
        values = [cast_value(v, dtype) for v in _split_list(cond["value"])]
        if _is_temporal(dtype):
            values = pl.Series(values).str.to_date() if dtype == pl.Date else pl.Series(values).str.to_datetime()
        expr = e.is_in(values)
        return ~expr if op == "not_in" else expr
    val = _lit_expr(_cast_filter_value(cond["value"], dtype), dtype)
    if op == "between":
        val2 = _lit_expr(_cast_filter_value(cond["value2"], dtype), dtype)
        return e.is_between(val, val2)
    return {"eq": e == val, "ne": e != val, "gt": e > val, "lt": e < val, "ge": e >= val, "le": e <= val}[op]


def _condition_code(cond: dict) -> str:
    col = _c(cond["column"])
    op = cond["operator"]
    dt = cond.get("dtype")
    if op == "is_null":
        return f"{col}.is_null()"
    if op == "is_not_null":
        return f"{col}.is_not_null()"
    if op in _TEXT_OPS:
        text = str(cond["value"])
        s = f"{col}.cast(pl.String)"
        if cond.get("ignore_case"):
            s += ".str.to_lowercase()"
            text = text.lower()
        return {
            "contains": f"{s}.str.contains({text!r}, literal=True)",
            "not_contains": f"~{s}.str.contains({text!r}, literal=True)",
            "starts_with": f"{s}.str.starts_with({text!r})",
            "ends_with": f"{s}.str.ends_with({text!r})",
            "regex": f"{s}.str.contains({text!r})",
        }[op]
    if op in ("in", "not_in"):
        values = _split_list(cond["value"])
        if dt == "Date":
            vals = f"pl.Series({values!r}).str.to_date()"
        elif dt and dt.startswith("Datetime"):
            vals = f"pl.Series({values!r}).str.to_datetime()"
        else:
            vals = repr(values)
        return f"{'~' if op == 'not_in' else ''}{col}.is_in({vals})"
    v = _lit_code(cond.get("value"), dt)
    if op == "between":
        return f"{col}.is_between({v}, {_lit_code(cond.get('value2'), dt)})"
    sym = {"eq": "==", "ne": "!=", "gt": ">", "lt": "<", "ge": ">=", "le": "<="}[op]
    return f"{col} {sym} {v}"


def filter_expr(params: dict, schema: dict | None) -> pl.Expr:
    conds, combine = _conditions(params)
    exprs = [_condition_expr(c, schema) for c in conds]
    return pl.all_horizontal(exprs) if combine == "and" else pl.any_horizontal(exprs)


# ------------------------------------------------------------- registry

@dataclass
class OpSpec:
    apply: Callable[[pl.LazyFrame, dict, dict], pl.LazyFrame]
    code: Callable[[dict], list[str]]
    describe: Callable[[dict], str]
    normalize: Callable[[dict, dict | None], dict] = field(default=lambda p, s: p)
    # Expensive steps (or steps that must collect) are cached after they run.
    expensive: bool = False
    imports: tuple[str, ...] = ()


OPS: dict[str, OpSpec] = {}


def register(name: str, **kwargs):
    OPS[name] = OpSpec(**kwargs)


def get_spec(op_type: str) -> OpSpec:
    if op_type not in OPS:
        raise ValueError(f"Unknown operation type: {op_type}")
    return OPS[op_type]


def _names(cols: list) -> str:
    cols = [str(c) for c in cols]
    return ", ".join(cols[:3]) + (f" +{len(cols) - 3}" if len(cols) > 3 else "")


# ---- ingest
register(
    "ingest",
    apply=lambda lf, p, s: lf,
    code=lambda p: [],
    describe=lambda p: "Load Data",
)


# ---- columns
def _drop_norm(p, s):
    cols = _as_list(p.get("columns"))
    if not cols:
        raise ValueError("drop_column requires 'columns' parameter")
    _check_columns(s, cols, "drop_column")
    return {**p, "columns": cols}


register(
    "drop_column",
    apply=lambda lf, p, s: lf.drop(_drop_norm(p, None)["columns"]),
    code=lambda p: [f"df = df.drop({_as_list(p.get('columns'))!r})"],
    describe=lambda p: f"Drop {_names(_as_list(p.get('columns')))}",
    normalize=_drop_norm,
)


def _select_norm(p, s):
    cols = _as_list(p.get("columns"))
    if not cols:
        raise ValueError("select_columns requires 'columns'")
    _check_columns(s, cols, "select_columns")
    return {**p, "columns": cols}


register(
    "select_columns",
    apply=lambda lf, p, s: lf.select(_select_norm(p, None)["columns"]),
    code=lambda p: [f"df = df.select({_as_list(p.get('columns'))!r})"],
    describe=lambda p: f"Keep {_names(_as_list(p.get('columns')))}",
    normalize=_select_norm,
)


def _rename_norm(p, s):
    mapping = p.get("mapping") or {}
    if not mapping and p.get("oldCol") and p.get("newName"):
        mapping = {p["oldCol"]: p["newName"]}
    if not mapping:
        raise ValueError("rename_column requires 'mapping' parameter")
    _check_columns(s, list(mapping), "rename_column")
    if s is not None:
        clashes = [new for old, new in mapping.items() if new in s and new not in mapping]
        if clashes:
            raise ValueError(f"rename_column: a column named {', '.join(clashes)} already exists")
    return {"mapping": mapping}


register(
    "rename_column",
    apply=lambda lf, p, s: lf.rename(_rename_norm(p, None)["mapping"]),
    code=lambda p: [f"df = df.rename({_rename_norm(p, None)['mapping']!r})"],
    describe=lambda p: "Rename " + ", ".join(f"{a} → {b}" for a, b in _rename_norm(p, None)["mapping"].items()),
    normalize=_rename_norm,
)


def _cast_expr(col: str, target: str, src_dtype, fmt: str | None, clean: bool) -> pl.Expr:
    e = pl.col(col)
    if target not in CAST_TYPES:
        raise ValueError(f"Unsupported type '{target}'. Use one of: {', '.join(CAST_TYPES)}")
    is_str = _is_string(src_dtype) or src_dtype is None
    if clean and is_str and target in ("Int64", "Int32", "Float64", "Float32"):
        e = e.cast(pl.String).str.replace_all(r"[^0-9eE.+\-]", "")
    if is_str and target == "Date":
        return e.cast(pl.String).str.to_date(fmt or None, strict=False)
    if is_str and target == "Datetime":
        return e.cast(pl.String).str.to_datetime(fmt or None, strict=False)
    if is_str and target == "Boolean":
        low = e.cast(pl.String).str.strip_chars().str.to_lowercase()
        return pl.when(low.is_in(TRUE_STRINGS)).then(True).when(low.is_in(FALSE_STRINGS)).then(False).otherwise(None)
    return e.cast(CAST_TYPES[target], strict=False)


def _cast_code(col: str, target: str, src: str | None, fmt: str | None, clean: bool) -> str:
    e = _c(col)
    is_str = src in (None, "String", "Categorical")
    if clean and is_str and target in ("Int64", "Int32", "Float64", "Float32"):
        e += ".cast(pl.String).str.replace_all(r'[^0-9eE.+\\-]', '')"
    if is_str and target == "Date":
        return f"{e}.cast(pl.String).str.to_date({fmt!r}, strict=False)" if fmt else f"{e}.cast(pl.String).str.to_date(strict=False)"
    if is_str and target == "Datetime":
        return f"{e}.cast(pl.String).str.to_datetime({fmt!r}, strict=False)" if fmt else f"{e}.cast(pl.String).str.to_datetime(strict=False)"
    if is_str and target == "Boolean":
        low = f"{e}.cast(pl.String).str.strip_chars().str.to_lowercase()"
        return f"pl.when({low}.is_in({TRUE_STRINGS!r})).then(True).when({low}.is_in({FALSE_STRINGS!r})).then(False).otherwise(None)"
    return f"{e}.cast(pl.{target}, strict=False)"


def _cast_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    if not cols or not p.get("dtype"):
        raise ValueError("cast_column needs: columns, dtype")
    if p["dtype"] not in CAST_TYPES:
        raise ValueError(f"Unsupported type '{p['dtype']}'. Use one of: {', '.join(CAST_TYPES)}")
    _check_columns(s, cols, "cast_column")
    src = {c: _dtype_name(s.get(c)) for c in cols} if s is not None else p.get("source_dtypes", {})
    return {"columns": cols, "dtype": p["dtype"], "format": p.get("format") or None,
            "clean": bool(p.get("clean")), "source_dtypes": src}


register(
    "cast_column",
    apply=lambda lf, p, s: lf.with_columns(
        [_cast_expr(c, p["dtype"], s.get(c), p.get("format"), p.get("clean", False)).alias(c) for c in _cast_norm(p, None)["columns"]]
    ),
    code=lambda p: [
        "df = df.with_columns(["
        + ", ".join(f"{_cast_code(c, p['dtype'], (p.get('source_dtypes') or {}).get(c), p.get('format'), p.get('clean', False))}.alias({c!r})" for c in _as_list(p.get("columns") or p.get("column")))
        + "])"
    ],
    describe=lambda p: f"Cast {_names(_as_list(p.get('columns') or p.get('column')))} → {p.get('dtype')}",
    normalize=_cast_norm,
)


def _add_norm(p, s):
    _require(p, "name", "expression", op="add_column")
    try:
        pl.sql_expr(p["expression"])
    except Exception as exc:
        raise ValueError(f"Invalid expression: {exc}")
    return {"name": p["name"], "expression": p["expression"]}


register(
    "add_column",
    apply=lambda lf, p, s: lf.with_columns(pl.sql_expr(p["expression"]).alias(p["name"])),
    code=lambda p: [f"df = df.with_columns(pl.sql_expr({p['expression']!r}).alias({p['name']!r}))"],
    describe=lambda p: f"Add {p.get('name')} = {p.get('expression')}",
    normalize=_add_norm,
)


# ---- rows
def _sort_keys(p) -> tuple[list[str], list[bool]]:
    by = p.get("by")
    if by:
        cols = [b["column"] if isinstance(b, dict) else b for b in by]
        desc = [bool(b.get("descending")) if isinstance(b, dict) else bool(p.get("descending")) for b in by]
    else:
        cols = _as_list(p.get("columns") or p.get("column"))
        desc = [bool(p.get("descending"))] * len(cols)
    if not cols:
        raise ValueError("sort needs at least one column")
    return cols, desc


def _sort_norm(p, s):
    cols, desc = _sort_keys(p)
    _check_columns(s, cols, "sort")
    return {"by": [{"column": c, "descending": d} for c, d in zip(cols, desc)]}


def _sort_apply(lf, p, s):
    cols, desc = _sort_keys(p)
    return lf.sort(cols, descending=desc, nulls_last=True, maintain_order=True)


register(
    "sort",
    apply=_sort_apply,
    code=lambda p: [f"df = df.sort({_sort_keys(p)[0]!r}, descending={_sort_keys(p)[1]!r}, nulls_last=True, maintain_order=True)"],
    describe=lambda p: "Sort by " + ", ".join(f"{c}{' ↓' if d else ' ↑'}" for c, d in zip(*_sort_keys(p))),
    normalize=_sort_norm,
)


def _filter_norm(p, s):
    conds, combine = _conditions(p)
    for c in conds:
        _check_columns(s, [c["column"]], "filter_rows")
    conds = [_normalize_condition(c, s) for c in conds]
    if len(conds) == 1 and not p.get("conditions"):
        return {**conds[0]}
    return {"conditions": conds, "combine": combine}


def _filter_code(p):
    conds, combine = _conditions(p)
    parts = [_condition_code(c) for c in conds]
    if len(parts) == 1:
        return [f"df = df.filter({parts[0]})"]
    joiner = "pl.all_horizontal" if combine == "and" else "pl.any_horizontal"
    return [f"df = df.filter({joiner}([{', '.join(parts)}]))"]


_SYM = {"eq": "=", "ne": "≠", "gt": ">", "lt": "<", "ge": "≥", "le": "≤", "is_null": "is empty", "is_not_null": "is not empty",
        "not_contains": "doesn't contain", "starts_with": "starts with", "ends_with": "ends with", "not_in": "not in"}


def _fmt(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


def _filter_describe(p):
    conds, combine = _conditions(p)
    parts = []
    for c in conds:
        op = c["operator"]
        val = "" if op in _NO_VALUE_OPS else f" {_fmt(c.get('value'))}" + (f"–{_fmt(c.get('value2'))}" if op == "between" else "")
        parts.append(f"{c['column']} {_SYM.get(op, op)}{val}")
    return "Filter " + f" {combine.upper()} ".join(parts)


register(
    "filter_rows",
    apply=lambda lf, p, s: lf.filter(filter_expr(p, s)),
    code=_filter_code,
    describe=_filter_describe,
    normalize=_filter_norm,
)


def _limit_norm(p, s):
    _require(p, "n", op="limit_rows")
    n = int(p["n"])
    if n < 0:
        raise ValueError("limit_rows: n must be positive")
    by = p.get("by") or None
    if by:
        _check_columns(s, [by], "limit_rows")
    return {"n": n, "by": by, "descending": bool(p.get("descending", True))}


def _limit_apply(lf, p, s):
    if p.get("by"):
        lf = lf.sort(p["by"], descending=bool(p.get("descending", True)), nulls_last=True, maintain_order=True)
    return lf.head(int(p["n"]))


def _limit_code(p):
    if p.get("by"):
        return [f"df = df.sort({p['by']!r}, descending={bool(p.get('descending', True))!r}, nulls_last=True, maintain_order=True).head({int(p['n'])})"]
    return [f"df = df.head({int(p['n'])})"]


register(
    "limit_rows",
    apply=_limit_apply,
    code=_limit_code,
    describe=lambda p: (f"Top {p.get('n')} by {p['by']}{' ↓' if p.get('descending', True) else ' ↑'}" if p.get("by") else f"First {p.get('n')} rows"),
    normalize=_limit_norm,
)


def _sample_norm(p, s):
    if p.get("n") in (None, "") and p.get("fraction") in (None, ""):
        raise ValueError("sample_rows needs n or fraction")
    out = {"seed": int(p.get("seed") if p.get("seed") not in (None, "") else 42)}
    if p.get("n") not in (None, ""):
        out["n"] = int(p["n"])
    else:
        frac = float(p["fraction"])
        if not 0 < frac <= 1:
            raise ValueError("sample_rows fraction must be between 0 and 1")
        out["fraction"] = frac
    return out


def _sample_apply(lf, p, s):
    df = lf.collect()
    if "n" in p:
        return df.sample(n=min(int(p["n"]), df.height), seed=p.get("seed", 42)).lazy()
    return df.sample(fraction=float(p["fraction"]), seed=p.get("seed", 42)).lazy()


register(
    "sample_rows",
    apply=_sample_apply,
    code=lambda p: [f"df = df.sample(n=min({int(p['n'])}, df.height), seed={p.get('seed', 42)})" if "n" in p
                    else f"df = df.sample(fraction={float(p['fraction'])}, seed={p.get('seed', 42)})"],
    describe=lambda p: f"Sample {p['n']} rows" if p.get("n") not in (None, "") else f"Sample {float(p.get('fraction', 0)) * 100:g}%",
    normalize=_sample_norm,
    expensive=True,
)


def _dedupe_norm(p, s):
    cols = _as_list(p.get("columns"))
    _check_columns(s, cols, "drop_duplicates")
    keep = p.get("keep") or "first"
    if keep not in ("first", "last", "any", "none"):
        raise ValueError("drop_duplicates keep must be first, last, any or none")
    return {"columns": cols, "keep": keep}


register(
    "drop_duplicates",
    apply=lambda lf, p, s: lf.unique(subset=_as_list(p.get("columns")) or None, keep=p.get("keep") or "first", maintain_order=True),
    code=lambda p: [f"df = df.unique(subset={(_as_list(p.get('columns')) or None)!r}, keep={(p.get('keep') or 'first')!r}, maintain_order=True)"],
    describe=lambda p: "Remove duplicates" + (f" on {_names(_as_list(p.get('columns')))}" if _as_list(p.get("columns")) else ""),
    normalize=_dedupe_norm,
)


_MATCH_SCALAR = ("Int", "UInt", "Float", "String", "Boolean", "Date", "Datetime", "Categorical", "Enum")


def _matchable(dtype_name: str | None) -> bool:
    return bool(dtype_name) and dtype_name.startswith(_MATCH_SCALAR)


def _edit_norm(p, s):
    """An edit remembers the row's original values, not its position.

    Replaying after earlier steps change (a new filter, a different sort)
    still finds the same row; if it no longer exists, replay fails loudly.
    Older edits saved with only ``row_index`` keep working positionally.
    """
    if not p.get("column"):
        raise ValueError("edit_cell needs a column")
    _check_columns(s, [p["column"]], "edit_cell")
    dtype = s.get(p["column"]) if s is not None else None
    out = {"column": p["column"], "value": cast_value(p.get("value"), dtype), "dtype": _dtype_name(dtype)}
    if p.get("match") is not None or p.get("row") is not None:
        raw = p.get("match") if p.get("match") is not None else p["row"]
        match, dtypes = {}, {}
        for col, val in raw.items():
            if s is not None and col not in s:
                continue
            dname = _dtype_name(s.get(col)) if s is not None else (p.get("match_dtypes") or {}).get(col)
            if not _matchable(dname):
                continue
            match[col] = cast_value(val, s.get(col)) if s is not None else val
            dtypes[col] = dname
        if not match:
            raise ValueError("edit_cell: the row has no columns that can identify it")
        out.update(match=match, match_dtypes=dtypes)
    elif p.get("row_index") not in (None, ""):
        out["row_index"] = int(p["row_index"])
    else:
        raise ValueError("edit_cell needs the row (its values) or a row_index")
    return out


def _match_lit(value, dname):
    if value is None:
        return pl.lit(None)
    if dname == "Date" and isinstance(value, str):
        return pl.lit(value).str.to_date()
    if dname and dname.startswith("Datetime") and isinstance(value, str):
        return pl.lit(value).str.to_datetime()
    return pl.lit(value)


def edit_match_expr(p) -> pl.Expr:
    dtypes = p.get("match_dtypes") or {}
    return pl.all_horizontal([pl.col(c).eq_missing(_match_lit(v, dtypes.get(c))) for c, v in p["match"].items()])


def _edit_apply(lf, p, s):
    col = p["column"]
    dtype = s.get(col)
    value = _lit_expr(cast_value(p.get("value"), dtype), dtype)
    if dtype is not None:
        value = value.cast(dtype, strict=False)
    cond = edit_match_expr(p) if p.get("match") is not None else pl.int_range(pl.len()) == int(p["row_index"])
    return lf.with_columns(pl.when(cond).then(value).otherwise(pl.col(col)).alias(col))


def _match_lit_code(value, dname):
    if value is None:
        return "None"
    if dname == "Date" and isinstance(value, str):
        return f"pl.lit({value!r}).str.to_date()"
    if dname and dname.startswith("Datetime") and isinstance(value, str):
        return f"pl.lit({value!r}).str.to_datetime()"
    return repr(value)


def _edit_code(p):
    col = p["column"]
    dt = p.get("dtype")
    value = _lit_code(p.get("value"), dt)
    if value != "None" and dt in CAST_TYPES:
        value = f"pl.lit({value}).cast(pl.{dt})" if not value.startswith("pl.") else f"{value}.cast(pl.{dt})"
    elif value == "None":
        value = "pl.lit(None)"
    if p.get("match") is not None:
        dtypes = p.get("match_dtypes") or {}
        conds = ", ".join(f"{_c(c)}.eq_missing({_match_lit_code(v, dtypes.get(c))})" for c, v in p["match"].items())
        cond = f"pl.all_horizontal([{conds}])"
    else:
        cond = f"pl.int_range(pl.len()) == {int(p['row_index'])}"
    return [f"df = df.with_columns(pl.when({cond}).then({value}).otherwise({_c(col)}).alias({col!r}))"]


def _edit_describe(p):
    if p.get("match"):
        key, val = next(iter(p["match"].items()))
        where = f"where {key}={val}"
    else:
        where = f"row {int(p.get('row_index', 0)) + 1}"
    return f"Edit {p.get('column')} {where} → {p.get('value')}"


register(
    "edit_cell",
    apply=_edit_apply,
    code=_edit_code,
    describe=_edit_describe,
    normalize=_edit_norm,
)


# ---- text
TEXT_TRANSFORMS = {
    "lower": (lambda e: e.str.to_lowercase(), ".str.to_lowercase()"),
    "upper": (lambda e: e.str.to_uppercase(), ".str.to_uppercase()"),
    "title": (lambda e: e.str.to_titlecase(), ".str.to_titlecase()"),
    "strip": (lambda e: e.str.strip_chars(), ".str.strip_chars()"),
    "strip_start": (lambda e: e.str.strip_chars_start(), ".str.strip_chars_start()"),
    "strip_end": (lambda e: e.str.strip_chars_end(), ".str.strip_chars_end()"),
    "collapse_spaces": (lambda e: e.str.replace_all(r"\s+", " ").str.strip_chars(), ".str.replace_all(r'\\s+', ' ').str.strip_chars()"),
    "length": (lambda e: e.str.len_chars(), ".str.len_chars()"),
}


def _text_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    if not cols or p.get("transform") not in TEXT_TRANSFORMS:
        raise ValueError(f"text_transform needs columns and a transform ({', '.join(TEXT_TRANSFORMS)})")
    _check_columns(s, cols, "text_transform")
    new = p.get("new_column") or None
    if new and len(cols) > 1:
        raise ValueError("text_transform: new_column only works with one column")
    return {"columns": cols, "transform": p["transform"], "new_column": new}


def _text_apply(lf, p, s):
    fn = TEXT_TRANSFORMS[p["transform"]][0]
    cols = _as_list(p.get("columns") or p.get("column"))
    return lf.with_columns([fn(pl.col(c).cast(pl.String)).alias(p.get("new_column") or c) for c in cols])


def _text_code(p):
    suffix = TEXT_TRANSFORMS[p["transform"]][1]
    cols = _as_list(p.get("columns") or p.get("column"))
    exprs = ", ".join(f"{_c(c)}.cast(pl.String){suffix}.alias({(p.get('new_column') or c)!r})" for c in cols)
    return [f"df = df.with_columns([{exprs}])"]


register(
    "text_transform",
    apply=_text_apply,
    code=_text_code,
    describe=lambda p: f"{str(p.get('transform', '')).replace('_', ' ').title()} {_names(_as_list(p.get('columns') or p.get('column')))}",
    normalize=_text_norm,
)


def _split_norm(p, s):
    _require(p, "column", "delimiter", op="split_column")
    _check_columns(s, [p["column"]], "split_column")
    names = _split_list(p.get("names")) if p.get("names") else []
    parts = int(p.get("parts") or len(names) or 2)
    if parts < 2:
        raise ValueError("split_column needs at least 2 parts")
    if not names:
        names = [f"{p['column']}_{i + 1}" for i in range(parts)]
    if len(names) != parts:
        raise ValueError(f"split_column: {parts} parts but {len(names)} names")
    if s is not None:
        clashes = [n for n in names if n in s and n != p["column"]]
        if clashes:
            raise ValueError(f"split_column: column(s) already exist: {', '.join(clashes)}")
    return {"column": p["column"], "delimiter": p["delimiter"], "parts": parts, "names": names,
            "drop_original": bool(p.get("drop_original"))}


def _split_apply(lf, p, s):
    col, names = p["column"], p["names"]
    lf = lf.with_columns(
        pl.col(col).cast(pl.String).str.split_exact(p["delimiter"], len(names) - 1).struct.rename_fields(names).alias("__loom_split")
    ).unnest("__loom_split")
    return lf.drop(col) if p.get("drop_original") else lf


def _split_code(p):
    col, names = p["column"], p["names"]
    lines = [f"df = df.with_columns({_c(col)}.cast(pl.String).str.split_exact({p['delimiter']!r}, {len(names) - 1}).struct.rename_fields({names!r}).alias('__loom_split')).unnest('__loom_split')"]
    if p.get("drop_original"):
        lines.append(f"df = df.drop({col!r})")
    return lines


register(
    "split_column",
    apply=_split_apply,
    code=_split_code,
    describe=lambda p: f"Split {p.get('column')} on {p.get('delimiter')!r}",
    normalize=_split_norm,
)


def _regex_check(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}")


def _extract_norm(p, s):
    _require(p, "column", "pattern", op="regex_extract")
    _check_columns(s, [p["column"]], "regex_extract")
    _regex_check(p["pattern"])
    return {"column": p["column"], "pattern": p["pattern"], "group": int(p.get("group") or 1),
            "new_column": p.get("new_column") or f"{p['column']}_extract"}


register(
    "regex_extract",
    apply=lambda lf, p, s: lf.with_columns(pl.col(p["column"]).cast(pl.String).str.extract(p["pattern"], int(p.get("group", 1))).alias(p["new_column"])),
    code=lambda p: [f"df = df.with_columns({_c(p['column'])}.cast(pl.String).str.extract({p['pattern']!r}, {int(p.get('group', 1))}).alias({p['new_column']!r}))"],
    describe=lambda p: f"Extract {p.get('pattern')} from {p.get('column')}",
    normalize=_extract_norm,
)


def _replace_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    if not cols or p.get("find") is None:
        raise ValueError("find_replace needs columns and find")
    _check_columns(s, cols, "find_replace")
    mode = p.get("mode") or ("regex" if p.get("regex") else "contains")
    if mode not in ("contains", "regex", "whole_cell"):
        raise ValueError("find_replace mode must be contains, regex or whole_cell")
    if mode == "regex":
        _regex_check(p["find"])
    if s is not None and mode != "whole_cell":
        non_text = [c for c in cols if not _is_string(s.get(c))]
        if non_text:
            mode_msg = "Use 'whole cell' mode for non-text columns"
            raise ValueError(f"find_replace: {', '.join(non_text)} is not text. {mode_msg}.")
    dtypes = {c: _dtype_name(s.get(c)) for c in cols} if s is not None else p.get("dtypes", {})
    find, replace = p["find"], p.get("replace", "")
    if mode == "whole_cell" and s is not None:
        find = {c: cast_value(_pick(find, c), s.get(c)) for c in cols}
        replace = {c: cast_value(_pick(replace, c), s.get(c)) for c in cols}
    return {"columns": cols, "find": find, "replace": replace, "mode": mode, "dtypes": dtypes}


def _pick(v, col):
    return v.get(col) if isinstance(v, dict) else v


def _replace_apply(lf, p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    mode = p.get("mode", "contains")
    exprs = []
    for c in cols:
        find, rep = _pick(p["find"], c), _pick(p.get("replace", ""), c)
        if mode == "whole_cell":
            dtype = s.get(c)
            find_v = cast_value(find, dtype)
            rep_e = _lit_expr(cast_value(rep, dtype), dtype)
            cond = pl.col(c).is_null() if find_v is None else pl.col(c) == _lit_expr(find_v, dtype)
            exprs.append(pl.when(cond).then(rep_e).otherwise(pl.col(c)).alias(c))
        else:
            exprs.append(pl.col(c).str.replace_all(str(find), str(rep), literal=(mode == "contains")).alias(c))
    return lf.with_columns(exprs)


def _replace_code(p):
    cols = _as_list(p.get("columns") or p.get("column"))
    mode = p.get("mode", "contains")
    dtypes = p.get("dtypes") or {}
    exprs = []
    for c in cols:
        find, rep = _pick(p["find"], c), _pick(p.get("replace", ""), c)
        if mode == "whole_cell":
            dt = dtypes.get(c)
            cond = f"{_c(c)}.is_null()" if find is None else f"{_c(c)} == {_lit_code(find, dt)}"
            rep_code = _lit_code(rep, dt)
            if not rep_code.startswith("pl."):
                rep_code = f"pl.lit({rep_code})"
            exprs.append(f"pl.when({cond}).then({rep_code}).otherwise({_c(c)}).alias({c!r})")
        else:
            exprs.append(f"{_c(c)}.str.replace_all({str(find)!r}, {str(rep)!r}, literal={mode == 'contains'!r}).alias({c!r})")
    return [f"df = df.with_columns([{', '.join(exprs)}])"]


register(
    "find_replace",
    apply=_replace_apply,
    code=_replace_code,
    describe=lambda p: f"Replace {_pick(p.get('find'), (_as_list(p.get('columns') or p.get('column')) or [''])[0])!r} in {_names(_as_list(p.get('columns') or p.get('column')))}",
    normalize=_replace_norm,
)


def _dates_norm(p, s):
    _require(p, "column", op="parse_dates")
    _check_columns(s, [p["column"]], "parse_dates")
    to = p.get("to") or "datetime"
    if to not in ("date", "datetime"):
        raise ValueError("parse_dates 'to' must be date or datetime")
    return {"column": p["column"], "to": to, "format": p.get("format") or None, "new_column": p.get("new_column") or None}


def _dates_apply(lf, p, s):
    e = pl.col(p["column"]).cast(pl.String)
    e = e.str.to_date(p.get("format"), strict=False) if p.get("to") == "date" else e.str.to_datetime(p.get("format"), strict=False)
    return lf.with_columns(e.alias(p.get("new_column") or p["column"]))


def _dates_code(p):
    fn = "to_date" if p.get("to") == "date" else "to_datetime"
    fmt = f"{p['format']!r}, " if p.get("format") else ""
    return [f"df = df.with_columns({_c(p['column'])}.cast(pl.String).str.{fn}({fmt}strict=False).alias({(p.get('new_column') or p['column'])!r}))"]


register(
    "parse_dates",
    apply=_dates_apply,
    code=_dates_code,
    describe=lambda p: f"Parse {p.get('column')} as {p.get('to', 'datetime')}",
    normalize=_dates_norm,
)


# ---- cleaning
def _fill_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    strategy = p.get("strategy") or "value"
    if not cols:
        raise ValueError("fill_missing needs columns")
    if strategy not in FILL_STRATEGIES:
        raise ValueError(f"fill_missing strategy must be one of: {', '.join(FILL_STRATEGIES)}")
    _check_columns(s, cols, "fill_missing")
    out = {"columns": cols, "strategy": strategy}
    if strategy == "value":
        if p.get("value") is None:
            raise ValueError("fill_missing with strategy 'value' needs a value")
        out["value"] = {c: cast_value(_pick(p["value"], c), s.get(c)) for c in cols} if s is not None else p["value"]
        out["dtypes"] = {c: _dtype_name(s.get(c)) for c in cols} if s is not None else p.get("dtypes", {})
    if strategy in ("mean", "median") and s is not None:
        bad = [c for c in cols if not _is_numeric(s.get(c))]
        if bad:
            raise ValueError(f"fill_missing {strategy} needs numeric columns; not numeric: {', '.join(bad)}")
    return out


def _fill_apply(lf, p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    strategy = p.get("strategy", "value")
    exprs = []
    for c in cols:
        e = pl.col(c)
        if strategy == "value":
            dtype = s.get(c)
            exprs.append(e.fill_null(_lit_expr(cast_value(_pick(p["value"], c), dtype), dtype)).alias(c))
        elif strategy == "median":
            exprs.append(e.fill_null(e.median()).alias(c))
        else:
            exprs.append(e.fill_null(strategy=strategy).alias(c))
    return lf.with_columns(exprs)


def _fill_code(p):
    cols = _as_list(p.get("columns") or p.get("column"))
    strategy = p.get("strategy", "value")
    dtypes = p.get("dtypes") or {}
    exprs = []
    for c in cols:
        if strategy == "value":
            exprs.append(f"{_c(c)}.fill_null({_lit_code(_pick(p['value'], c), dtypes.get(c))}).alias({c!r})")
        elif strategy == "median":
            exprs.append(f"{_c(c)}.fill_null({_c(c)}.median()).alias({c!r})")
        else:
            exprs.append(f"{_c(c)}.fill_null(strategy={strategy!r}).alias({c!r})")
    return [f"df = df.with_columns([{', '.join(exprs)}])"]


register(
    "fill_missing",
    apply=_fill_apply,
    code=_fill_code,
    describe=lambda p: f"Fill empty {_names(_as_list(p.get('columns') or p.get('column')))} ({p.get('strategy', 'value')})",
    normalize=_fill_norm,
)


def _dropna_norm(p, s):
    cols = _as_list(p.get("columns"))
    _check_columns(s, cols, "drop_missing")
    how = p.get("how") or "any"
    if how not in ("any", "all"):
        raise ValueError("drop_missing how must be any or all")
    return {"columns": cols, "how": how}


def _dropna_apply(lf, p, s):
    cols = _as_list(p.get("columns"))
    if p.get("how", "any") == "any":
        return lf.drop_nulls(subset=cols or None)
    target = [pl.col(c).is_null() for c in cols] if cols else [pl.all().is_null()]
    return lf.filter(~pl.all_horizontal(target))


def _dropna_code(p):
    cols = _as_list(p.get("columns"))
    if p.get("how", "any") == "any":
        return [f"df = df.drop_nulls(subset={(cols or None)!r})"]
    target = ", ".join(f"{_c(c)}.is_null()" for c in cols) if cols else "pl.all().is_null()"
    return [f"df = df.filter(~pl.all_horizontal([{target}]))"]


register(
    "drop_missing",
    apply=_dropna_apply,
    code=_dropna_code,
    describe=lambda p: f"Drop rows with {'any' if p.get('how', 'any') == 'any' else 'all'} empty" + (f" in {_names(_as_list(p.get('columns')))}" if _as_list(p.get("columns")) else ""),
    normalize=_dropna_norm,
)


def _bin_norm(p, s):
    _require(p, "column", op="bin_column")
    _check_columns(s, [p["column"]], "bin_column")
    if s is not None and not _is_numeric(s.get(p["column"])):
        raise ValueError(f"bin_column needs a numeric column; {p['column']} is {s.get(p['column'])}")
    method = p.get("method") or "breaks"
    out = {"column": p["column"], "method": method, "new_column": p.get("new_column") or f"{p['column']}_bin"}
    labels = _split_list(p["labels"]) if p.get("labels") else None
    if method == "breaks":
        breaks = sorted(float(b) for b in _split_list(p.get("breaks") or []))
        if not breaks:
            raise ValueError("bin_column needs breaks, e.g. 0, 18, 65")
        out["breaks"] = breaks
        if labels and len(labels) != len(breaks) + 1:
            raise ValueError(f"bin_column: {len(breaks)} breaks need {len(breaks) + 1} labels")
    elif method == "quantiles":
        out["bins"] = int(p.get("bins") or 4)
        if out["bins"] < 2:
            raise ValueError("bin_column needs at least 2 bins")
        if labels and len(labels) != out["bins"]:
            raise ValueError(f"bin_column: {out['bins']} bins need {out['bins']} labels")
    else:
        raise ValueError("bin_column method must be breaks or quantiles")
    out["labels"] = labels
    return out


def _bin_apply(lf, p, s):
    e = pl.col(p["column"])
    if p.get("method", "breaks") == "breaks":
        e = e.cut(p["breaks"], labels=p.get("labels"))
    else:
        e = e.qcut(int(p["bins"]), labels=p.get("labels"), allow_duplicates=True)
    return lf.with_columns(e.cast(pl.String).alias(p["new_column"]))


def _bin_code(p):
    if p.get("method", "breaks") == "breaks":
        e = f"{_c(p['column'])}.cut({p['breaks']!r}, labels={p.get('labels')!r})"
    else:
        e = f"{_c(p['column'])}.qcut({int(p['bins'])}, labels={p.get('labels')!r}, allow_duplicates=True)"
    return [f"df = df.with_columns({e}.cast(pl.String).alias({p['new_column']!r}))"]


register(
    "bin_column",
    apply=_bin_apply,
    code=_bin_code,
    describe=lambda p: f"Bin {p.get('column')}" + (f" at {p.get('breaks')}" if p.get("method", "breaks") == "breaks" else f" into {p.get('bins')} quantiles"),
    normalize=_bin_norm,
)


# ---- reshaping
def _agg_expr(a: dict) -> pl.Expr:
    agg = _check_agg(a.get("agg") or "count")
    col = a.get("column")
    alias = a.get("alias") or (f"{col}_{agg}" if col else agg)
    if agg == "count" and not col:
        return pl.len().alias(alias)
    return getattr(pl.col(col), agg)().alias(alias)


def _agg_code(a: dict) -> str:
    agg = a.get("agg") or "count"
    col = a.get("column")
    alias = a.get("alias") or (f"{col}_{agg}" if col else agg)
    if agg == "count" and not col:
        return f"pl.len().alias({alias!r})"
    return f"{_c(col)}.{agg}().alias({alias!r})"


def _group_norm(p, s):
    by = _as_list(p.get("by"))
    aggs = [dict(a) for a in (p.get("aggs") or []) if a]
    if not by:
        raise ValueError("group_by needs at least one 'by' column")
    if not aggs:
        aggs = [{"agg": "count"}]
    for a in aggs:
        _check_agg(a.get("agg") or "count")
        if a.get("agg", "count") != "count" and not a.get("column"):
            raise ValueError(f"group_by: aggregation '{a.get('agg')}' needs a column")
    _check_columns(s, by + [a["column"] for a in aggs if a.get("column")], "group_by")
    aliases = [a.get("alias") or (f"{a['column']}_{a.get('agg', 'count')}" if a.get("column") else a.get("agg", "count")) for a in aggs]
    if len(set(aliases)) != len(aliases):
        raise ValueError("group_by: two aggregations produce the same column name; give them aliases")
    return {"by": by, "aggs": [{"column": a.get("column") or None, "agg": a.get("agg") or "count", "alias": al} for a, al in zip(aggs, aliases)]}


register(
    "group_by",
    apply=lambda lf, p, s: lf.group_by(_as_list(p["by"]), maintain_order=True).agg([_agg_expr(a) for a in p.get("aggs") or [{"agg": "count"}]]),
    code=lambda p: [f"df = df.group_by({_as_list(p['by'])!r}, maintain_order=True).agg([{', '.join(_agg_code(a) for a in p.get('aggs') or [{'agg': 'count'}])}])"],
    describe=lambda p: f"Group by {_names(_as_list(p.get('by')))}",
    normalize=_group_norm,
    expensive=True,
)

PIVOT_AGGS = {"first": "first", "last": "last", "sum": "sum", "mean": "mean", "median": "median",
              "min": "min", "max": "max", "count": "len", "len": "len"}


def _pivot_norm(p, s):
    _require(p, "on", "values", op="pivot")
    index = _as_list(p.get("index"))
    if not index:
        raise ValueError("pivot needs at least one index (row) column")
    agg = p.get("agg") or "first"
    if agg not in PIVOT_AGGS:
        raise ValueError(f"pivot agg must be one of: {', '.join(PIVOT_AGGS)}")
    _check_columns(s, index + [p["on"], p["values"]], "pivot")
    return {"index": index, "on": p["on"], "values": p["values"], "agg": agg}


register(
    "pivot",
    apply=lambda lf, p, s: lf.collect().pivot(on=p["on"], index=_as_list(p["index"]), values=p["values"],
                                              aggregate_function=PIVOT_AGGS[p.get("agg", "first")], sort_columns=True).lazy(),
    code=lambda p: [f"df = df.pivot(on={p['on']!r}, index={_as_list(p['index'])!r}, values={p['values']!r}, aggregate_function={PIVOT_AGGS[p.get('agg', 'first')]!r}, sort_columns=True)"],
    describe=lambda p: f"Pivot {p.get('on')} ({p.get('agg', 'first')} of {p.get('values')})",
    normalize=_pivot_norm,
    expensive=True,
)


def _unpivot_norm(p, s):
    index = _as_list(p.get("index"))
    on = _as_list(p.get("on"))
    if not index and not on:
        raise ValueError("unpivot needs index columns (kept) or 'on' columns (turned into rows)")
    _check_columns(s, index + on, "unpivot")
    return {"index": index, "on": on or None, "variable_name": p.get("variable_name") or "variable",
            "value_name": p.get("value_name") or "value"}


register(
    "unpivot",
    apply=lambda lf, p, s: lf.unpivot(on=p.get("on") or None, index=_as_list(p.get("index")) or None,
                                      variable_name=p.get("variable_name", "variable"), value_name=p.get("value_name", "value")),
    code=lambda p: [f"df = df.unpivot(on={(p.get('on') or None)!r}, index={(_as_list(p.get('index')) or None)!r}, variable_name={p.get('variable_name', 'variable')!r}, value_name={p.get('value_name', 'value')!r})"],
    describe=lambda p: f"Unpivot keeping {_names(_as_list(p.get('index')))}" if _as_list(p.get("index")) else f"Unpivot {_names(_as_list(p.get('on')))}",
    normalize=_unpivot_norm,
)


def _other_source(p) -> tuple[str, dict]:
    path = p.get("path")
    if not path:
        raise ValueError("A file path is required")
    if not sources.source_exists(path):
        raise ValueError(f"File not found: {path}")
    return path, dict(p.get("options") or {})


def _join_norm(p, s):
    path, opts = _other_source(p)
    how = p.get("how") or "left"
    if how not in JOIN_TYPES:
        raise ValueError(f"join how must be one of: {', '.join(JOIN_TYPES)}")
    left_on = _as_list(p.get("left_on") or p.get("on"))
    right_on = _as_list(p.get("right_on") or p.get("on")) or left_on
    if how != "cross":
        if not left_on:
            raise ValueError("join needs key columns")
        if len(left_on) != len(right_on):
            raise ValueError("join needs the same number of left and right keys")
        _check_columns(s, left_on, "join (left)")
        right_schema = sources.load_source(path, opts).collect_schema()
        _check_columns(dict(right_schema), right_on, "join (right file)")
    return {"path": path, "options": opts, "how": how, "left_on": left_on, "right_on": right_on,
            "suffix": p.get("suffix") or "_right"}


def _join_apply(lf, p, s):
    right = sources.load_source(p["path"], p.get("options") or {})
    if p.get("how") == "cross":
        return lf.join(right, how="cross", suffix=p.get("suffix", "_right"))
    return lf.join(right, left_on=p["left_on"], right_on=p["right_on"], how=p.get("how", "left"),
                   suffix=p.get("suffix", "_right"), coalesce=True)


def _join_code(p):
    imports, load = sources.load_statement(p["path"], p.get("options") or {})
    lines = [*(f"{i}  # for the joined file" for i in imports), f"right = {load}"]
    if p.get("how") == "cross":
        lines.append(f"df = df.join(right, how='cross', suffix={p.get('suffix', '_right')!r})")
    else:
        lines.append(f"df = df.join(right, left_on={p['left_on']!r}, right_on={p['right_on']!r}, how={p.get('how', 'left')!r}, suffix={p.get('suffix', '_right')!r}, coalesce=True)")
    return lines


register(
    "join",
    apply=_join_apply,
    code=_join_code,
    describe=lambda p: f"{str(p.get('how', 'left')).title()} join {str(p.get('path', '')).rsplit('/', 1)[-1]}" + (f" on {_names(_as_list(p.get('left_on')))}" if p.get("left_on") else ""),
    normalize=_join_norm,
    expensive=True,
)


def _append_norm(p, s):
    path, opts = _other_source(p)
    how = p.get("how") or "diagonal_relaxed"
    if how not in ("vertical", "vertical_relaxed", "diagonal", "diagonal_relaxed"):
        raise ValueError("append how must be vertical, vertical_relaxed, diagonal or diagonal_relaxed")
    return {"path": path, "options": opts, "how": how}


def _append_code(p):
    imports, load = sources.load_statement(p["path"], p.get("options") or {})
    return [*(f"{i}  # for the appended file" for i in imports), f"df = pl.concat([df, {load}], how={p.get('how', 'diagonal_relaxed')!r})"]


register(
    "append_rows",
    apply=lambda lf, p, s: pl.concat([lf, sources.load_source(p["path"], p.get("options") or {})], how=p.get("how", "diagonal_relaxed")),
    code=_append_code,
    describe=lambda p: f"Append {str(p.get('path', '')).rsplit('/', 1)[-1]}",
    normalize=_append_norm,
    expensive=True,
)


# ---- window functions
WINDOW_FUNCS = ("row_number", "rank", "dense_rank", "lag", "lead", "cum_sum", "cum_max", "cum_min", "cum_count", "diff", "pct_change")


def _window_norm(p, s):
    fn = p.get("function")
    if fn not in WINDOW_FUNCS:
        raise ValueError(f"window function must be one of: {', '.join(WINDOW_FUNCS)}")
    if fn != "row_number" and not p.get("column"):
        raise ValueError(f"window {fn} needs a column")
    partition = _as_list(p.get("partition_by"))
    order_by = p.get("order_by") or None
    _check_columns(s, partition + ([p["column"]] if p.get("column") else []) + ([order_by] if order_by else []), "window")
    new = p.get("new_column") or (f"{p['column']}_{fn}" if p.get("column") else fn)
    return {"function": fn, "column": p.get("column") or None, "partition_by": partition, "order_by": order_by,
            "descending": bool(p.get("descending")), "offset": int(p.get("offset") or 1), "new_column": new}


def _window_base(p) -> tuple[pl.Expr, str]:
    fn, col, off = p["function"], p.get("column"), int(p.get("offset", 1))
    c = pl.col(col) if col else None
    cc = _c(col) if col else ""
    desc = bool(p.get("descending"))
    table = {
        "row_number": (pl.int_range(pl.len()) + 1, "(pl.int_range(pl.len()) + 1)"),
        "rank": (c.rank("min", descending=desc) if c is not None else None, f"{cc}.rank('min', descending={desc!r})"),
        "dense_rank": (c.rank("dense", descending=desc) if c is not None else None, f"{cc}.rank('dense', descending={desc!r})"),
        "lag": (c.shift(off) if c is not None else None, f"{cc}.shift({off})"),
        "lead": (c.shift(-off) if c is not None else None, f"{cc}.shift({-off})"),
        "cum_sum": (c.cum_sum() if c is not None else None, f"{cc}.cum_sum()"),
        "cum_max": (c.cum_max() if c is not None else None, f"{cc}.cum_max()"),
        "cum_min": (c.cum_min() if c is not None else None, f"{cc}.cum_min()"),
        "cum_count": (c.cum_count() if c is not None else None, f"{cc}.cum_count()"),
        "diff": (c.diff(off) if c is not None else None, f"{cc}.diff({off})"),
        "pct_change": (c.pct_change(off) if c is not None else None, f"{cc}.pct_change({off})"),
    }
    return table[fn]


def _window_apply(lf, p, s):
    expr, _ = _window_base(p)
    partition = _as_list(p.get("partition_by"))
    order_by = p.get("order_by")
    # rank already orders by its own column; other functions follow order_by.
    ob = order_by if p["function"] not in ("rank", "dense_rank") else None
    if partition or ob:
        expr = expr.over(partition or None, order_by=ob, descending=bool(p.get("descending")) if ob else False)
    return lf.with_columns(expr.alias(p["new_column"]))


def _window_code(p):
    _, code = _window_base(p)
    partition = _as_list(p.get("partition_by"))
    ob = p.get("order_by") if p["function"] not in ("rank", "dense_rank") else None
    if partition or ob:
        code += f".over({(partition or None)!r}, order_by={ob!r}, descending={bool(p.get('descending')) if ob else False!r})"
    return [f"df = df.with_columns({code}.alias({p['new_column']!r}))"]


register(
    "window",
    apply=_window_apply,
    code=_window_code,
    describe=lambda p: f"{str(p.get('function', '')).replace('_', ' ').title()}" + (f" of {p['column']}" if p.get("column") else "")
    + (f" per {_names(_as_list(p.get('partition_by')))}" if _as_list(p.get("partition_by")) else ""),
    normalize=_window_norm,
)


# ---- SQL
def _sql_norm(p, s):
    _require(p, "query", op="sql")
    dialect = p.get("dialect") or "duckdb"
    if dialect not in ("duckdb", "polars"):
        raise ValueError("sql dialect must be duckdb or polars")
    return {"query": p["query"], "dialect": dialect}


def _sql_apply(lf, p, s):
    if p.get("dialect", "duckdb") == "polars":
        return lf.sql(p["query"], table_name="df")
    import duckdb

    with duckdb.connect() as con:
        con.register("df", lf.collect())
        return con.sql(p["query"]).pl().lazy()


def _sql_code(p):
    if p.get("dialect", "duckdb") == "polars":
        return [f"df = df.sql({p['query']!r}, table_name='df')"]
    return [f"df = duckdb.sql({p['query']!r}).pl()  # 'df' in the query refers to the variable df"]


register(
    "sql",
    apply=_sql_apply,
    code=_sql_code,
    describe=lambda p: "SQL: " + " ".join(str(p.get("query", "")).split())[:60],
    normalize=_sql_norm,
    expensive=True,
    imports=("import duckdb",),
)


# ---- time series (kept compatible with projects saved before this module)
def _resample_norm(p, s):
    for k in ("time_col", "freq", "val_col", "agg"):
        if not p.get(k):
            raise ValueError(f"resample requires time_col, freq, val_col, and agg — missing: {[k for k in ('time_col', 'freq', 'val_col', 'agg') if not p.get(k)]}")
    _check_agg(p["agg"])
    _check_columns(s, [p["time_col"], p["val_col"]], "resample")
    if s is not None and not _is_temporal(s.get(p["time_col"])):
        raise ValueError(f"resample: {p['time_col']} is not a date/time column")
    return {"time_col": p["time_col"], "freq": p["freq"], "val_col": p["val_col"], "agg": p["agg"]}


register(
    "resample",
    apply=lambda lf, p, s: lf.sort(_resample_norm(p, None)["time_col"]).group_by_dynamic(p["time_col"], every=p["freq"]).agg(getattr(pl.col(p["val_col"]), p["agg"])()),
    code=lambda p: [f"df = df.sort({p['time_col']!r}).group_by_dynamic({p['time_col']!r}, every={p['freq']!r}).agg({_c(p['val_col'])}.{_check_agg(p['agg'])}())"],
    describe=lambda p: f"Resample {p.get('val_col')} every {p.get('freq')} ({p.get('agg')})",
    normalize=_resample_norm,
    expensive=True,
)

ROLLING_AGGS = ("mean", "sum", "min", "max", "median", "std", "var")


def _rolling_norm(p, s):
    for k in ("time_col", "window", "val_col", "agg"):
        if not p.get(k):
            raise ValueError(f"rolling requires time_col, window, val_col, and agg — missing: {[k for k in ('time_col', 'window', 'val_col', 'agg') if not p.get(k)]}")
    _check_agg(p["agg"])
    _check_columns(s, [p["time_col"], p["val_col"]], "rolling")
    out = {"time_col": p["time_col"], "window": p["window"], "val_col": p["val_col"], "agg": p["agg"],
           "keep_columns": p.get("keep_columns", True)}
    if out["keep_columns"]:
        if p["agg"] not in ROLLING_AGGS:
            raise ValueError(f"rolling agg must be one of: {', '.join(ROLLING_AGGS)}")
        out["new_column"] = p.get("new_column") or f"{p['val_col']}_rolling_{p['agg']}"
    return out


def _rolling_apply(lf, p, s):
    _check_agg(p["agg"])
    if p.get("keep_columns"):
        e = getattr(pl.col(p["val_col"]), f"rolling_{p['agg']}_by")(p["time_col"], window_size=p["window"])
        return lf.sort(p["time_col"]).with_columns(e.alias(p["new_column"]))
    # Legacy behaviour: one aggregated value per timestamp, other columns dropped.
    return lf.sort(p["time_col"]).rolling(index_column=p["time_col"], period=p["window"]).agg(getattr(pl.col(p["val_col"]), p["agg"])())


def _rolling_code(p):
    _check_agg(p["agg"])
    if p.get("keep_columns"):
        return [f"df = df.sort({p['time_col']!r}).with_columns({_c(p['val_col'])}.rolling_{p['agg']}_by({p['time_col']!r}, window_size={p['window']!r}).alias({p['new_column']!r}))"]
    return [f"df = df.sort({p['time_col']!r}).rolling(index_column={p['time_col']!r}, period={p['window']!r}).agg({_c(p['val_col'])}.{p['agg']}())"]


register(
    "rolling",
    apply=_rolling_apply,
    code=_rolling_code,
    describe=lambda p: f"Rolling {p.get('agg')} of {p.get('val_col')} over {p.get('window')}",
    normalize=_rolling_norm,
    expensive=True,
)


def _gaps_norm(p, s):
    for k in ("time_col", "val_col", "method"):
        if not p.get(k):
            raise ValueError(f"fill_gaps requires time_col, val_col, and method — missing: {[k for k in ('time_col', 'val_col', 'method') if not p.get(k)]}")
    if p["method"] not in ("forward", "backward"):
        raise ValueError("fill_gaps method must be forward or backward")
    _check_columns(s, [p["time_col"], p["val_col"]], "fill_gaps")
    return {"time_col": p["time_col"], "val_col": p["val_col"], "method": p["method"]}


register(
    "fill_gaps",
    apply=lambda lf, p, s: lf.sort(p["time_col"]).with_columns(pl.col(p["val_col"]).fill_null(strategy=_gaps_norm(p, None)["method"])),
    code=lambda p: [f"df = df.sort({p['time_col']!r}).with_columns({_c(p['val_col'])}.fill_null(strategy={p['method']!r}))"],
    describe=lambda p: f"Fill gaps in {p.get('val_col')} ({p.get('method')})",
    normalize=_gaps_norm,
)


def catalogue() -> list[str]:
    return [k for k in OPS if k != "ingest"]
