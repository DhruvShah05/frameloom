"""End-to-end tests for formats, every operation, profiling, charts, undo/redo,
grid views, exports and the local-only guard.

The parity test is the important one: for every operation it checks that the
script produced by "Export code" gives exactly the same table as the app.
"""
import io
import os
import sqlite3

import duckdb
import polars as pl
import pytest
from fastapi.testclient import TestClient
from polars.testing import assert_frame_equal

from backend.main import app, engine, projects

client = TestClient(app)


@pytest.fixture
def people(tmp_path):
    df = pl.DataFrame({
        "id": [1, 2, 3, 4, 5, 6],
        "name": ["Alice", " bob ", "Charlie", "alice", None, "Eve"],
        "age": [30, 35, None, 42, 38, 29],
        "dept": ["Eng", "Sales", "Eng", "Mkt", "Sales", "Eng"],
        "salary": ["100,000", "85000", "90000", "110000", "95000", "70000"],
        "joined": ["2020-01-05", "2019-03-10", "2021-07-01", "2018-11-20", "2022-02-14", "2023-05-30"],
        "tags": ["a|b", "c", "a|c|d", "b", "d|e", "a"],
    })
    path = tmp_path / "people.csv"
    df.write_csv(path)
    return str(path)


@pytest.fixture
def sales(tmp_path):
    df = pl.DataFrame({
        "date": pl.date_range(pl.date(2023, 1, 1), pl.date(2023, 1, 10), "1d", eager=True),
        "store": ["A", "B"] * 5,
        "units": [5, 3, None, 8, 2, 7, 6, None, 4, 9],
        "price": [1.5, 2.0, 1.5, 2.5, 3.0, 1.0, 2.0, 2.5, 1.5, 3.5],
    })
    path = tmp_path / "sales.parquet"
    df.write_parquet(path)
    return str(path)


def _load(path, **options):
    r = client.post("/project/load", json={"path": path, "options": options})
    assert r.status_code == 200, r.text
    p = r.json()
    return p["id"], p["branches"]["main"]["head_node_id"]


def _op(pid, parent, type_, **params):
    r = client.post(f"/project/{pid}/op", json={"parent_node_id": parent, "operation": {"type": type_, "params": params}})
    return r


def _ok_op(pid, parent, type_, **params):
    r = _op(pid, parent, type_, **params)
    assert r.status_code == 200, f"{type_}: {r.text}"
    return r.json()["id"]


def _engine_df(pid, node_id):
    return engine.build_plan(projects[pid], node_id).collect()


def _script_df(pid, node_id):
    code = client.post(f"/project/{pid}/export/code", json={"node_id": node_id}).json()["code"]
    scope = {}
    exec(compile(code, "<export>", "exec"), scope)
    return scope["df"], code


def _assert_parity(pid, node_id):
    expected = _engine_df(pid, node_id)
    actual, code = _script_df(pid, node_id)
    try:
        assert_frame_equal(actual, expected, check_dtypes=True)
    except AssertionError as e:
        raise AssertionError(f"Exported script differs from app output\n\n{code}\n\n{e}")


# ---------------------------------------------------------------- formats

def test_all_file_formats_load(tmp_path):
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    df.write_csv(tmp_path / "f.csv")
    df.write_csv(tmp_path / "f.tsv", separator="\t")
    df.write_csv(tmp_path / "semi.txt", separator=";")
    df.write_parquet(tmp_path / "f.parquet")
    df.write_ipc(tmp_path / "f.arrow")
    df.write_json(tmp_path / "f.json")
    df.write_ndjson(tmp_path / "f.ndjson")
    df.write_excel(tmp_path / "f.xlsx", worksheet="Data")
    with sqlite3.connect(tmp_path / "f.db") as conn:
        conn.execute("CREATE TABLE things (a INTEGER, b TEXT)")
        conn.executemany("INSERT INTO things VALUES (?, ?)", df.rows())
    with duckdb.connect(str(tmp_path / "f.duckdb")) as conn:
        conn.register("src", df)
        conn.execute("CREATE TABLE things AS SELECT * FROM src")

    cases = [
        ("f.csv", {}), ("f.tsv", {}), ("semi.txt", {"separator": ";"}), ("f.parquet", {}), ("f.arrow", {}),
        ("f.json", {}), ("f.ndjson", {}), ("f.xlsx", {"sheet_name": "Data"}), ("f.db", {"table": "things"}),
        ("f.duckdb", {"table": "things"}), ("f.duckdb", {"query": "SELECT a, b FROM things ORDER BY a"}),
    ]
    for name, opts in cases:
        pid, root = _load(str(tmp_path / name), **opts)
        view = client.get(f"/project/{pid}/data", params={"node_id": root}).json()
        assert view["total_rows"] == 3, name
        assert [r["b"] for r in view["data"]] == ["x", "y", "z"], name
        node = _ok_op(pid, root, "filter_rows", column="a", operator="ge", value=2)
        _assert_parity(pid, node)


