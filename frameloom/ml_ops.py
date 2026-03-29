"""Machine-learning preparation steps.

Encoders, scalers and clipping *learn* their settings when the step is added
(see OpSpec.fit) and store them in the step. That means:
  * replaying the step - or applying it to a new file through a recipe - uses
    exactly the same categories / means / bounds (like a fitted transformer);
  * exported code contains the learned numbers;
  * `fit_on` can restrict learning to e.g. the training rows, so nothing leaks
    from the test set.
Editing the step, or editing an earlier step, re-learns them.
"""
from __future__ import annotations

import polars as pl

from .ops import (
    _as_list,
    _c,
    _check_columns,
    _dtype_name,
    _is_numeric,
    _is_temporal,
    _names,
    cast_value,
    register,
)


# ------------------------------------------------------------ fit_on helper
def _fit_on_norm(p, s) -> dict | None:
    fo = p.get("fit_on")
    if not fo or not fo.get("column"):
        return None
    _check_columns(s, [fo["column"]], "fit_on")
    dtype = s.get(fo["column"]) if s is not None else None
    return {"column": fo["column"], "value": cast_value(fo.get("value"), dtype)}


def _fit_frame(lf: pl.LazyFrame, p: dict) -> pl.LazyFrame:
    fo = p.get("fit_on")
    if fo:
        lf = lf.filter(pl.col(fo["column"]) == fo["value"])
    return lf


def _fit_suffix(p) -> str:
    fo = p.get("fit_on")
    return f" (learned on {fo['column']}={fo['value']})" if fo else ""


def _py(v):
    """Plain Python scalars for JSON storage."""
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    return v


# ------------------------------------------------------------ one-hot encode
def _onehot_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    if not cols:
        raise ValueError("one_hot_encode needs columns")
    _check_columns(s, cols, "one_hot_encode")
    max_c = int(p.get("max_categories") or 20)
    if max_c < 1:
        raise ValueError("max_categories must be at least 1")
    out = {"columns": cols, "max_categories": max_c, "drop_original": p.get("drop_original", True) is not False,
           "fit_on": _fit_on_norm(p, s)}
    if p.get("categories"):
        out["categories"] = p["categories"]
    return out


def _onehot_fit(lf, p, s):
    data = _fit_frame(lf, p)
    cats = {}
    for c in p["columns"]:
        counts = (data.select(pl.col(c).cast(pl.String)).drop_nulls().group_by(c).len()
                  .sort(["len", c], descending=[True, False]).collect())
        values = counts[c].to_list()
        cats[c] = {"values": sorted(values[: p["max_categories"]]), "other": len(values) > p["max_categories"]}
    return {**p, "categories": cats}


def _onehot_name(col, val):
    return f"{col}_{val}"


def _onehot_apply(lf, p, s):
    exprs = []
    for c, info in p["categories"].items():
        text = pl.col(c).cast(pl.String)
        for v in info["values"]:
            exprs.append((text == v).fill_null(False).cast(pl.Int8).alias(_onehot_name(c, v)))
        if info.get("other"):
            exprs.append((text.is_not_null() & ~text.is_in(info["values"])).cast(pl.Int8).alias(_onehot_name(c, "other")))
    lf = lf.with_columns(exprs)
    return lf.drop(list(p["categories"])) if p.get("drop_original", True) else lf


def _onehot_code(p):
    lines = []
    for c, info in p["categories"].items():
        lines.append(f"_values = {info['values']!r}")
        lines.append(f"df = df.with_columns([({_c(c)}.cast(pl.String) == v).fill_null(False).cast(pl.Int8).alias({c!r} + '_' + v) for v in _values])")
        if info.get("other"):
            lines.append(f"df = df.with_columns(({_c(c)}.cast(pl.String).is_not_null() & ~{_c(c)}.cast(pl.String).is_in(_values)).cast(pl.Int8).alias({_onehot_name(c, 'other')!r}))")
    if p.get("drop_original", True):
        lines.append(f"df = df.drop({list(p['categories'])!r})")
    return lines


register(
    "one_hot_encode",
    apply=_onehot_apply,
    code=_onehot_code,
    describe=lambda p: f"One-hot encode {_names(_as_list(p.get('columns') or p.get('column')))}" + _fit_suffix(p),
    normalize=_onehot_norm,
    fit=_onehot_fit,
)


# ------------------------------------------------------------- label encode
def _label_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    if not cols:
        raise ValueError("label_encode needs columns")
    _check_columns(s, cols, "label_encode")
    order = p.get("order") or "alphabetical"
    if order not in ("alphabetical", "frequency"):
        raise ValueError("label_encode order must be alphabetical or frequency")
    return {"columns": cols, "order": order, "suffix": p.get("suffix", "_code") or "",
            "fit_on": _fit_on_norm(p, s), **({"mapping": p["mapping"]} if p.get("mapping") else {})}


