"""Export a pipeline as pandas code.

Most steps translate to idiomatic pandas. A few can't produce *identical*
results in pandas (random sampling and splits use a different RNG; SQL
formulas, pivots, bins and time-window resampling label things differently),
so those steps briefly hand the frame to Polars and back. The script says so
in a comment, and the result matches the app exactly either way.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import ops, sources

# A step that returns None is "bridged" through Polars.
PandasCode = Callable[[dict], Optional[list[str]]]
PANDAS: dict[str, PandasCode] = {}


def pandas_op(name: str):
    def deco(fn):
        PANDAS[name] = fn
        return fn
    return deco


def _col(c: str) -> str:
    return f"df[{c!r}]"


def _val(v: Any, dtype_name: Optional[str]) -> str:
    if v is None:
        return "None"
    if dtype_name and (dtype_name == "Date" or dtype_name.startswith("Datetime")) and isinstance(v, str):
        return f"pd.Timestamp({v!r})"
    return repr(v)


RESET = ".reset_index(drop=True)"


# ---------------------------------------------------------------- reading
def load_statement(path: str, options: dict, root_schema: Optional[dict] = None) -> tuple[list[str], str]:
    fmt = sources.infer_format(path, options)
    p = repr(path)
    dates = [c for c, t in (root_schema or {}).items() if t == "Date" or t.startswith("Datetime")]
    if fmt in ("csv", "tsv"):
        args = []
        sep = options.get("separator") or ("\t" if fmt == "tsv" else ",")
        if sep == "\\t":
            sep = "\t"
        if sep != ",":
            args.append(f"sep={sep!r}")
        enc = (options.get("encoding") or "utf8").lower()
        if enc == "utf8-lossy":
            args.append("encoding_errors='replace'")
        elif enc not in ("utf8", "utf-8"):
            args.append(f"encoding={enc!r}")
        if options.get("has_header") is False:
            args.append("header=None")
        if options.get("skip_rows"):
            args.append(f"skiprows={int(options['skip_rows'])}")
        if options.get("null_values"):
            args.append(f"na_values={options['null_values']!r}, keep_default_na=True")
        if dates and options.get("try_parse_dates", True) is not False:
            args.append(f"parse_dates={dates!r}")
        return [], f"pd.read_csv({p}{', ' + ', '.join(args) if args else ''})"
    if fmt == "parquet":
        return [], f"pd.read_parquet({p})"
    if fmt == "ipc":
        return [], f"pd.read_feather({p})"
    if fmt == "ndjson":
        return [], f"pd.read_json({p}, lines=True)"
    if fmt == "json":
        return [], f"pd.read_json({p})"
    if fmt == "excel":
        sheet = options.get("sheet_name")
        arg = f"sheet_name={sheet!r}" if sheet else f"sheet_name={int(options.get('sheet_id') or 1) - 1}"
        return [], f"pd.read_excel({p}, {arg})"
    if fmt == "sqlite":
        return ["import sqlite3"], f"pd.read_sql({sources._db_query(options)!r}, sqlite3.connect({p}))"
    if fmt == "duckdb":
        return ["import duckdb"], f"duckdb.connect({p}, read_only=True).sql({sources._db_query(options)!r}).df()"
    raise ValueError(f"Unsupported format '{fmt}'")


# ---------------------------------------------------------------- columns
@pandas_op("drop_column")
def _(p):
    return [f"df = df.drop(columns={ops._as_list(p['columns'])!r})"]


@pandas_op("select_columns")
def _(p):
    return [f"df = df[{ops._as_list(p['columns'])!r}]"]


@pandas_op("rename_column")
def _(p):
    return [f"df = df.rename(columns={p['mapping']!r})"]


@pandas_op("cast_column")
def _(p):
    lines = []
    src = p.get("source_dtypes") or {}
    for c in ops._as_list(p.get("columns") or p.get("column")):
        s = _col(c)
        is_text = src.get(c) in (None, "String", "Categorical")
        t = p["dtype"]
        if t in ("Int64", "Int32", "Float64", "Float32"):
            if p.get("clean") and is_text:
                s = f"{s}.astype('string').str.replace(r'[^0-9eE.+\\-]', '', regex=True)"
            num = f"pd.to_numeric({s}, errors='coerce')"
            if t.startswith("Int"):
                lines.append(f"df[{c!r}] = {num}.astype('Float64').astype('Int64')  # whole numbers; bad values -> <NA>")
            else:
                lines.append(f"df[{c!r}] = {num}.astype('float64')")
        elif t in ("Date", "Datetime"):
            fmt = f", format={p['format']!r}" if p.get("format") else ""
            norm = ".dt.normalize()" if t == "Date" else ""
            lines.append(f"df[{c!r}] = pd.to_datetime({s}{fmt}, errors='coerce'){norm}")
        elif t == "Boolean" and is_text:
            lines.append(f"df[{c!r}] = {s}.astype('string').str.strip().str.lower().map("
                         f"{{**dict.fromkeys({ops.TRUE_STRINGS!r}, True), **dict.fromkeys({ops.FALSE_STRINGS!r}, False)}}).astype('boolean')")
        elif t == "Boolean":
            lines.append(f"df[{c!r}] = {s}.astype('boolean')")
        elif t == "String":
            return None  # number/date -> text formatting differs between the libraries
        elif t == "Categorical":
            lines.append(f"df[{c!r}] = {s}.astype('category')")
        else:
            return None
    return lines


@pandas_op("add_column")
def _(p):
    return None  # SQL expression: evaluated with Polars for identical results


# ------------------------------------------------------------------- rows
def _cond(c: dict) -> str:
    col, op, dt = _col(c["column"]), c["operator"], c.get("dtype")
    if op == "is_null":
        return f"{col}.isna()"
    if op == "is_not_null":
        return f"{col}.notna()"
    if op in ("contains", "not_contains", "starts_with", "ends_with", "regex"):
        text = str(c["value"])
        s = f"{col}.astype('string')"
        if c.get("ignore_case"):
            s += ".str.lower()"
            text = text.lower()
        return {
            "contains": f"{s}.str.contains({text!r}, regex=False, na=False)",
            "not_contains": f"~{s}.str.contains({text!r}, regex=False, na=True)",
            "starts_with": f"{s}.str.startswith({text!r}, na=False)",
            "ends_with": f"{s}.str.endswith({text!r}, na=False)",
            "regex": f"{s}.str.contains({text!r}, regex=True, na=False)",
        }[op]
    if op in ("in", "not_in"):
        vals = ops._split_list(c["value"])
        vals_code = "[" + ", ".join(_val(v, dt) for v in vals) + "]"
        return f"{col}.isin({vals_code})" if op == "in" else f"(~{col}.isin({vals_code}) & {col}.notna())"
    v = _val(c.get("value"), dt)
    if op == "between":
        return f"{col}.between({v}, {_val(c.get('value2'), dt)})"
    if op == "ne":  # pandas treats missing != x as True; Polars (and SQL) drop it
        return f"(({col} != {v}) & {col}.notna())"
    sym = {"eq": "==", "gt": ">", "lt": "<", "ge": ">=", "le": "<="}[op]
    return f"({col} {sym} {v})"


@pandas_op("filter_rows")
def _(p):
    conds, combine = ops._conditions(p)
    parts = [_cond(c) for c in conds]
    joined = (" & " if combine == "and" else " | ").join(parts) if len(parts) > 1 else parts[0]
    return [f"df = df[{joined}]{RESET}"]


@pandas_op("sort")
def _(p):
    cols, desc = ops._sort_keys(p)
    return [f"df = df.sort_values({cols!r}, ascending={[not d for d in desc]!r}, na_position='last', kind='stable'){RESET}"]


@pandas_op("limit_rows")
def _(p):
    if p.get("by"):
        asc = not p.get("descending", True)
        return [f"df = df.sort_values({p['by']!r}, ascending={asc!r}, na_position='last', kind='stable').head({int(p['n'])}){RESET}"]
    return [f"df = df.head({int(p['n'])})"]


@pandas_op("sample_rows")
def _(p):
    return None  # same rows as the app need Polars' random generator


@pandas_op("drop_duplicates")
def _(p):
    keep = {"first": "'first'", "any": "'first'", "last": "'last'", "none": "False"}[p.get("keep") or "first"]
    subset = ops._as_list(p.get("columns")) or None
    return [f"df = df.drop_duplicates(subset={subset!r}, keep={keep}){RESET}"]


@pandas_op("edit_cell")
def _(p):
    col = p["column"]
    value = _val(p.get("value"), p.get("dtype"))
    if p.get("match") is not None:
        dts = p.get("match_dtypes") or {}
        parts = [f"{_col(c)}.isna()" if v is None else f"({_col(c)} == {_val(v, dts.get(c))})" for c, v in p["match"].items()]
        return [f"df.loc[{' & '.join(parts)}, {col!r}] = {value}"]
    return [f"df.loc[{int(p['row_index'])}, {col!r}] = {value}"]


# ------------------------------------------------------------------- text
TEXT_PANDAS = {
    "lower": ".str.lower()", "upper": ".str.upper()", "title": ".str.title()", "strip": ".str.strip()",
    "strip_start": ".str.lstrip()", "strip_end": ".str.rstrip()",
    "collapse_spaces": ".str.replace(r'\\s+', ' ', regex=True).str.strip()", "length": ".str.len()",
}


@pandas_op("text_transform")
def _(p):
    suffix = TEXT_PANDAS[p["transform"]]
    return [f"df[{(p.get('new_column') or c)!r}] = {_col(c)}.astype('string'){suffix}"
            for c in ops._as_list(p.get("columns") or p.get("column"))]


@pandas_op("split_column")
def _(p):
    col, names = p["column"], p["names"]
    lines = [f"_parts = {_col(col)}.astype('string').str.split({p['delimiter']!r}, regex=False, expand=True).reindex(columns=range({len(names)}))",
             f"df[{names!r}] = _parts.to_numpy()"]
    if p.get("drop_original"):
        lines.append(f"df = df.drop(columns=[{col!r}])")
    return lines


@pandas_op("regex_extract")
def _(p):
    return [f"df[{p['new_column']!r}] = {_col(p['column'])}.astype('string').str.extract({p['pattern']!r}, expand=True)[{int(p.get('group', 1)) - 1}]"]


@pandas_op("find_replace")
def _(p):
    lines = []
    dtypes = p.get("dtypes") or {}
    for c in ops._as_list(p.get("columns") or p.get("column")):
        find, rep = ops._pick(p["find"], c), ops._pick(p.get("replace", ""), c)
        mode = p.get("mode", "contains")
        if mode == "whole_cell":
            cond = f"{_col(c)}.isna()" if find is None else f"({_col(c)} == {_val(find, dtypes.get(c))})"
            lines.append(f"df[{c!r}] = {_col(c)}.mask({cond}, {_val(rep, dtypes.get(c))})")
        elif mode == "regex":
            rep_py = re.sub(r"\$(\d+)", r"\\\1", str(rep))  # $1 (Polars) -> \1 (Python re)
            lines.append(f"df[{c!r}] = {_col(c)}.str.replace({str(find)!r}, {rep_py!r}, regex=True)")
        else:
            lines.append(f"df[{c!r}] = {_col(c)}.str.replace({str(find)!r}, {str(rep)!r}, regex=False)")
    return lines


@pandas_op("parse_dates")
def _(p):
    fmt = f", format={p['format']!r}" if p.get("format") else ""
    norm = ".dt.normalize()" if p.get("to") == "date" else ""
    return [f"df[{(p.get('new_column') or p['column'])!r}] = pd.to_datetime({_col(p['column'])}.astype('string'){fmt}, errors='coerce'){norm}"]


# ---------------------------------------------------------------- missing
@pandas_op("fill_missing")
def _(p):
    lines = []
    strategy = p.get("strategy", "value")
    group = ops._as_list(p.get("group_by"))
    dtypes = p.get("dtypes") or {}
    for c in ops._as_list(p.get("columns") or p.get("column")):
        s = _col(c)
        if strategy == "value":
            lines.append(f"df[{c!r}] = {s}.fillna({_val(ops._pick(p['value'], c), dtypes.get(c))})")
        elif strategy in ("forward", "backward"):
            fn = "ffill" if strategy == "forward" else "bfill"
            lines.append(f"df[{c!r}] = df.groupby({group!r}, dropna=False)[{c!r}].{fn}()" if group else f"df[{c!r}] = {s}.{fn}()")
        elif strategy == "zero":
            lines.append(f"df[{c!r}] = {s}.fillna(0)")
        else:
            stat = f"df.groupby({group!r}, dropna=False)[{c!r}].transform({strategy!r})" if group else f"{s}.{strategy}()"
            lines.append(f"df[{c!r}] = {s}.fillna({stat})")
    return lines


@pandas_op("drop_missing")
def _(p):
    subset = ops._as_list(p.get("columns")) or None
    return [f"df = df.dropna(subset={subset!r}, how={p.get('how', 'any')!r}){RESET}"]


@pandas_op("bin_column")
def _(p):
    return None  # Polars' bin labels ("(18, 35]") differ from pandas' Interval labels


# ---------------------------------------------------------------- reshape
PANDAS_AGG = {"n_unique": "nunique"}


@pandas_op("group_by")
def _(p):
    by = ops._as_list(p["by"])
    if any(a.get("agg") in ("first", "last") for a in p.get("aggs") or []):
        return None  # pandas' first/last skip missing values; Polars' don't
    named = []
    for a in p.get("aggs") or [{"agg": "count"}]:
        agg, col, alias = a.get("agg") or "count", a.get("column"), a.get("alias")
        if agg == "count" and not col:
            named.append(f"{alias!r}: ({by[0]!r}, 'size')")
        else:
            named.append(f"{alias!r}: ({col!r}, {PANDAS_AGG.get(agg, agg)!r})")
    return [f"df = df.groupby({by!r}, sort=False, dropna=False).agg(**{{{', '.join(named)}}}).reset_index()"]


@pandas_op("pivot")
def _(p):
    return None


@pandas_op("unpivot")
def _(p):
    index = ops._as_list(p.get("index")) or None
    return [f"df = df.melt(id_vars={index!r}, value_vars={p.get('on') or None!r}, var_name={p.get('variable_name', 'variable')!r}, value_name={p.get('value_name', 'value')!r})"]


def _other_frame(p) -> tuple[list[str], str]:
    return load_statement(p["path"], p.get("options") or {}, None)


@pandas_op("join")
def _(p):
    imports, load = _other_frame(p)
    lines = [*(f"{i}  # for the joined file" for i in imports), f"right = {load}"]
    how, lo, ro = p.get("how", "left"), p.get("left_on") or [], p.get("right_on") or []
    suffix = p.get("suffix", "_right")
    if how == "cross":
        lines.append(f"df = df.merge(right, how='cross', suffixes=('', {suffix!r}))")
    elif how in ("semi", "anti"):
        lines.append(f"_keys = right[{ro!r}].drop_duplicates().set_axis({lo!r}, axis=1)")
        lines.append(f"_hit = df[{lo!r}].merge(_keys, how='left', indicator=True)['_merge'].eq('both').to_numpy()")
        lines.append(f"df = df[{'_hit' if how == 'semi' else '~_hit'}]{RESET}")
    else:
        pd_how = {"full": "outer"}.get(how, how)
        lines.append(f"df = df.merge(right, left_on={lo!r}, right_on={ro!r}, how={pd_how!r}, suffixes=('', {suffix!r}))")
        extra = [r for l_, r in zip(lo, ro) if r != l_]
        if extra:
            for l_, r in zip(lo, ro):
                if r != l_:
                    lines.append(f"df[{l_!r}] = df[{l_!r}].fillna(df[{r!r}])")
            lines.append(f"df = df.drop(columns={extra!r})")
    return lines


@pandas_op("append_rows")
def _(p):
    imports, load = _other_frame(p)
    return [*(f"{i}  # for the appended file" for i in imports), f"df = pd.concat([df, {load}], ignore_index=True)"]


# ---------------------------------------------------------------- window
@pandas_op("window")
def _(p):
    fn, col, off = p["function"], p.get("column"), int(p.get("offset", 1))
    part = ops._as_list(p.get("partition_by"))
    ob = p.get("order_by") if fn not in ("rank", "dense_rank") else None
    desc = bool(p.get("descending"))
    new = p["new_column"]
    lines = []
    if ob:
        lines.append(f"df = df.sort_values({ob!r}, ascending={not desc!r}, kind='stable')  # compute in this order...")
    g = f"df.groupby({part!r}, dropna=False, sort=False)" if part else None
    target = f"{g}[{col!r}]" if g and col else _col(col) if col else None
    expr = {
        "row_number": f"{g}.cumcount() + 1" if g else "pd.Series(range(1, len(df) + 1), index=df.index)",
        "rank": f"{target}.rank(method='min', ascending={not desc!r})",
        "dense_rank": f"{target}.rank(method='dense', ascending={not desc!r})",
        "lag": f"{target}.shift({off})",
        "lead": f"{target}.shift({-off})",
        "cum_sum": f"{target}.cumsum()",
        "cum_max": f"{target}.cummax()",
        "cum_min": f"{target}.cummin()",
        "cum_count": (f"{_col(col)}.notna().groupby([df[c] for c in {part!r}]).cumsum()" if part else f"{_col(col)}.notna().cumsum()") if col else "",
        "diff": f"{target}.diff({off})",
        "pct_change": f"{target}.pct_change({off}, fill_method=None)",
    }[fn]
    lines.append(f"df[{new!r}] = {expr}")
    if ob:
        lines.append("df = df.sort_index()  # ...then back to the original row order")
    return lines


@pandas_op("sql")
def _(p):
    if p.get("dialect", "duckdb") == "polars":
        return None
    return [f"df = duckdb.sql({p['query']!r}).df()  # 'df' in the query refers to the variable df"]


# ----------------------------------------------------------- time series
@pandas_op("resample")
def _(p):
    return None


@pandas_op("rolling")
def _(p):
    return None


@pandas_op("fill_gaps")
def _(p):
    fn = "ffill" if p["method"] == "forward" else "bfill"
    return [f"df = df.sort_values({p['time_col']!r}, kind='stable'){RESET}", f"df[{p['val_col']!r}] = {_col(p['val_col'])}.{fn}()"]


# --------------------------------------------------------------- ML prep
@pandas_op("one_hot_encode")
def _(p):
    lines = []
    for c, info in p["categories"].items():
        lines.append(f"for v in {info['values']!r}:")
        lines.append(f"    df[{c!r} + '_' + v] = ({_col(c)}.astype('string') == v).fillna(False).astype('int8')")
        if info.get("other"):
            lines.append(f"df[{c + '_other'!r}] = ({_col(c)}.notna() & ~{_col(c)}.astype('string').isin({info['values']!r})).astype('int8')")
    if p.get("drop_original", True):
        lines.append(f"df = df.drop(columns={list(p['categories'])!r})")
    return lines


@pandas_op("label_encode")
def _(p):
    lines = []
    for c, values in p["mapping"].items():
        mapping = {v: i for i, v in enumerate(values)}
        lines.append(f"df[{c + p.get('suffix', '_code')!r}] = {_col(c)}.astype('string').map({mapping!r}).fillna(-1).astype('int64')  # unseen -> -1")
    return lines


@pandas_op("scale")
def _(p):
    return [f"df[{c + p.get('suffix', '')!r}] = ({_col(c)}.astype('float64') - {st['center']!r}) / {st['scale']!r}"
            for c, st in p["stats"].items()]


@pandas_op("clip_outliers")
def _(p):
    return [f"df[{c!r}] = {_col(c)}.clip({b['lower']!r}, {b['upper']!r})" for c, b in p["bounds"].items()
            if b["lower"] is not None or b["upper"] is not None]


@pandas_op("train_test_split")
def _(p):
    return None  # identical splits need Polars' seeded shuffle


DATE_PARTS_PANDAS = {
    "year": ".dt.year", "quarter": ".dt.quarter", "month": ".dt.month", "week": ".dt.isocalendar().week",
    "day": ".dt.day", "day_of_year": ".dt.dayofyear", "weekday": ".dt.dayofweek + 1", "hour": ".dt.hour",
    "minute": ".dt.minute", "is_weekend": ".dt.dayofweek >= 5",
}


@pandas_op("date_parts")
def _(p):
    return [f"df[{p['prefix'] + part!r}] = {_col(p['column'])}{DATE_PARTS_PANDAS[part]}" for part in p["parts"]]


# ----------------------------------------------------------------- script
def render(op, imports: list[str]) -> list[str]:
    fn = PANDAS.get(op.type)
    lines = fn(op.params) if fn else None
    if lines is not None:
        spec = ops.OPS.get(op.type)
        if spec and op.type == "sql" and "import duckdb" not in imports:
            imports.append("import duckdb")
        return lines or ["pass  # nothing to do"]
    # Bridge: run this one step with Polars so results match the app exactly.
    if "import polars as pl" not in imports:
        imports.append("import polars as pl")
    spec = ops.get_spec(op.type)
    for imp in spec.imports:
        if imp not in imports:
            imports.append(imp)
    return ["# (this step runs in Polars so the result is identical to Loom's)", "df = pl.from_pandas(df)",
            *spec.code(op.params), "df = df.to_pandas()"]