def test_inspect_lists_sheets_and_tables(tmp_path):
    pl.DataFrame({"a": [1]}).write_excel(tmp_path / "w.xlsx", worksheet="First")
    r = client.post("/source/inspect", json={"path": str(tmp_path / "w.xlsx")}).json()
    assert r["format"] == "excel" and r["tables"] == ["First"]
    with sqlite3.connect(tmp_path / "d.sqlite") as conn:
        conn.execute("CREATE TABLE t1 (x)")
        conn.execute("CREATE TABLE t2 (y)")
    r = client.post("/source/inspect", json={"path": str(tmp_path / "d.sqlite")}).json()
    assert r["tables"] == ["t1", "t2"] and r["needs_table"]


def test_unknown_format_and_missing_file(tmp_path):
    (tmp_path / "x.weird").write_text("hi")
    r = client.post("/project/load", json={"path": str(tmp_path / "x.weird")})
    assert r.status_code == 400 and "format" in r.json()["detail"]
    assert client.post("/project/load", json={"path": str(tmp_path / "nope.csv")}).status_code == 404


def test_latin1_csv(tmp_path):
    p = tmp_path / "latin.csv"
    p.write_bytes("name,city\nJosé,São Paulo\n".encode("latin-1"))
    pid, root = _load(str(p), encoding="latin-1")
    rows = client.get(f"/project/{pid}/data", params={"node_id": root}).json()["data"]
    assert rows[0]["city"] == "São Paulo"


# ------------------------------------------------------------- operations