def _label_fit(lf, p, s):
    data = _fit_frame(lf, p)
    mapping = {}
    for c in p["columns"]:
        counts = data.select(pl.col(c).cast(pl.String)).drop_nulls().group_by(c).len().collect()
        if p["order"] == "frequency":
            values = counts.sort(["len", c], descending=[True, False])[c].to_list()
        else:
            values = sorted(counts[c].to_list())
        mapping[c] = values
    return {**p, "mapping": mapping}


def _label_apply(lf, p, s):
    exprs = []
    for c, values in p["mapping"].items():
        exprs.append(pl.col(c).cast(pl.String)
                     .replace_strict(values, list(range(len(values))), default=-1, return_dtype=pl.Int64)
                     .alias(f"{c}{p.get('suffix', '_code')}"))
    return lf.with_columns(exprs)


def _label_code(p):
    lines = []
    for c, values in p["mapping"].items():
        lines.append(f"_values = {values!r}  # unseen values become -1")
        lines.append(f"df = df.with_columns({_c(c)}.cast(pl.String).replace_strict(_values, list(range(len(_values))), default=-1, return_dtype=pl.Int64).alias({c + p.get('suffix', '_code')!r}))")
    return lines


register(
    "label_encode",
    apply=_label_apply,
    code=_label_code,
    describe=lambda p: f"Label-encode {_names(_as_list(p.get('columns') or p.get('column')))}" + _fit_suffix(p),
    normalize=_label_norm,
    fit=_label_fit,
)


# -------------------------------------------------------------------- scale
SCALE_METHODS = ("standard", "minmax", "robust")


def _scale_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    method = p.get("method") or "standard"
    if not cols:
        raise ValueError("scale needs columns")
    if method not in SCALE_METHODS:
        raise ValueError(f"scale method must be one of: {', '.join(SCALE_METHODS)}")
    _check_columns(s, cols, "scale")
    if s is not None:
        bad = [c for c in cols if not _is_numeric(s.get(c))]
        if bad:
            raise ValueError(f"scale needs numeric columns; not numeric: {', '.join(bad)}")
    return {"columns": cols, "method": method, "suffix": p.get("suffix") or "", "fit_on": _fit_on_norm(p, s),
            **({"stats": p["stats"]} if p.get("stats") else {})}


def _scale_fit(lf, p, s):
    data = _fit_frame(lf, p)
    stats = {}
    for c in p["columns"]:
        e = pl.col(c).cast(pl.Float64)
        if p["method"] == "standard":
            row = data.select(e.mean().alias("center"), e.std().alias("scale")).collect().row(0)
        elif p["method"] == "minmax":
            row = data.select(e.min().alias("center"), (e.max() - e.min()).alias("scale")).collect().row(0)
        else:
            row = data.select(e.median().alias("center"), (e.quantile(0.75) - e.quantile(0.25)).alias("scale")).collect().row(0)
        center, scale = _py(row[0]), _py(row[1])
        stats[c] = {"center": center if center is not None else 0.0, "scale": scale if scale else 1.0}
    return {**p, "stats": stats}


def _scale_apply(lf, p, s):
    return lf.with_columns([((pl.col(c).cast(pl.Float64) - st["center"]) / st["scale"]).alias(c + p.get("suffix", ""))
                            for c, st in p["stats"].items()])


def _scale_code(p):
    exprs = [f"(({_c(c)}.cast(pl.Float64) - {st['center']!r}) / {st['scale']!r}).alias({c + p.get('suffix', '')!r})"
             for c, st in p["stats"].items()]
    return [f"df = df.with_columns([{', '.join(exprs)}])"]


register(
    "scale",
    apply=_scale_apply,
    code=_scale_code,
    describe=lambda p: f"Scale {_names(_as_list(p.get('columns') or p.get('column')))} ({p.get('method', 'standard')})" + _fit_suffix(p),
    normalize=_scale_norm,
    fit=_scale_fit,
)


