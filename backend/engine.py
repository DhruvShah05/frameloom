import math
import os
from collections import OrderedDict
from typing import Any, Dict, Optional

import duckdb
import polars as pl

from . import ops, sources
from .models import Node, Operation, Project

ROW_INDEX_COL = "__row_nr"
DEFAULT_CACHE_MB = 1024
# Cache every Nth step of an uncached chain to bound recompute cost.
CACHE_EVERY = 10


def _env_cache_bytes() -> int:
    try:
        return int(float(os.environ.get("LOOM_CACHE_MB", DEFAULT_CACHE_MB)) * 1024 * 1024)
    except ValueError:
        return DEFAULT_CACHE_MB * 1024 * 1024


def json_safe_rows(df: pl.DataFrame) -> list[dict]:
    """Rows ready for JSON: NaN/inf become null, exotic types become text."""
    exprs = []
    for name, dtype in df.schema.items():
        col = pl.col(name)
        if dtype.is_float():
            exprs.append(pl.when(col.is_nan() | col.is_infinite()).then(None).otherwise(col).alias(name))
        elif dtype in (pl.Binary,):
            exprs.append(col.bin.encode("hex").alias(name))
        elif dtype in (pl.Duration, pl.Object, pl.Null) or isinstance(dtype, (pl.Decimal,)):
            exprs.append(col.cast(pl.String).alias(name) if dtype != pl.Object else col.map_elements(str, return_dtype=pl.String).alias(name))
        elif dtype in (pl.Categorical,) or isinstance(dtype, pl.Enum):
            exprs.append(col.cast(pl.String).alias(name))
    if exprs:
        df = df.with_columns(exprs)
    return df.to_dicts()