OPERATION_CASES = [
    ("drop_column", {"columns": ["tags"]}),
    ("select_columns", {"columns": ["name", "id"]}),
    ("rename_column", {"mapping": {"dept": "department"}}),
    ("cast_column", {"columns": ["salary"], "dtype": "Float64", "clean": True}),
    ("cast_column", {"columns": ["joined"], "dtype": "Date"}),
    ("cast_column", {"columns": ["id"], "dtype": "String"}),
    ("add_column", {"name": "age_next", "expression": "age + 1"}),
    ("sort", {"by": [{"column": "dept"}, {"column": "age", "descending": True}]}),
    ("filter_rows", {"column": "age", "operator": "gt", "value": "31"}),
    ("filter_rows", {"column": "age", "operator": "gt", "value": "31.5"}),
    ("filter_rows", {"column": "name", "operator": "contains", "value": "li", "ignore_case": True}),
    ("filter_rows", {"column": "dept", "operator": "in", "value": "Eng, Mkt"}),
    ("filter_rows", {"column": "age", "operator": "between", "value": 30, "value2": 40}),
    ("filter_rows", {"column": "name", "operator": "is_null"}),
    ("filter_rows", {"conditions": [{"column": "dept", "operator": "eq", "value": "Eng"},
                                    {"column": "age", "operator": "lt", "value": 30}], "combine": "or"}),
    ("filter_rows", {"column": "name", "operator": "regex", "value": "^[A-Z]"}),
    ("limit_rows", {"n": 3}),
    ("limit_rows", {"n": 2, "by": "age", "descending": True}),
    ("sample_rows", {"n": 3, "seed": 1}),
    ("drop_duplicates", {"columns": ["dept"], "keep": "first"}),
    ("edit_cell", {"row_index": 2, "column": "age", "value": "33"}),
    ("edit_cell", {"row_index": 0, "column": "name", "value": "Alicia"}),
    ("text_transform", {"columns": ["name"], "transform": "strip"}),
    ("text_transform", {"columns": ["name", "dept"], "transform": "upper"}),
    ("text_transform", {"columns": ["name"], "transform": "length", "new_column": "name_len"}),
    ("split_column", {"column": "tags", "delimiter": "|", "parts": 3}),
    ("split_column", {"column": "joined", "delimiter": "-", "names": "year, month, day", "drop_original": True}),
    ("regex_extract", {"column": "joined", "pattern": r"^(\d{4})", "new_column": "year"}),
    ("find_replace", {"columns": ["dept"], "find": "Eng", "replace": "Engineering"}),
    ("find_replace", {"columns": ["salary"], "find": r"[^0-9]", "replace": "", "mode": "regex"}),
    ("find_replace", {"columns": ["age"], "find": "42", "replace": "43", "mode": "whole_cell"}),
    ("parse_dates", {"column": "joined", "to": "date"}),
    ("parse_dates", {"column": "joined", "to": "datetime", "format": "%Y-%m-%d", "new_column": "joined_ts"}),
    ("fill_missing", {"columns": ["age"], "strategy": "mean"}),
    ("fill_missing", {"columns": ["age"], "strategy": "median"}),
    ("fill_missing", {"columns": ["age"], "strategy": "forward"}),
    ("fill_missing", {"columns": ["name"], "strategy": "value", "value": "unknown"}),
    ("fill_missing", {"columns": ["age"], "strategy": "value", "value": "0"}),
    ("drop_missing", {"how": "any"}),
    ("drop_missing", {"columns": ["age", "name"], "how": "all"}),
    ("bin_column", {"column": "age", "breaks": "30, 40", "labels": "young, mid, senior"}),
    ("bin_column", {"column": "id", "method": "quantiles", "bins": 2}),
    ("group_by", {"by": ["dept"], "aggs": [{"column": "age", "agg": "mean"}, {"agg": "count"}]}),
    ("pivot", {"index": ["id"], "on": "dept", "values": "age", "agg": "sum"}),
    ("unpivot", {"index": ["id"], "on": ["dept", "name"]}),
    ("window", {"function": "row_number", "partition_by": ["dept"], "order_by": "id"}),
    ("window", {"function": "rank", "column": "age", "descending": True}),
    ("window", {"function": "cum_sum", "column": "age", "order_by": "id"}),
    ("window", {"function": "lag", "column": "age", "partition_by": ["dept"], "order_by": "id"}),
    ("window", {"function": "pct_change", "column": "id"}),
    ("sql", {"query": "SELECT dept, COUNT(*) AS n FROM df GROUP BY dept ORDER BY dept"}),
    ("sql", {"query": "SELECT * FROM df WHERE age > 30 ORDER BY id", "dialect": "polars"}),
    ("fill_missing", {"columns": ["age"], "strategy": "mean", "group_by": ["dept"]}),
    ("fill_missing", {"columns": ["age"], "strategy": "forward", "group_by": ["dept"]}),
    ("one_hot_encode", {"columns": ["dept"]}),
    ("one_hot_encode", {"columns": ["dept", "name"], "max_categories": 2, "drop_original": False}),
    ("label_encode", {"columns": ["dept"]}),
    ("label_encode", {"columns": ["dept"], "order": "frequency", "suffix": ""}),
    ("scale", {"columns": ["age", "id"], "method": "standard"}),
    ("scale", {"columns": ["age"], "method": "minmax", "suffix": "_scaled"}),
    ("scale", {"columns": ["age"], "method": "robust"}),
    ("clip_outliers", {"columns": ["age"], "method": "iqr", "k": 0.5}),
    ("clip_outliers", {"columns": ["age"], "method": "percentile", "lower_pct": 10, "upper_pct": 90}),
    ("clip_outliers", {"columns": ["age", "id"], "method": "values", "lower": 2, "upper": 40}),
    ("train_test_split", {"test_size": 0.33, "seed": 7}),
    ("train_test_split", {"test_size": 0.2, "val_size": 0.2, "stratify": "dept"}),
    ("train_test_split", {"test_size": 0.5, "group": "dept", "column": "fold"}),
    ("date_parts", {"column": "joined", "parts": ["year", "quarter", "month", "week", "day", "day_of_year", "weekday", "is_weekend"]}),
]


@pytest.mark.parametrize("op_type,params", OPERATION_CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(OPERATION_CASES)])
def test_operation_matches_exported_code(people, op_type, params):
    pid, root = _load(people)
    node = _ok_op(pid, root, op_type, **params)
    _assert_parity(pid, node)
    label = projects[pid].nodes[node].operation.label
    assert label and label != op_type, "steps should get a readable label"


def test_operation_results_are_correct(people):
    pid, root = _load(people)
    n = _ok_op(pid, root, "cast_column", columns=["salary"], dtype="Int64", clean=True)
    assert _engine_df(pid, n)["salary"].to_list() == [100000, 85000, 90000, 110000, 95000, 70000]
    n = _ok_op(pid, root, "group_by", by=["dept"], aggs=[{"column": "id", "agg": "count", "alias": "n"}])
    assert dict(_engine_df(pid, n).rows()) == {"Eng": 3, "Sales": 2, "Mkt": 1}
    n = _ok_op(pid, root, "edit_cell", row_index=2, column="age", value=33)
    assert _engine_df(pid, n)["age"][2] == 33
    n = _ok_op(pid, root, "filter_rows", column="name", operator="contains", value="(")  # literal, not regex
    assert _engine_df(pid, n).height == 0


