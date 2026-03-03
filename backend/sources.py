"""Reading data sources (files, remote URLs, databases) and writing exports.

Everything here has a matching code-generation helper so that exported scripts
read and write data exactly the way the app does.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

import polars as pl

REMOTE_PREFIXES = ("http://", "https://", "s3://", "s3a://", "gs://", "gcs://", "az://", "abfs://", "abfss://", "hf://")

EXTENSION_FORMATS = {
    ".csv": "csv",
    ".txt": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".arrow": "ipc",
    ".feather": "ipc",
    ".ipc": "ipc",
    ".json": "json",
    ".ndjson": "ndjson",
    ".jsonl": "ndjson",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xlsb": "excel",
    ".xls": "excel",
    ".ods": "excel",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".duckdb": "duckdb",
    ".ddb": "duckdb",
}

SUPPORTED_FORMATS = sorted(set(EXTENSION_FORMATS.values()))

# Formats Polars can only read eagerly; their root frame is always cached.
EAGER_FORMATS = {"json", "excel", "sqlite", "duckdb"}

# CSV encodings Polars' lazy reader understands natively.
_NATIVE_CSV_ENCODINGS = {"utf8", "utf-8", "utf8-lossy"}


def is_remote(path: str) -> bool:
    return path.lower().startswith(REMOTE_PREFIXES)


def source_exists(path: str) -> bool:
    return is_remote(path) or os.path.exists(path)


def infer_format(path: str, options: dict[str, Any] | None = None) -> str:
    options = options or {}
    if options.get("format"):
        fmt = options["format"]
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format '{fmt}'. Supported: {', '.join(SUPPORTED_FORMATS)}")
        return fmt
    clean = path.split("?", 1)[0].lower()
    if clean.endswith(".gz"):
        clean = clean[:-3]
    ext = os.path.splitext(clean)[1]
    if ext in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[ext]
    raise ValueError(
        f"Cannot tell the file format of '{path}'. Pass a format explicitly "
        f"(one of: {', '.join(SUPPORTED_FORMATS)})."
    )


def source_signature(path: str) -> str:
    """Cheap fingerprint used to invalidate cached results when a file changes."""
    if is_remote(path):
        return f"remote:{path}"
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


def _csv_kwargs(fmt: str, options: dict[str, Any]) -> dict[str, Any]:
    separator = options.get("separator") or ("\t" if fmt == "tsv" else ",")
    if separator == "\\t":
        separator = "\t"
    kwargs: dict[str, Any] = {
        "separator": separator,
        "has_header": bool(options.get("has_header", True)),
        "skip_rows": int(options.get("skip_rows") or 0),
        "try_parse_dates": bool(options.get("try_parse_dates", True)),
        "infer_schema_length": int(options.get("infer_schema_length") or 10000),
    }
    if options.get("null_values"):
        kwargs["null_values"] = options["null_values"]
    if options.get("quote_char") is not None and options.get("quote_char") != "":
        kwargs["quote_char"] = options["quote_char"]
    if options.get("ignore_errors"):
        kwargs["ignore_errors"] = True
    return kwargs


def _db_query(options: dict[str, Any]) -> str:
    if options.get("query"):
        return options["query"]
    table = options.get("table")
    if not table:
        raise ValueError("Database sources need a 'table' or a 'query' option.")
    return f"SELECT * FROM {quote_ident(table)}"


def quote_ident(name: str) -> str:
    """Quote an SQL identifier (works for SQLite, DuckDB and Postgres)."""
    return '"' + str(name).replace('"', '""') + '"'


def list_tables(path: str, fmt: str) -> list[str]:
    if fmt == "sqlite":
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall()
        return [r[0] for r in rows]
    if fmt == "duckdb":
        import duckdb

        with duckdb.connect(path, read_only=True) as conn:
            return [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables ORDER BY table_name").fetchall()]
    if fmt == "excel":
        import fastexcel

        return list(fastexcel.read_excel(path).sheet_names)
    return []


def load_source(path: str, options: dict[str, Any] | None = None) -> pl.LazyFrame:
    options = options or {}
    fmt = infer_format(path, options)

    if fmt in ("csv", "tsv"):
        kwargs = _csv_kwargs(fmt, options)
        encoding = (options.get("encoding") or "utf8").lower()
        if encoding in _NATIVE_CSV_ENCODINGS:
            if encoding == "utf8-lossy":
                kwargs["encoding"] = "utf8-lossy"
            return pl.scan_csv(path, **kwargs)
        # Other encodings (latin-1, cp1252, utf-16, ...) need the eager reader.
        return pl.read_csv(path, encoding=encoding, **kwargs).lazy()
    if fmt == "parquet":
        return pl.scan_parquet(path)
    if fmt == "ipc":
        return pl.scan_ipc(path)
    if fmt == "ndjson":
        return pl.scan_ndjson(path, infer_schema_length=int(options.get("infer_schema_length") or 10000))
    if fmt == "json":
        return pl.read_json(path).lazy()
    if fmt == "excel":
        sheet = options.get("sheet_name") or None
        kwargs: dict[str, Any] = {}
        if sheet:
            kwargs["sheet_name"] = sheet
        else:
            kwargs["sheet_id"] = int(options.get("sheet_id") or 1)
        return pl.read_excel(path, **kwargs).lazy()
    if fmt == "sqlite":
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            return pl.read_database(_db_query(options), connection=conn).lazy()
    if fmt == "duckdb":
        import duckdb

        with duckdb.connect(path, read_only=True) as conn:
            return conn.sql(_db_query(options)).pl().lazy()
    raise ValueError(f"Unsupported format '{fmt}'")


def load_statement(path: str, options: dict[str, Any] | None = None) -> tuple[list[str], str]:
    """Return (extra imports, Python expression) that reproduce load_source."""
    options = options or {}
    fmt = infer_format(path, options)
    p = repr(path)
    if fmt in ("csv", "tsv"):
        kwargs = _csv_kwargs(fmt, options)
        encoding = (options.get("encoding") or "utf8").lower()
        if encoding not in ("utf8", "utf-8"):
            kwargs["encoding"] = encoding
        defaults = {"separator": ",", "has_header": True, "skip_rows": 0, "infer_schema_length": 10000}
        args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items() if defaults.get(k, object()) != v)
        return [], f"pl.read_csv({p}{', ' + args if args else ''})"
    if fmt == "parquet":
        return [], f"pl.read_parquet({p})"
    if fmt == "ipc":
        return [], f"pl.read_ipc({p})"
    if fmt == "ndjson":
        return [], f"pl.read_ndjson({p})"
    if fmt == "json":
        return [], f"pl.read_json({p})"
    if fmt == "excel":
        sheet = options.get("sheet_name")
        arg = f"sheet_name={sheet!r}" if sheet else f"sheet_id={int(options.get('sheet_id') or 1)}"
        return [], f"pl.read_excel({p}, {arg})"
    if fmt == "sqlite":
        return ["import sqlite3"], f"pl.read_database({_db_query(options)!r}, connection=sqlite3.connect({p}))"
    if fmt == "duckdb":
        return ["import duckdb"], f"duckdb.connect({p}, read_only=True).sql({_db_query(options)!r}).pl()"
    raise ValueError(f"Unsupported format '{fmt}'")


# ---------------------------------------------------------------- exports

EXPORT_FORMATS = {
    "csv": ("text/csv", ".csv"),
    "tsv": ("text/tab-separated-values", ".tsv"),
    "parquet": ("application/vnd.apache.parquet", ".parquet"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "json": ("application/json", ".json"),
    "ndjson": ("application/x-ndjson", ".ndjson"),
    "ipc": ("application/vnd.apache.arrow.file", ".arrow"),
}


def write_export(lf: pl.LazyFrame, fmt: str, out_path: str) -> None:
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format '{fmt}'. Supported: {', '.join(EXPORT_FORMATS)}")
    # Streaming sinks keep memory flat for large results; fall back to collect
    # for plans the streaming engine cannot run.
    sinks = {
        "csv": lambda: lf.sink_csv(out_path),
        "tsv": lambda: lf.sink_csv(out_path, separator="\t"),
        "parquet": lambda: lf.sink_parquet(out_path),
        "ndjson": lambda: lf.sink_ndjson(out_path),
        "ipc": lambda: lf.sink_ipc(out_path),
    }
    if fmt in sinks:
        try:
            sinks[fmt]()
            return
        except pl.exceptions.InvalidOperationError:
            pass
    df = lf.collect()
    if fmt == "csv":
        df.write_csv(out_path)
    elif fmt == "tsv":
        df.write_csv(out_path, separator="\t")
    elif fmt == "parquet":
        df.write_parquet(out_path)
    elif fmt == "ndjson":
        df.write_ndjson(out_path)
    elif fmt == "ipc":
        df.write_ipc(out_path)
    elif fmt == "json":
        df.write_json(out_path)
    elif fmt == "xlsx":
        if df.height > 1_048_575:
            raise ValueError(f"Excel sheets hold at most 1,048,575 rows; this result has {df.height:,}. Export as CSV or Parquet instead.")
        df.write_excel(out_path, autofit=True)