# ------------------------------------------------------------ clip outliers
def _clip_norm(p, s):
    cols = _as_list(p.get("columns") or p.get("column"))
    method = p.get("method") or "iqr"
    if not cols:
        raise ValueError("clip_outliers needs columns")
    if method not in ("iqr", "percentile", "values"):
        raise ValueError("clip_outliers method must be iqr, percentile or values")
    _check_columns(s, cols, "clip_outliers")
    if s is not None:
        bad = [c for c in cols if not _is_numeric(s.get(c))]
        if bad:
            raise ValueError(f"clip_outliers needs numeric columns; not numeric: {', '.join(bad)}")
    out = {"columns": cols, "method": method, "fit_on": _fit_on_norm(p, s)}
    if method == "iqr":
        out["k"] = float(p.get("k") or 1.5)
    elif method == "percentile":
        out["lower_pct"] = float(p.get("lower_pct") if p.get("lower_pct") not in (None, "") else 1)
        out["upper_pct"] = float(p.get("upper_pct") if p.get("upper_pct") not in (None, "") else 99)
        if not 0 <= out["lower_pct"] < out["upper_pct"] <= 100:
            raise ValueError("Percentiles must satisfy 0 ≤ lower < upper ≤ 100")
    else:
        lo, hi = p.get("lower"), p.get("upper")
        if lo in (None, "") and hi in (None, ""):
            raise ValueError("clip_outliers with fixed values needs a lower and/or upper bound")
        out["bounds"] = {c: {"lower": float(lo) if lo not in (None, "") else None,
                             "upper": float(hi) if hi not in (None, "") else None} for c in cols}
    if p.get("bounds") and method != "values":
        out["bounds"] = p["bounds"]
    return out


def _clip_fit(lf, p, s):
    if p["method"] == "values":
        return p
    data = _fit_frame(lf, p)
    bounds = {}
    for c in p["columns"]:
        e = pl.col(c).cast(pl.Float64)
        if p["method"] == "iqr":
            q1, q3 = data.select(e.quantile(0.25).alias("q1"), e.quantile(0.75).alias("q3")).collect().row(0)
            q1, q3 = _py(q1), _py(q3)
            iqr = (q3 - q1) if q1 is not None and q3 is not None else None
            bounds[c] = {"lower": q1 - p["k"] * iqr if iqr is not None else None,
                         "upper": q3 + p["k"] * iqr if iqr is not None else None}
        else:
            lo, hi = data.select(e.quantile(p["lower_pct"] / 100).alias("lo"), e.quantile(p["upper_pct"] / 100).alias("hi")).collect().row(0)
            bounds[c] = {"lower": _py(lo), "upper": _py(hi)}
    return {**p, "bounds": bounds}


def _clip_apply(lf, p, s):
    exprs = []
    for c, b in p["bounds"].items():
        e = pl.col(c)
        if b["lower"] is not None or b["upper"] is not None:
            exprs.append(e.clip(b["lower"], b["upper"]).alias(c))
    return lf.with_columns(exprs) if exprs else lf


def _clip_code(p):
    exprs = [f"{_c(c)}.clip({b['lower']!r}, {b['upper']!r}).alias({c!r})" for c, b in p["bounds"].items()
             if b["lower"] is not None or b["upper"] is not None]
    return [f"df = df.with_columns([{', '.join(exprs)}])"] if exprs else []


register(
    "clip_outliers",
    apply=_clip_apply,
    code=_clip_code,
    describe=lambda p: f"Clip outliers in {_names(_as_list(p.get('columns') or p.get('column')))} ({p.get('method', 'iqr')})" + _fit_suffix(p),
    normalize=_clip_norm,
    fit=_clip_fit,
)


# --------------------------------------------------------- train/test split
_HASH_A, _HASH_B, _HASH_P = 2654435761, 40503, 4294967291


def _split_norm(p, s):
    test = float(p.get("test_size") if p.get("test_size") not in (None, "") else 0.2)
    val = float(p.get("val_size") or 0)
    if test > 1:
        test /= 100
    if val > 1:
        val /= 100
    if not (0 < test < 1) or not (0 <= val < 1) or test + val >= 1:
        raise ValueError("Split sizes must be fractions (e.g. 0.2) that add up to less than 1")
    stratify, group = p.get("stratify") or None, p.get("group") or None
    if stratify and group:
        raise ValueError("Choose stratify or group, not both")
    _check_columns(s, [c for c in (stratify, group) if c], "train_test_split")
    column = p.get("column") or "split"
    if s is not None and column in s:
        raise ValueError(f"A column named '{column}' already exists; pick another name for the split column")
    return {"test_size": test, "val_size": val, "seed": int(p.get("seed") if p.get("seed") not in (None, "") else 42),
            "stratify": stratify, "group": group, "column": column}


def _split_cut(p) -> tuple[float, float]:
    test = float(p["test_size"])
    return test, round(test + float(p["val_size"] or 0), 10)


def _split_apply(lf, p, s):
    seed = int(p["seed"])
    test, test_val = _split_cut(p)
    if p.get("group"):
        # Same split for every row of a group: a deterministic hash of the group's rank.
        d = pl.col(p["group"]).rank("dense").cast(pl.Int64)
        u = ((d * _HASH_A + seed * _HASH_B) % _HASH_P) / _HASH_P
        is_test, is_val = u < test, u < test_val
    else:
        # A seeded permutation; comparing row counts keeps the sizes exact.
        rank = pl.int_range(pl.len()).shuffle(seed=seed)
        n = pl.len()
        if p.get("stratify"):
            rank, n = rank.over(p["stratify"]), n.over(p["stratify"])
        is_test, is_val = rank < (n * test).round(), rank < (n * test_val).round()
    expr = pl.when(is_test).then(pl.lit("test"))
    if p["val_size"]:
        expr = expr.when(is_val).then(pl.lit("val"))
    return lf.with_columns(expr.otherwise(pl.lit("train")).alias(p["column"]))