def test_time_series_ops_match_code(sales):
    pid, root = _load(sales)
    for type_, params in [
        ("resample", {"time_col": "date", "freq": "2d", "val_col": "units", "agg": "sum"}),
        ("rolling", {"time_col": "date", "window": "3d", "val_col": "price", "agg": "mean"}),
        ("fill_gaps", {"time_col": "date", "val_col": "units", "method": "forward"}),
        ("filter_rows", {"column": "date", "operator": "ge", "value": "2023-01-05"}),
        ("filter_rows", {"column": "date", "operator": "in", "value": "2023-01-01, 2023-01-03"}),
        ("edit_cell", {"row_index": 0, "column": "date", "value": "2024-01-01"}),
        ("window", {"function": "cum_sum", "column": "units", "partition_by": ["store"], "order_by": "date"}),
    ]:
        node = _ok_op(pid, root, type_, **params)
        _assert_parity(pid, node)
    n = _ok_op(pid, root, "filter_rows", column="date", operator="ge", value="2023-01-05")
    assert _engine_df(pid, n).height == 6
    n = _ok_op(pid, root, "rolling", time_col="date", window="3d", val_col="price", agg="mean")
    assert "price_rolling_mean" in _engine_df(pid, n).columns, "rolling keeps the other columns"


def test_join_and_append_match_code(people, tmp_path):
    lookup = tmp_path / "depts.csv"
    pl.DataFrame({"dept": ["Eng", "Sales"], "floor": [3, 1]}).write_csv(lookup)
    extra = tmp_path / "extra.csv"
    pl.DataFrame({"id": [7], "name": ["Zed"], "bonus": [5]}).write_csv(extra)
    pid, root = _load(people)
    for how in ("left", "inner", "anti", "full"):
        node = _ok_op(pid, root, "join", path=str(lookup), on="dept", how=how)
        _assert_parity(pid, node)
    node = _ok_op(pid, root, "append_rows", path=str(extra))
    _assert_parity(pid, node)
    assert _engine_df(pid, node).height == 7


@pytest.mark.parametrize("type_,params,fragment", [
    ("drop_column", {"columns": ["nope"]}, "not found"),
    ("filter_rows", {"column": "age", "operator": "gt", "value": "abc"}, "Cannot convert"),
    ("filter_rows", {"column": "age", "operator": "bogus", "value": 1}, "Unsupported filter operator"),
    ("group_by", {"by": ["dept"], "aggs": [{"column": "age", "agg": "__class__"}]}, "Unsupported aggregation"),
    ("rolling", {"time_col": "joined", "window": "2d", "val_col": "age", "agg": "__init__"}, "Unsupported aggregation"),
    ("regex_extract", {"column": "name", "pattern": "("}, "Invalid regular expression"),
    ("add_column", {"name": "x", "expression": "age +"}, "Invalid expression"),
    ("rename_column", {"mapping": {"id": "name"}}, "already exists"),
    ("find_replace", {"columns": ["age"], "find": "1", "replace": "2"}, "not text"),
    ("cast_column", {"columns": ["age"], "dtype": "Wizard"}, "Unsupported type"),
    ("not_an_op", {}, "Unknown operation"),
    ("sql", {"query": "SELECT nope FROM df"}, ""),
])
def test_bad_operations_are_rejected_and_not_saved(people, type_, params, fragment):
    pid, root = _load(people)
    before = len(projects[pid].nodes)
    r = _op(pid, root, type_, **params)
    assert r.status_code == 400, r.text
    assert fragment in r.json()["detail"]
    assert len(projects[pid].nodes) == before


# ------------------------------------------------------- grid / undo / redo

def test_view_sort_filter_paging_and_row_numbers(people):
    pid, root = _load(people)
    r = client.post(f"/project/{pid}/view", json={
        "node_id": root, "offset": 0, "limit": 2,
        "sort": [{"column": "age", "descending": True}],
        "filters": [{"column": "dept", "operator": "ne", "value": "Mkt"}],
    }).json()
    assert r["total_rows"] == 5
    assert [row["age"] for row in r["data"]] == [38, 35]
    # __row_nr points back at the unsorted row, so edits land on the right cell.
    assert [row["__row_nr"] for row in r["data"]] == [4, 1]
    node = _ok_op(pid, root, "edit_cell", row_index=r["data"][0]["__row_nr"], column="age", value=39)
    assert _engine_df(pid, node)["age"][4] == 39


