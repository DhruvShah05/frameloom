"""The Python API (import loom) and the command line."""
import os

import pandas as pd
import polars as pl
import pytest
from click.testing import CliRunner

import loom
from backend.cli import cli


@pytest.fixture
def csv(tmp_path):
    p = tmp_path / "sales.csv"
    pl.DataFrame({"id": [1, 2, 3, 4], "amount": [10.0, None, 30.0, 400.0], "region": ["n", "s", "n", "s"]}).write_csv(p)
    return str(p)


def test_open_steps_versions_and_load(csv):
    from backend.main import loom as core

    proj = loom.open(csv, name="sales-test")
    p = core.store.get(proj.id)
    core.add_step(p, core.head_node_id(p), {"type": "drop_missing", "params": {}})
    proj.save_version("v1", message="no missing amounts")
    core.add_step(p, core.head_node_id(p), {"type": "filter_rows", "params": {"column": "amount", "operator": "lt", "value": 100}})
    proj.save_version("v2", message="no outliers", val_auc=0.7)

    assert loom.load(proj, "v1")["id"].to_list() == [1, 3, 4]
    assert loom.load("sales-test", "v2")["id"].to_list() == [1, 3]
    as_pd = loom.load(csv, "v2", pandas=True)  # found by file path
    assert isinstance(as_pd, pd.DataFrame) and list(as_pd["id"]) == [1, 3]
    assert proj.df()["id"].to_list() == [1, 3]

    loom.log_metrics(proj, "v2", val_auc=0.83, f1=0.6)
    table = proj.versions()
    assert table["version"].to_list() == ["v1", "v2"]
    assert table.filter(pl.col("version") == "v2")["metric:val_auc"].item() == 0.83

    assert [h["label"] for h in proj.history()] == ["Load Data", "Drop rows with any empty", "Filter amount < 100"]
    assert "drop_nulls" in proj.code("v1") and "pl.read_csv" in proj.code("v1")
    out = proj.export(os.path.join(os.path.dirname(csv), "v2.parquet"), "v2")
    assert pl.read_parquet(out).height == 2
    assert "sales-test" in loom.projects()["name"].to_list()


def test_view_dataframes_without_opening_a_browser():
    pdf = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    proj = loom.view(pdf, name="from-pandas", open_browser=False)
    assert proj.df().to_dicts() == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    # The same frame again reuses the frozen input file.
    proj2 = loom.view(pl.from_pandas(pdf), open_browser=False)
    from backend.main import loom as core
    assert core.store.get(proj.id).source_path == core.store.get(proj2.id).source_path
    with pytest.raises(TypeError):
        loom.view(42, open_browser=False)


def test_view_starts_the_web_app(csv):
    proj = loom.view(csv, open_browser=False)
    url = proj.url
    assert url.startswith("http://localhost:") and proj.id in url
    import urllib.request
    with urllib.request.urlopen(url.split("/?")[0] + "/health") as r:
        assert r.status == 200
    assert "<iframe" in proj._repr_html_()


def test_changed_source_is_refused_unless_allowed(tmp_path):
    p = tmp_path / "live.csv"
    p.write_text("a\n1\n")
    proj = loom.open(str(p), snapshot=False)
    p.write_text("a\n1\n2\n")
    os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 10_000_000))
    with pytest.raises(loom.SourceChangedError):
        proj.df()
    assert proj.df(allow_changed_source=True).height == 2


def test_cli(csv, tmp_path):
    from backend.main import loom as core
    from backend.models import Operation, Recipe

    proj = loom.open(csv, name="cli-test")
    p = core.store.get(proj.id)
    core.add_step(p, core.head_node_id(p), {"type": "filter_rows", "params": {"column": "region", "operator": "eq", "value": "n"}})
    proj.save_version("north", message="north only")
    loom.log_metrics(proj, "north", rmse=1.5)
    runner = CliRunner()

    r = runner.invoke(cli, ["list"])
    assert r.exit_code == 0 and "cli-test" in r.output and "north" in r.output
    r = runner.invoke(cli, ["versions", "cli-test"])
    assert r.exit_code == 0 and "north only" in r.output and "rmse=1.5" in r.output
    out = tmp_path / "north.csv"
    r = runner.invoke(cli, ["export", "cli-test", "-v", "north", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert pl.read_csv(out)["id"].to_list() == [1, 3]
    r = runner.invoke(cli, ["code", proj.id[:8], "-v", "north"])
    assert r.exit_code == 0 and "pl.col('region') == 'n'" in r.output
    r = runner.invoke(cli, ["export", "cli-test", "-v", "nope", "-o", str(out)])
    assert r.exit_code != 0 and "not a version" in r.output

    recipe = Recipe(id="r1", name="north-only", operations=[Operation(**{**p.nodes[core.head_node_id(p)].operation.model_dump()})],
                    expected_schema=core.engine.get_schema(p, core.root_id(p)), origin_hash="unknown")
    core.store.save_recipe(recipe)
    r = runner.invoke(cli, ["recipes"])
    assert "north-only" in r.output
    run_out = tmp_path / "run.parquet"
    r = runner.invoke(cli, ["run", "north-only", csv, "-o", str(run_out)])
    assert r.exit_code == 0, r.output
    assert pl.read_parquet(run_out)["id"].to_list() == [1, 3]
    other = tmp_path / "other.csv"
    pl.DataFrame({"x": [1]}).write_csv(other)
    r = runner.invoke(cli, ["run", "north-only", str(other), "-o", str(run_out)])
    assert r.exit_code != 0 and "Missing column" in r.output
    r = runner.invoke(cli, ["--recipes"])  # old flag still works
    assert "north-only" in r.output