def _split_code(p):
    seed = int(p["seed"])
    test, test_val = _split_cut(p)
    if p.get("group"):
        lines = [f"_u = (({_c(p['group'])}.rank('dense').cast(pl.Int64) * {_HASH_A} + {seed} * {_HASH_B}) % {_HASH_P}) / {_HASH_P}",
                 f"_is_test, _is_val = _u < {test!r}, _u < {test_val!r}"]
    else:
        over = f".over({p['stratify']!r})" if p.get("stratify") else ""
        lines = [f"_rank, _n = pl.int_range(pl.len()).shuffle(seed={seed}){over}, pl.len(){over}",
                 f"_is_test, _is_val = _rank < (_n * {test!r}).round(), _rank < (_n * {test_val!r}).round()"]
    expr = "pl.when(_is_test).then(pl.lit('test'))"
    if p["val_size"]:
        expr += ".when(_is_val).then(pl.lit('val'))"
    lines.append(f"df = df.with_columns({expr}.otherwise(pl.lit('train')).alias({p['column']!r}))")
    return lines


register(
    "train_test_split",
    apply=_split_apply,
    code=_split_code,
    describe=lambda p: f"Split {int(round((1 - float(p.get('test_size', 0.2)) - float(p.get('val_size') or 0)) * 100))}/"
    + (f"{int(round(float(p.get('val_size')) * 100))}/" if p.get("val_size") else "")
    + f"{int(round(float(p.get('test_size', 0.2)) * 100))} train/" + ("val/" if p.get("val_size") else "") + "test"
    + (f" by {p['stratify']}" if p.get("stratify") else f" grouped by {p['group']}" if p.get("group") else ""),
    normalize=_split_norm,
)


# --------------------------------------------------------------- date parts
DATE_PARTS = {
    "year": ("year", "dt.year()"),
    "quarter": ("quarter", "dt.quarter()"),
    "month": ("month", "dt.month()"),
    "week": ("week", "dt.week()"),
    "day": ("day", "dt.day()"),
    "day_of_year": ("ordinal_day", "dt.ordinal_day()"),
    "weekday": ("weekday", "dt.weekday()"),
    "hour": ("hour", "dt.hour()"),
    "minute": ("minute", "dt.minute()"),
}


def _parts_norm(p, s):
    if not p.get("column"):
        raise ValueError("date_parts needs a column")
    _check_columns(s, [p["column"]], "date_parts")
    if s is not None and not _is_temporal(s.get(p["column"])):
        raise ValueError(f"date_parts needs a date column; {p['column']} is {_dtype_name(s.get(p['column']))} (parse it first)")
    parts = _as_list(p.get("parts")) or ["year", "month", "day", "weekday"]
    bad = [x for x in parts if x not in DATE_PARTS and x != "is_weekend"]
    if bad:
        raise ValueError(f"Unknown date parts: {', '.join(bad)}")
    if s is not None and s.get(p["column"]) == pl.Date and any(x in ("hour", "minute") for x in parts):
        raise ValueError("hour/minute need a date-and-time column")
    return {"column": p["column"], "parts": parts, "prefix": p.get("prefix") if p.get("prefix") is not None else f"{p['column']}_"}


def _parts_apply(lf, p, s):
    col = pl.col(p["column"])
    exprs = []
    for part in p["parts"]:
        if part == "is_weekend":
            exprs.append((col.dt.weekday() >= 6).alias(f"{p['prefix']}is_weekend"))
        else:
            exprs.append(getattr(col.dt, DATE_PARTS[part][0])().alias(f"{p['prefix']}{part}"))
    return lf.with_columns(exprs)


def _parts_code(p):
    exprs = []
    for part in p["parts"]:
        if part == "is_weekend":
            exprs.append(f"({_c(p['column'])}.dt.weekday() >= 6).alias({p['prefix'] + 'is_weekend'!r})")
        else:
            exprs.append(f"{_c(p['column'])}.{DATE_PARTS[part][1]}.alias({p['prefix'] + part!r})")
    return [f"df = df.with_columns([{', '.join(exprs)}])"]


register(
    "date_parts",
    apply=_parts_apply,
    code=_parts_code,
    describe=lambda p: f"Date parts of {p.get('column')}: {', '.join(_as_list(p.get('parts')))}",
    normalize=_parts_norm,
)