def test_undo_redo(people):
    pid, root = _load(people)
    a = _ok_op(pid, root, "drop_column", columns=["tags"])
    b = _ok_op(pid, a, "drop_column", columns=["salary"])
    r = client.post(f"/project/{pid}/undo").json()
    assert r["node_id"] == a and r["can_redo"] and r["can_undo"]
    r = client.post(f"/project/{pid}/undo").json()
    assert r["node_id"] == root and not r["can_undo"]
    assert client.post(f"/project/{pid}/undo").status_code == 400
    r = client.post(f"/project/{pid}/redo").json()
    assert r["node_id"] == a
    r = client.post(f"/project/{pid}/redo").json()
    assert r["node_id"] == b and not r["can_redo"]
    # A new step after undo clears the redo stack.
    client.post(f"/project/{pid}/undo")
    _ok_op(pid, a, "drop_column", columns=["dept"])
    assert client.post(f"/project/{pid}/redo").status_code == 400


def test_branch_names_are_validated(people):
    pid, root = _load(people)
    assert client.post(f"/project/{pid}/branch", json={"name": "exp-1", "from_node_id": root}).status_code == 200
    assert client.post(f"/project/{pid}/branch", json={"name": "exp-1", "from_node_id": root}).status_code == 409
    assert client.post(f"/project/{pid}/branch", json={"name": "", "from_node_id": root}).status_code == 400


# --------------------------------------------------------- profile / charts

def test_profile_finds_quality_issues(people):
    pid, root = _load(people)
    prof = client.get(f"/project/{pid}/profile", params={"node_id": root}).json()
    cols = {c["name"]: c for c in prof["columns"]}
    assert prof["row_count"] == 6
    assert cols["age"]["nulls"] == 1 and cols["age"]["kind"] == "numeric"
    assert cols["age"]["min"] == 29 and cols["age"]["max"] == 42
    assert sum(b["count"] for b in cols["age"]["histogram"]) == 5
    name_issues = " ".join(i["message"] for i in cols["name"]["issues"])
    assert "spaces" in name_issues and "inconsistently" in name_issues
    assert any("numeric" in i["message"] for i in cols["salary"]["issues"])
    assert cols["joined"]["kind"] == "temporal"  # the CSV reader already parsed it
    raw_pid, raw_root = _load(people, try_parse_dates=False)
    raw = {c["name"]: c for c in client.get(f"/project/{raw_pid}/profile", params={"node_id": raw_root}).json()["columns"]}
    assert any("dates" in i["message"] for i in raw["joined"]["issues"])
    # Suggested fixes are real operations that apply cleanly.
    for col in cols.values():
        for issue in col["issues"]:
            if issue.get("fix"):
                _ok_op(pid, root, issue["fix"]["type"], **issue["fix"]["params"])


def test_charts(sales, people):
    pid, root = _load(sales)
    h = client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "histogram", "x": "price", "bins": 5}).json()
    assert sum(b["count"] for b in h["bins"]) == 10
    b = client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "bar", "x": "store", "y": "units", "agg": "sum"}).json()
    assert {p["x"]: p["y"] for p in b["points"]} == {"A": 17, "B": 27}
    line = client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "line", "x": "date", "y": "price", "agg": "none"}).json()
    assert len(line["points"]) == 10
    sc = client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "scatter", "x": "units", "y": "price"}).json()
    assert len(sc["points"]) == 8
    assert client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "bar", "x": "store", "y": "price", "agg": "mro"}).status_code == 400
    th = client.post(f"/project/{pid}/chart", json={"node_id": root, "type": "histogram", "x": "date"}).json()
    assert sum(b["count"] for b in th["bins"]) == 10


# ------------------------------------------------------------------ exports

@pytest.mark.parametrize("fmt", ["csv", "tsv", "parquet", "xlsx", "json", "ndjson", "ipc"])
def test_data_export_round_trips(people, fmt):
    pid, root = _load(people)
    node = _ok_op(pid, root, "filter_rows", column="dept", operator="eq", value="Eng")
    r = client.post(f"/project/{pid}/export/data", json={"node_id": node, "format": fmt})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    buf = io.BytesIO(r.content)
    reader = {"csv": pl.read_csv, "tsv": lambda b: pl.read_csv(b, separator="\t"), "parquet": pl.read_parquet,
              "xlsx": pl.read_excel, "json": pl.read_json, "ndjson": pl.read_ndjson, "ipc": pl.read_ipc}[fmt]
    out = reader(buf)
    assert out.height == 3 and out["name"].to_list() == ["Alice", "Charlie", "Eve"]