def _float_or_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class ExecutionEngine:
    def __init__(self, max_cache_bytes: Optional[int] = None):
        # node_id -> materialized DataFrame, least recently used first.
        self.cache: "OrderedDict[str, pl.DataFrame]" = OrderedDict()
        self.max_cache_bytes = max_cache_bytes if max_cache_bytes is not None else _env_cache_bytes()
        self._cache_bytes = 0
        self._sizes: Dict[str, int] = {}
        self._node_project: Dict[str, str] = {}
        self._signatures: Dict[str, str] = {}
        self._count_cache: "OrderedDict[tuple, int]" = OrderedDict()

    # ------------------------------------------------------------ cache
    def _cache_put(self, project: Project, node_id: str, df: pl.DataFrame) -> None:
        size = int(df.estimated_size())
        if size > self.max_cache_bytes:
            return
        self._cache_drop(node_id)
        self.cache[node_id] = df
        self._sizes[node_id] = size
        self._node_project[node_id] = project.id
        self._cache_bytes += size
        while self._cache_bytes > self.max_cache_bytes and self.cache:
            oldest = next(iter(self.cache))
            self._cache_drop(oldest)

    def _cache_get(self, node_id: str) -> Optional[pl.DataFrame]:
        df = self.cache.get(node_id)
        if df is not None:
            self.cache.move_to_end(node_id)
        return df

    def _cache_drop(self, node_id: str) -> None:
        if node_id in self.cache:
            del self.cache[node_id]
            self._cache_bytes -= self._sizes.pop(node_id, 0)
            self._node_project.pop(node_id, None)

    def invalidate_project(self, project_id: str) -> None:
        for node_id in [n for n, p in self._node_project.items() if p == project_id]:
            self._cache_drop(node_id)
        for key in [k for k in self._count_cache if k[0] == project_id]:
            del self._count_cache[key]

    def _check_source(self, project: Project) -> None:
        """Drop cached results if the source file changed on disk."""
        sig = sources.source_signature(project.source_path)
        previous = self._signatures.get(project.id)
        if previous is not None and previous != sig:
            self.invalidate_project(project.id)
        self._signatures[project.id] = sig

    # ------------------------------------------------------------ plans
    def load_source(self, path: str, options: Optional[dict] = None) -> pl.LazyFrame:
        return sources.load_source(path, options or {})

    def _path_to(self, project: Project, target_node_id: str) -> list[Node]:
        path_nodes: list[Node] = []
        seen = set()
        current_id = target_node_id
        while current_id:
            if current_id in seen:
                raise ValueError("History contains a cycle")
            seen.add(current_id)
            node = project.nodes.get(current_id)
            if not node:
                break
            path_nodes.insert(0, node)
            current_id = node.parent_id
        if not path_nodes or path_nodes[-1].id != target_node_id:
            raise ValueError(f"Node '{target_node_id}' not found")
        return path_nodes

    def build_plan(self, project: Project, target_node_id: str) -> pl.LazyFrame:
        """Walks from root to target_node_id, building the Polars lazy plan."""
        self._check_source(project)
        path_nodes = self._path_to(project, target_node_id)

        # Start from the nearest cached ancestor, scanning back from the target.
        cache_hit_index = -1
        for i in range(len(path_nodes) - 1, -1, -1):
            if path_nodes[i].id in self.cache:
                cache_hit_index = i
                break

        if cache_hit_index != -1:
            lf = self._cache_get(path_nodes[cache_hit_index].id).lazy()
            remaining_nodes = path_nodes[cache_hit_index + 1:]
        else:
            lf = self.load_source(project.source_path, project.source_options)
            remaining_nodes = path_nodes
            fmt = sources.infer_format(project.source_path, project.source_options)
            if fmt in sources.EAGER_FORMATS and remaining_nodes:
                # Re-reading Excel/JSON/databases is slow: keep the root in memory.
                root = remaining_nodes[0]
                lf = self._apply_operation(lf, root.operation)
                df = lf.collect()
                self._cache_put(project, root.id, df)
                lf = df.lazy()
                remaining_nodes = remaining_nodes[1:]

        nodes_since_cache = 0
        for node in remaining_nodes:
            lf = self._apply_operation(lf, node.operation)
            nodes_since_cache += 1
            spec = ops.OPS.get(node.operation.type)
            if (spec is not None and spec.expensive) or nodes_since_cache >= CACHE_EVERY:
                df = lf.collect()
                self._cache_put(project, node.id, df)
                lf = df.lazy()
                nodes_since_cache = 0

        return lf

    def _apply_operation(self, lf: pl.LazyFrame, op: Operation) -> pl.LazyFrame:
        spec = ops.get_spec(op.type)
        schema = dict(lf.collect_schema()) if op.type != "ingest" else {}
        return spec.apply(lf, op.params, schema)

    def normalize_operation(self, lf: pl.LazyFrame, op: Operation) -> Operation:
        """Validate a new step against its parent's schema and store typed params."""
        spec = ops.get_spec(op.type)
        schema = dict(lf.collect_schema())
        params = spec.normalize(dict(op.params), schema)
        label = op.label
        if not label or label == op.type:
            try:
                label = spec.describe(params)
            except Exception:
                label = op.type.replace("_", " ").title()
        return op.model_copy(update={"params": params, "label": label})

    # ------------------------------------------------------------ reads
    def get_data(self, project: Project, node_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        lf = self.build_plan(project, node_id)
        return json_safe_rows(lf.slice(offset, limit).collect())

    def get_schema(self, project: Project, node_id: str) -> dict[str, str]:
        lf = self.build_plan(project, node_id)
        return {name: str(dtype) for name, dtype in lf.collect_schema().items()}

    def row_count(self, project: Project, node_id: str) -> int:
        key = (project.id, node_id, None)
        if key in self._count_cache and node_id in self.cache:
            return self._count_cache[key]
        n = self.build_plan(project, node_id).select(pl.len()).collect().item()
        self._remember_count(key, n)
        return n

    def _remember_count(self, key: tuple, n: int) -> None:
        self._count_cache[key] = n
        self._count_cache.move_to_end(key)
        while len(self._count_cache) > 512:
            self._count_cache.popitem(last=False)

    def get_view(self, project: Project, node_id: str, offset: int = 0, limit: int = 100,
                 sort: Optional[list[dict]] = None, filters: Optional[list[dict]] = None,
                 columns: Optional[list[str]] = None) -> dict:
        """A page of rows with optional view-only sort and filters.

        Each row carries ``__row_nr``: its position in the step's real output,
        so edits made in a sorted/filtered view land on the right row.
        """
        lf = self.build_plan(project, node_id)
        schema = dict(lf.collect_schema())
        view = lf.with_row_index(ROW_INDEX_COL)
        if filters:
            # Each item is a condition, or a group {"conditions": [...], "combine": "or"}.
            exprs = [ops.filter_expr(f if f.get("conditions") else {"conditions": [f]}, schema) for f in filters]
            view = view.filter(pl.all_horizontal(exprs))
        if sort:
            cols = [s["column"] for s in sort if s.get("column") in schema]
            if cols:
                desc = [bool(s.get("descending")) for s in sort if s.get("column") in schema]
                view = view.sort(cols, descending=desc, nulls_last=True, maintain_order=True)
        if columns:
            keep = [c for c in columns if c in schema]
            view = view.select([ROW_INDEX_COL, *keep])

        count_key = (project.id, node_id, repr((filters or [], sort or [])))
        if count_key in self._count_cache:
            total = self._count_cache[count_key]
        else:
            total = view.select(pl.len()).collect().item() if filters else self.row_count(project, node_id)
            self._remember_count(count_key, total)
        rows = json_safe_rows(view.slice(offset, limit).collect())
        return {"data": rows, "schema": {k: str(v) for k, v in schema.items()}, "total_rows": total,
                "offset": offset}

    def cache_node(self, project: Project, node_id: str):
        lf = self.build_plan(project, node_id)
        self._cache_put(project, node_id, lf.collect())

    # ------------------------------------------------------------ profile
    def profile(self, project: Project, node_id: str, bins: int = 20) -> dict:
        df = self.build_plan(project, node_id).collect()
        height = df.height
        columns = []
        for name, dtype in df.schema.items():
            columns.append(self._profile_column(df.get_column(name), dtype, height, bins))
        return {"row_count": height, "column_count": df.width, "columns": columns}

    def _profile_column(self, s: pl.Series, dtype, height: int, bins: int) -> dict:
        nulls = s.null_count()
        info: dict[str, Any] = {
            "name": s.name,
            "dtype": str(dtype),
            "kind": "numeric" if dtype.is_numeric() else "temporal" if dtype.is_temporal() else
                    "boolean" if dtype == pl.Boolean else "text" if dtype in (pl.String, pl.Categorical) or isinstance(dtype, pl.Enum) else "other",
            "count": height - nulls,
            "nulls": nulls,
            "null_pct": round(100 * nulls / height, 2) if height else 0.0,
            "issues": [],
        }
        issues = info["issues"]
        try:
            info["unique"] = s.n_unique()
        except Exception:
            info["unique"] = None

        if height and nulls == height:
            issues.append({"level": "warning", "message": "Column is completely empty",
                           "fix": {"type": "drop_column", "params": {"columns": [s.name]}}})
        elif nulls:
            issues.append({"level": "info", "message": f"{nulls:,} empty value{'s' if nulls != 1 else ''} ({info['null_pct']}%)",
                           "fix": {"type": "drop_missing", "params": {"columns": [s.name], "how": "any"}}})
        if height > 1 and info.get("unique") == 1 and nulls == 0:
            issues.append({"level": "info", "message": "Every row has the same value"})

        nn = s.drop_nulls()
        kind = info["kind"]
        if kind == "numeric" and nn.len():
            if nn.dtype.is_float():
                nn = nn.filter(nn.is_finite())
            if nn.len():
                q1, q3 = nn.quantile(0.25), nn.quantile(0.75)
                info.update({
                    "min": _float_or_none(nn.min()), "max": _float_or_none(nn.max()),
                    "mean": _float_or_none(nn.mean()), "median": _float_or_none(nn.median()),
                    "std": _float_or_none(nn.std()), "q1": _float_or_none(q1), "q3": _float_or_none(q3),
                    "zeros": int((nn == 0).sum()), "negatives": int((nn < 0).sum()),
                })
                if q1 is not None and q3 is not None:
                    iqr = q3 - q1
                    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                    outliers = int(((nn < lo) | (nn > hi)).sum())
                    info["outliers"] = outliers
                    if iqr > 0 and outliers:
                        issues.append({"level": "info", "message": f"{outliers:,} possible outlier{'s' if outliers != 1 else ''} (outside {lo:.4g} – {hi:.4g})",
                                       "filter": {"column": s.name, "operator": "between", "value": lo, "value2": hi}})
                info["histogram"] = self._histogram(nn.cast(pl.Float64), bins)
        elif kind == "temporal" and nn.len():
            info.update({"min": nn.min(), "max": nn.max()})
            if dtype in (pl.Date, pl.Datetime):
                phys = nn.to_physical().cast(pl.Float64)
                hist = self._histogram(phys, bins)
                to_dt = (lambda v: pl.Series([int(v)]).cast(pl.Int32 if dtype == pl.Date else pl.Int64).cast(dtype)[0])
                for b in hist:
                    b["x0"], b["x1"] = to_dt(b["x0"]), to_dt(b["x1"])
                info["histogram"] = hist
        elif kind in ("text", "boolean") and nn.len():
            text = nn.cast(pl.String)
            vc = text.value_counts(sort=True).head(10)
            info["top_values"] = [{"value": r[0], "count": r[1]} for r in vc.rows()]
            if kind == "text":
                lengths = text.str.len_chars()
                info.update({"min_length": lengths.min(), "max_length": lengths.max()})
                padded = int((text != text.str.strip_chars()).sum())
                if padded:
                    issues.append({"level": "warning", "message": f"{padded:,} value{'s' if padded != 1 else ''} with leading/trailing spaces",
                                   "fix": {"type": "text_transform", "params": {"columns": [s.name], "transform": "strip"}}})
                blanks = int((text.str.strip_chars() == "").sum())
                if blanks:
                    issues.append({"level": "warning", "message": f"{blanks:,} blank text value{'s' if blanks != 1 else ''} (not marked empty)",
                                   "fix": {"type": "find_replace", "params": {"columns": [s.name], "find": "", "replace": None, "mode": "whole_cell"}}})
                normalized = text.str.strip_chars().str.to_lowercase().str.replace_all(r"\s+", " ")
                variants = pl.DataFrame({"raw": text, "norm": normalized}).group_by("norm").agg(pl.col("raw").n_unique().alias("n")).filter(pl.col("n") > 1)
                if variants.height:
                    examples = (pl.DataFrame({"raw": text, "norm": normalized}).filter(pl.col("norm").is_in(variants["norm"].head(1).implode()))
                                .get_column("raw").unique().head(3).to_list())
                    issues.append({"level": "warning",
                                   "message": f"{variants.height:,} value{'s' if variants.height != 1 else ''} spelled inconsistently (case/spacing), e.g. {', '.join(repr(e) for e in examples)}",
                                   "fix": {"type": "text_transform", "params": {"columns": [s.name], "transform": "lower"}}})
                sample = text.head(5000)
                numeric_like = sample.str.strip_chars().str.replace_all(r"[,$€£₹%\s]", "").cast(pl.Float64, strict=False).drop_nulls().len()
                if sample.len() and numeric_like / sample.len() >= 0.9:
                    issues.append({"level": "warning", "message": "Looks numeric but is stored as text",
                                   "fix": {"type": "cast_column", "params": {"columns": [s.name], "dtype": "Float64", "clean": True}}})
                else:
                    try:
                        date_like = sample.str.to_datetime(strict=False).drop_nulls().len()
                    except pl.exceptions.PolarsError:
                        date_like = 0  # no consistent date format
                    if sample.len() and date_like / sample.len() >= 0.9:
                        issues.append({"level": "warning", "message": "Looks like dates but is stored as text",
                                       "fix": {"type": "parse_dates", "params": {"column": s.name, "to": "datetime"}}})
        return info

    @staticmethod
    def _histogram(values: pl.Series, bins: int) -> list[dict]:
        lo, hi = values.min(), values.max()
        if lo is None or hi is None:
            return []
        if lo == hi:
            return [{"x0": lo, "x1": hi, "count": values.len()}]
        width = (hi - lo) / bins
        idx = ((values - lo) / width).floor().cast(pl.Int64).clip(0, bins - 1)
        counts = dict(idx.value_counts().rows())
        return [{"x0": lo + i * width, "x1": lo + (i + 1) * width, "count": int(counts.get(i, 0))} for i in range(bins)]

    # ------------------------------------------------------------ charts
    def chart(self, project: Project, node_id: str, spec: dict) -> dict:
        lf = self.build_plan(project, node_id)
        schema = dict(lf.collect_schema())
        ctype = spec.get("type")
        x, y = spec.get("x"), spec.get("y")
        agg = spec.get("agg") or ("count" if not y else "sum")
        if ctype not in ("histogram", "bar", "line", "scatter"):
            raise ValueError("Chart type must be histogram, bar, line or scatter")
        if not x or x not in schema:
            raise ValueError("Choose a column for the x axis")
        if y and y not in schema:
            raise ValueError(f"Column '{y}' not found")
        if agg != "none":
            ops._check_agg(agg)
        x_type = "numeric" if schema[x].is_numeric() else "temporal" if schema[x].is_temporal() else "category"

        if ctype == "histogram":
            if x_type == "category":
                raise ValueError("Histograms need a numeric or date column; use a bar chart for text")
            s = lf.select(pl.col(x)).collect().get_column(x).drop_nulls()
            if x_type == "temporal":
                info = self._profile_column(s, schema[x], s.len(), int(spec.get("bins") or 30))
                return {"type": ctype, "x": x, "x_type": x_type, "bins": info.get("histogram", [])}
            if s.dtype.is_float():
                s = s.filter(s.is_finite())
            return {"type": ctype, "x": x, "x_type": x_type, "bins": self._histogram(s.cast(pl.Float64), int(spec.get("bins") or 30))}

        if ctype == "bar":
            value = pl.len() if (agg == "count" and not y) else getattr(pl.col(y), agg)()
            limit = int(spec.get("limit") or 30)
            grouped = lf.group_by(x).agg(value.alias("value"))
            total_groups = grouped.select(pl.len()).collect().item()
            if spec.get("sort", "value") == "x":
                grouped = grouped.sort(x, nulls_last=True)
            else:
                grouped = grouped.sort("value", descending=True, nulls_last=True)
            df = grouped.head(limit).collect()
            return {"type": ctype, "x": x, "y": y, "agg": agg, "x_type": x_type,
                    "points": [{"x": r[0], "y": _float_or_none(r[1])} for r in df.rows()],
                    "truncated": total_groups > limit, "total_groups": total_groups}

        if not y or not schema[y].is_numeric():
            raise ValueError(f"{ctype.title()} charts need a numeric y column")
        max_points = int(spec.get("max_points") or (2000 if ctype == "line" else 5000))
        if ctype == "line":
            if agg != "none":
                data = lf.group_by(x).agg(getattr(pl.col(y), agg)().alias(y)).sort(x)
            else:
                data = lf.select([x, y]).sort(x)
            df = data.drop_nulls(x).collect()
            n = df.height
            if n > max_points:
                step = math.ceil(n / max_points)
                df = df.gather_every(step)
        else:
            df = lf.select([x, y]).drop_nulls().collect()
            n = df.height
            if n > max_points:
                df = df.sample(n=max_points, seed=0)
        rows = json_safe_rows(df)
        return {"type": ctype, "x": x, "y": y, "agg": agg, "x_type": x_type,
                "points": [{"x": r[x], "y": r[y]} for r in rows], "sampled": n > max_points, "total_points": n}

    # ------------------------------------------------------------ compare
    def diff(self, project: Project, node_a: str, node_b: str) -> dict:
        schema_a = self.get_schema(project, node_a)
        schema_b = self.get_schema(project, node_b)
        row_count_a = self.row_count(project, node_a)
        row_count_b = self.row_count(project, node_b)
        return {
            "columns_added": [c for c in schema_b if c not in schema_a],
            "columns_removed": [c for c in schema_a if c not in schema_b],
            "dtype_changes": [{"column": c, "from": schema_a[c], "to": schema_b[c]}
                              for c in schema_a if c in schema_b and schema_a[c] != schema_b[c]],
            "row_count_a": row_count_a,
            "row_count_b": row_count_b,
            "row_count_delta": row_count_b - row_count_a,
        }

    def audit_compare(self, project: Project, node_a: str, node_b: str, key_column: str) -> list[dict]:
        df_a = self.build_plan(project, node_a).collect()
        df_b = self.build_plan(project, node_b).collect()

        if key_column not in df_a.schema or key_column not in df_b.schema:
            raise ValueError(f"Key column '{key_column}' must be present in both branches.")

        # A column whose type changed between the two steps (e.g. "$1,757" text
        # vs 1757.0) can't be compared directly: compare both sides as text.
        retyped = [c for c in df_a.schema if c in df_b.schema and df_a.schema[c] != df_b.schema[c]]
        if retyped:
            df_a = df_a.with_columns([pl.col(c).cast(pl.String) for c in retyped])
            df_b = df_b.with_columns([pl.col(c).cast(pl.String) for c in retyped])

        q = sources.quote_ident
        key = q(key_column)
        shared_cols = [c for c in df_a.schema if c in df_b.schema and c != key_column]

        select_cols = [f"COALESCE(a.{key}, b.{key}) AS {key}"]
        for i, col in enumerate(shared_cols):
            c = q(col)
            select_cols.append(f"a.{c} AS {q(col + '_a')}")
            select_cols.append(f"b.{c} AS {q(col + '_b')}")
            select_cols.append(f"(a.{c} IS DISTINCT FROM b.{c}) AS {q(f'__mismatch_{i}')}")

        match_conds = " AND ".join(f"a.{q(c)} IS NOT DISTINCT FROM b.{q(c)}" for c in shared_cols) or "TRUE"
        query = f"""
        SELECT {", ".join(select_cols)},
            CASE
                WHEN b.{key} IS NULL THEN 'missing_in_b'
                WHEN a.{key} IS NULL THEN 'missing_in_a'
                WHEN {match_conds} THEN 'match'
                ELSE 'mismatch'
            END AS _audit_status
        FROM df_a AS a
        FULL OUTER JOIN df_b AS b ON a.{key} = b.{key}
        ORDER BY 1 NULLS LAST
        """
        with duckdb.connect() as con:
            con.register("df_a", df_a)
            con.register("df_b", df_b)
            result_df = con.sql(query).pl()

        dicts = json_safe_rows(result_df)
        for row in dicts:
            mismatches = []
            for i, col in enumerate(shared_cols):
                if row.pop(f"__mismatch_{i}", None):
                    mismatches.append(col)
            row["_mismatched_columns"] = mismatches
        return dicts