def test_float_nan_and_inf_are_json_safe(tmp_path):
    p = tmp_path / "nan.parquet"
    pl.DataFrame({"x": [1.0, float("nan"), float("inf")]}).write_parquet(p)
    pid, root = _load(str(p))
    r = client.get(f"/project/{pid}/data", params={"node_id": root})
    assert r.status_code == 200
    assert r.json()["data"] == [{"x": 1.0}, {"x": None}, {"x": None}]
    assert client.get(f"/project/{pid}/profile", params={"node_id": root}).status_code == 200


# ---------------------------------------------------------- cache / recipes

def _load_unpinned(path):
    r = client.post("/project/load", json={"path": path, "snapshot": False})
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["branches"]["main"]["head_node_id"]


def _touch_newer(p):
    os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 10_000_000))


def test_cache_is_invalidated_when_source_changes(tmp_path):
    p = tmp_path / "live.csv"
    p.write_text("a\n1\n2\n")
    pid, root = _load_unpinned(str(p))
    node = _ok_op(pid, root, "group_by", by=["a"])  # expensive -> cached
    assert _engine_df(pid, node).height == 2
    assert node in engine.cache
    p.write_text("a\n1\n2\n3\n")
    os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 10_000_000))
    assert _engine_df(pid, node).height == 3


def test_cache_is_bounded():
    from backend.engine import ExecutionEngine
    from backend.models import Project

    small = ExecutionEngine(max_cache_bytes=10_000)
    proj = Project(id="p", source_path="x.csv", source_hash="", head="main")
    for i in range(20):
        small._cache_put(proj, f"n{i}", pl.DataFrame({"a": list(range(200))}))
    assert small._cache_bytes <= 10_000
    assert "n19" in small.cache and "n0" not in small.cache


def test_recipe_round_trip_and_validation(people, tmp_path):
    pid, root = _load(people)
    a = _ok_op(pid, root, "rename_column", mapping={"dept": "department"})
    b = _ok_op(pid, a, "filter_rows", column="age", operator="gt", value=30)
    recipe = client.post(f"/project/{pid}/recipe/save", json={"name": "adults", "node_id": b}).json()
    # Expected schema is the INPUT file's columns, not the output's.
    assert "dept" in recipe["expected_schema"] and "department" not in recipe["expected_schema"]

    same_shape = tmp_path / "people2.csv"
    pl.read_csv(people).head(4).write_csv(same_shape)
    r = client.post("/recipe/apply", json={"recipe_id": recipe["id"], "new_source_path": str(same_shape)}).json()
    assert r["status"] == "warning" and r["warnings"] == ["This file is different from the one the recipe was built on."]
    r = client.post("/recipe/apply", json={"recipe_id": recipe["id"], "new_source_path": str(same_shape), "force": True}).json()
    assert r["status"] == "success"
    new_pid = r["project"]["id"]
    head = r["project"]["branches"]["main"]["head_node_id"]
    assert _engine_df(new_pid, head)["department"].to_list() == ["Sales", "Mkt"]

    broken = tmp_path / "broken.csv"
    pl.DataFrame({"other": [1]}).write_csv(broken)
    before = len(projects)
    r = client.post("/recipe/apply", json={"recipe_id": recipe["id"], "new_source_path": str(broken)}).json()
    assert r["status"] == "warning" and any("Missing column 'dept'" in w for w in r["warnings"])
    assert len(projects) == before, "a warning must not leave a half-made project behind"


def test_audit_handles_awkward_column_names(tmp_path):
    # Parquet keeps the names exactly (Polars' CSV reader doesn't unescape "" in headers).
    p = tmp_path / "odd.parquet"
    pl.DataFrame({"order id": [1, 2, 3], 'we"ird': ["a", "b", "c"], "select": [1, 2, 3]}).write_parquet(p)
    pid, root = _load(str(p))
    edited = _ok_op(pid, root, "edit_cell", row_index=1, column='we"ird', value="B")
    dropped = _ok_op(pid, edited, "filter_rows", column="order id", operator="ne", value=3)
    r = client.post(f"/project/{pid}/audit/compare", json={"node_a": root, "node_b": dropped, "key_column": "order id"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == {"match": 1, "mismatch": 1, "missing_in_a": 0, "missing_in_b": 1}
    mismatch = next(row for row in body["data"] if row["_audit_status"] == "mismatch")
    assert mismatch["_mismatched_columns"] == ['we"ird']


def test_audit_compares_columns_whose_type_changed(people):
    pid, root = _load(people)
    cast = _ok_op(pid, root, "cast_column", columns=["salary"], dtype="Float64", clean=True)
    r = client.post(f"/project/{pid}/audit/compare", json={"node_a": root, "node_b": cast, "key_column": "id"})
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["data"]}
    assert rows[1]["_mismatched_columns"] == ["salary"]  # "100,000" vs "100000.0"


# ------------------------------------------------------------------ security

def test_rejects_foreign_origins_and_hosts(people):
    r = client.post("/project/load", json={"path": people}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.get("/projects", headers={"Host": "evil.example"})
    assert r.status_code == 403
    r = client.get("/projects", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ------------------------------------------------------- reproducible sources

def test_pinned_versions_never_change(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a\n1\n2\n")
    pid, root = _load(str(p))
    project = projects[pid]
    assert project.snapshot_path and os.path.exists(project.snapshot_path)
    node = _ok_op(pid, root, "filter_rows", column="a", operator="ge", value=1)
    p.write_text("a\n1\n2\n3\n")
    _touch_newer(p)
    assert _engine_df(pid, node).height == 2  # still the frozen data
    status = client.get(f"/project/{pid}/source").json()
    assert status["status"] == "pinned" and status["original_changed"] is True


def test_identical_files_share_one_snapshot(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("x\n1\n")
    b.write_text("x\n1\n")
    pa, _ = _load(str(a))
    pb, _ = _load(str(b))
    assert projects[pa].source_sha256 == projects[pb].source_sha256


def test_unpinned_change_is_detected_and_can_be_accepted(tmp_path):
    from backend.core import SourceChangedError
    from backend.main import loom

    p = tmp_path / "data.csv"
    p.write_text("a\n1\n2\n")
    pid, root = _load_unpinned(str(p))
    assert client.get(f"/project/{pid}/source").json()["status"] == "ok"
    os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 10_000_000))
    assert client.get(f"/project/{pid}/source").json()["status"] == "ok", "touching is not changing"
    p.write_text("a\n1\n2\n3\n")
    _touch_newer(p)
    assert client.get(f"/project/{pid}/source").json()["status"] == "changed"
    with pytest.raises(SourceChangedError):
        loom.dataframe(projects[pid])
    assert loom.dataframe(projects[pid], allow_changed_source=True).height == 3
    r = client.post(f"/project/{pid}/source/accept", json={"snapshot": True}).json()
    assert r["status"] == "pinned"
    assert [h.reason for h in projects[pid].source_history] == ["loaded", "pinned"]


def test_database_and_remote_style_sources_are_frozen_to_parquet(tmp_path):
    db = tmp_path / "d.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    pid, root = _load(str(db), table="t")
    assert projects[pid].snapshot_path.endswith(".parquet")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO t VALUES (3)")
    assert _engine_df(pid, root).height == 2


def test_exported_code_records_provenance(people):
    pid, root = _load(people)
    code = client.post(f"/project/{pid}/export/code", json={"node_id": root}).json()["code"]
    assert projects[pid].source_sha256 in code and "frozen copy" in code


# ------------------------------------------------------- row-following edits

def _row(pid, node, i):
    return client.get(f"/project/{pid}/data", params={"node_id": node, "offset": i, "limit": 1}).json()["data"][0]


def test_edit_follows_the_row_not_its_position(people):
    pid, root = _load(people)
    target = _row(pid, root, 2)  # Charlie, age null
    sorted_node = _ok_op(pid, root, "sort", by=[{"column": "id", "descending": True}])
    node = _ok_op(pid, sorted_node, "edit_cell", row=target, column="age", value=50)
    df = _engine_df(pid, node)
    assert df.filter(pl.col("name") == "Charlie")["age"].to_list() == [50]
    assert df["age"].to_list().count(50) == 1
    _assert_parity(pid, node)
    assert "where id=3" in projects[pid].nodes[node].operation.label


def test_edit_with_dates_and_nulls_matches_exported_code(sales):
    pid, root = _load(sales)
    target = _row(pid, root, 2)  # units is null here
    node = _ok_op(pid, root, "edit_cell", row=target, column="units", value=11)
    assert _engine_df(pid, node)["units"][2] == 11
    _assert_parity(pid, node)


def test_edit_of_a_row_that_is_not_there_is_rejected(people):
    pid, root = _load(people)
    target = _row(pid, root, 0)
    filtered = _ok_op(pid, root, "filter_rows", column="id", operator="gt", value=1)
    r = _op(pid, filtered, "edit_cell", row=target, column="age", value=1)
    assert r.status_code == 400 and "isn't in the data" in r.json()["detail"]



# --------------------------------------------------------------- ML prep

def test_ml_steps_produce_the_right_values(people):
    pid, root = _load(people)
    oh = _engine_df(pid, _ok_op(pid, root, "one_hot_encode", columns=["dept"]))
    assert {"dept_Eng", "dept_Mkt", "dept_Sales"} <= set(oh.columns) and "dept" not in oh.columns
    assert oh["dept_Eng"].to_list() == [1, 0, 1, 0, 0, 1]

    le = _engine_df(pid, _ok_op(pid, root, "label_encode", columns=["dept"]))
    assert le["dept_code"].to_list() == [0, 2, 0, 1, 2, 0]

    sc = _engine_df(pid, _ok_op(pid, root, "scale", columns=["age"], method="minmax"))
    assert sc["age"].min() == 0.0 and sc["age"].max() == 1.0

    cl = _engine_df(pid, _ok_op(pid, root, "clip_outliers", columns=["age"], method="values", lower=30, upper=40))
    assert cl["age"].drop_nulls().to_list() == [30, 35, 40, 38, 30]

    fm = _engine_df(pid, _ok_op(pid, root, "fill_missing", columns=["age"], strategy="mean", group_by=["dept"]))
    assert fm["age"][2] == (30 + 29) / 2  # Charlie (Eng) gets the Eng mean


def test_split_sizes_are_exact_and_repeatable(tmp_path):
    p = tmp_path / "big.csv"
    pl.DataFrame({"i": list(range(1000)), "g": [i % 10 for i in range(1000)], "label": [i % 2 for i in range(1000)]}).write_csv(p)
    pid, root = _load(str(p))
    a = _engine_df(pid, _ok_op(pid, root, "train_test_split", test_size=0.2, val_size=0.1, seed=1))
    counts = dict(a.group_by("split").len().rows())
    assert counts == {"train": 700, "val": 100, "test": 200}
    b = _engine_df(pid, _ok_op(pid, root, "train_test_split", test_size=0.2, val_size=0.1, seed=1))
    assert a["split"].to_list() == b["split"].to_list()
    strat = _engine_df(pid, _ok_op(pid, root, "train_test_split", test_size=0.2, stratify="label"))
    assert dict(strat.filter(pl.col("split") == "test").group_by("label").len().rows()) == {0: 100, 1: 100}
    grp = _engine_df(pid, _ok_op(pid, root, "train_test_split", test_size=0.3, group="g"))
    assert grp.group_by("g").agg(pl.col("split").n_unique())["split"].max() == 1  # a group never straddles splits


def test_fit_on_training_rows_only(tmp_path):
    p = tmp_path / "d.csv"
    pl.DataFrame({"x": [0.0, 10.0, 1000.0], "split": ["train", "train", "test"]}).write_csv(p)
    pid, root = _load(str(p))
    node = _ok_op(pid, root, "scale", columns=["x"], method="minmax", fit_on={"column": "split", "value": "train"})
    assert _engine_df(pid, node)["x"].to_list() == [0.0, 1.0, 100.0]  # the test row doesn't leak into min/max
    assert "learned on split=train" in projects[pid].nodes[node].operation.label


def test_recipes_reuse_learned_categories(people, tmp_path):
    pid, root = _load(people)
    node = _ok_op(pid, root, "one_hot_encode", columns=["dept"])
    recipe = client.post(f"/project/{pid}/recipe/save", json={"name": "onehot", "node_id": node}).json()
    other = tmp_path / "other.csv"
    pl.read_csv(people).filter(pl.col("dept") == "Eng").write_csv(other)  # only one category present
    r = client.post("/recipe/apply", json={"recipe_id": recipe["id"], "new_source_path": str(other), "force": True}).json()
    head = r["project"]["branches"]["main"]["head_node_id"]
    out = _engine_df(r["project"]["id"], head)
    assert {"dept_Eng", "dept_Mkt", "dept_Sales"} <= set(out.columns)  # same columns as at training time
    assert out["dept_Sales"].sum() == 0


def test_editing_an_earlier_step_refits(people):
    pid, root = _load(people)
    f = _ok_op(pid, root, "filter_rows", column="dept", operator="ne", value="Mkt")
    enc = _ok_op(pid, f, "one_hot_encode", columns=["dept"])
    assert "dept_Mkt" not in _engine_df(pid, enc).columns
    client.post(f"/project/{pid}/step/{f}/edit", json={"params": {"column": "dept", "operator": "ne", "value": "Sales"}})
    head = projects[pid].branches["main"].head_node_id
    cols = _engine_df(pid, head).columns
    assert "dept_Mkt" in cols and "dept_Sales" not in cols
