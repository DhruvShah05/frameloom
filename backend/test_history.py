"""History editing: edit / delete / insert a step, cherry-pick, merge, undo."""
import polars as pl
import pytest
from fastapi.testclient import TestClient

from backend.main import app, engine, projects

client = TestClient(app)


@pytest.fixture
def pid_chain(tmp_path):
    """people.csv with three steps on main: filter age>30, strip names, sort by id desc."""
    p = tmp_path / "people.csv"
    pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": [" Ann", "Bob ", "Cy", "Dee", "Eve"],
        "age": [25, 35, 45, 55, 65],
        "dept": ["a", "b", "a", "b", "a"],
    }).write_csv(p)
    pr = client.post("/project/load", json={"path": str(p)}).json()
    pid, root = pr["id"], pr["branches"]["main"]["head_node_id"]
    ids = [root]
    for op in [
        {"type": "filter_rows", "params": {"column": "age", "operator": "gt", "value": 30}},
        {"type": "text_transform", "params": {"columns": ["name"], "transform": "strip"}},
        {"type": "sort", "params": {"by": [{"column": "id", "descending": True}]}},
    ]:
        r = client.post(f"/project/{pid}/op", json={"parent_node_id": ids[-1], "operation": op})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return pid, ids


def head(pid, branch="main"):
    return projects[pid].branches[branch].head_node_id


def df_at(pid, node):
    return engine.build_plan(projects[pid], node).collect()


def labels(pid, branch="main"):
    p = projects[pid]
    out, cur = [], p.branches[branch].head_node_id
    while cur:
        out.insert(0, p.nodes[cur].operation.label)
        cur = p.nodes[cur].parent_id
    return out


def test_edit_a_middle_step_replays_the_rest(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    r = client.post(f"/project/{pid}/step/{f}/edit", json={"params": {"column": "age", "operator": "gt", "value": 50}})
    assert r.status_code == 200, r.text
    df = df_at(pid, head(pid))
    assert df["id"].to_list() == [5, 4]  # new filter, still sorted desc
    assert labels(pid) == ["Load Data", "Filter age > 50", "Strip name", "Sort by id ↓"]
    # The old chain still exists, and undo brings it back.
    client.post(f"/project/{pid}/undo")
    assert head(pid) == srt


def test_delete_a_middle_step(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    r = client.post(f"/project/{pid}/step/{f}/delete", json={})
    assert r.status_code == 200, r.text
    assert df_at(pid, head(pid))["id"].to_list() == [5, 4, 3, 2, 1]
    assert labels(pid) == ["Load Data", "Strip name", "Sort by id ↓"]


def test_notes_travel_with_replayed_steps(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    client.post(f"/project/{pid}/step/{srt}/note", json={"note": "newest first"})
    client.post(f"/project/{pid}/step/{f}/delete", json={})
    assert projects[pid].nodes[head(pid)].note == "newest first"


def test_insert_a_step_in_the_middle(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    r = client.post(f"/project/{pid}/step/{f}/insert",
                    json={"operation": {"type": "filter_rows", "params": {"column": "dept", "operator": "eq", "value": "a"}}})
    assert r.status_code == 200, r.text
    assert df_at(pid, head(pid))["id"].to_list() == [5, 3]
    assert len(labels(pid)) == 5


def test_rewrite_that_breaks_a_later_step_changes_nothing(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    before_nodes = len(projects[pid].nodes)
    # Dropping "name" breaks the later "strip name" step.
    r = client.post(f"/project/{pid}/step/{f}/insert", json={"operation": {"type": "drop_column", "params": {"columns": ["name"]}}})
    assert r.status_code == 400
    assert "Strip name" in r.json()["detail"]
    assert head(pid) == srt and len(projects[pid].nodes) == before_nodes


def test_edit_cell_survives_an_inserted_filter(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    row = client.get(f"/project/{pid}/data", params={"node_id": srt, "limit": 1}).json()["data"][0]  # id 5
    edit = client.post(f"/project/{pid}/op", json={"parent_node_id": srt, "operation": {
        "type": "edit_cell", "params": {"row": row, "column": "name", "value": "Evelyn"}}}).json()["id"]
    # Insert a step before the edit that removes the first two rows of its input.
    r = client.post(f"/project/{pid}/step/{f}/insert",
                    json={"operation": {"type": "filter_rows", "params": {"column": "id", "operator": "ge", "value": 4}}})
    assert r.status_code == 200, r.text
    df = df_at(pid, head(pid))
    assert dict(zip(df["id"], df["name"])) == {5: "Evelyn", 4: "Dee"}
    assert edit != head(pid)


def test_edit_cell_whose_row_disappears_blocks_the_rewrite(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    row = client.get(f"/project/{pid}/data", params={"node_id": srt, "limit": 1}).json()["data"][0]  # id 5
    client.post(f"/project/{pid}/op", json={"parent_node_id": srt, "operation": {
        "type": "edit_cell", "params": {"row": row, "column": "name", "value": "Evelyn"}}})
    r = client.post(f"/project/{pid}/step/{f}/edit", json={"params": {"column": "age", "operator": "lt", "value": 60}})
    assert r.status_code == 400 and "isn't in the data" in r.json()["detail"]


def test_cherry_pick_and_merge(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    assert client.post(f"/project/{pid}/branch", json={"name": "exp", "from_node_id": root}).status_code == 200
    a = client.post(f"/project/{pid}/op", json={"parent_node_id": root, "operation": {
        "type": "filter_rows", "params": {"column": "dept", "operator": "eq", "value": "b"}}}).json()["id"]
    assert projects[pid].head == "exp" and head(pid, "exp") == a
    # Copy main's "strip" step onto exp.
    r = client.post(f"/project/{pid}/cherry-pick", json={"node_ids": [strip], "branch": "exp"})
    assert r.status_code == 200, r.text
    assert df_at(pid, head(pid, "exp"))["name"].to_list() == ["Bob", "Dee"]
    # Merge exp into main: exp's two steps replay on top of main.
    # Merge exp into main: only exp's own filter replays - main already has
    # the strip step that exp copied from it.
    r = client.post(f"/project/{pid}/merge", json={"source": "exp", "into": "main"}).json()
    assert r["replayed"] == 1 and not r["fast_forward"]
    assert df_at(pid, head(pid))["id"].to_list() == [4, 2]
    assert labels(pid)[-1] == "Filter dept = b"
    # Merging again: nothing new.
    assert client.post(f"/project/{pid}/merge", json={"source": "exp", "into": "main"}).status_code == 400


def test_merge_fast_forward(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    client.post(f"/project/{pid}/branch", json={"name": "ahead", "from_node_id": srt})
    nxt = client.post(f"/project/{pid}/op", json={"parent_node_id": srt, "operation": {
        "type": "limit_rows", "params": {"n": 1}}}).json()["id"]
    r = client.post(f"/project/{pid}/merge", json={"source": "ahead", "into": "main"}).json()
    assert r["fast_forward"] and head(pid) == nxt


def test_notes_and_branch_delete(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    client.post(f"/project/{pid}/step/{f}/note", json={"note": "seniors only"})
    assert projects[pid].nodes[f].note == "seniors only"
    client.post(f"/project/{pid}/branch", json={"name": "tmp", "from_node_id": f})
    assert client.delete(f"/project/{pid}/branch/tmp").status_code == 400  # it's the current branch
    client.post(f"/project/{pid}/checkout", json={"target": "main"})
    assert client.delete(f"/project/{pid}/branch/tmp").status_code == 200
    assert "tmp" not in projects[pid].branches


def test_the_load_step_cannot_be_edited_or_deleted(pid_chain):
    pid, (root, *_rest) = pid_chain
    assert client.post(f"/project/{pid}/step/{root}/delete", json={}).status_code == 400
    assert client.post(f"/project/{pid}/step/{root}/edit", json={"params": {}}).status_code == 400


# ------------------------------------------------------------------ versions

def test_versions_pin_a_step_and_carry_metrics(pid_chain):
    pid, (root, f, strip, srt) = pid_chain
    r = client.post(f"/project/{pid}/versions", json={"name": "baseline", "message": "first clean pass"})
    assert r.status_code == 200, r.text
    assert r.json()["node_id"] == srt and r.json()["branch"] == "main"
    # Versions never move, even when the branch history is rewritten.
    client.post(f"/project/{pid}/step/{f}/delete", json={})
    assert projects[pid].versions["baseline"].node_id == srt
    assert df_at(pid, srt)["id"].to_list() == [5, 4, 3, 2]

    client.patch(f"/project/{pid}/versions/baseline", json={"metrics": {"val_auc": 0.81}})
    client.patch(f"/project/{pid}/versions/baseline", json={"metrics": {"f1": 0.7}, "notes": "xgboost default"})
    v = client.get(f"/project/{pid}/versions").json()[0]
    assert v["metrics"] == {"val_auc": 0.81, "f1": 0.7} and v["notes"] == "xgboost default"

    assert client.post(f"/project/{pid}/versions", json={"name": "baseline"}).status_code == 400
    assert client.post(f"/project/{pid}/versions", json={"name": "main"}).status_code == 400
    assert client.post(f"/project/{pid}/versions", json={"name": "bad name!"}).status_code == 400
    assert client.patch(f"/project/{pid}/versions/baseline", json={"metrics": {"x": [1, 2]}}).status_code == 400
    assert client.post(f"/project/{pid}/versions", json={"name": "raw", "node_id": root}).json()["node_id"] == root
    assert client.delete(f"/project/{pid}/versions/raw").status_code == 200
    assert "raw" not in projects[pid].versions


def test_resolve_refs(pid_chain):
    from backend.main import loom

    pid, (root, f, strip, srt) = pid_chain
    client.post(f"/project/{pid}/versions", json={"name": "v1", "node_id": f})
    p = projects[pid]
    assert loom.resolve(p, "v1") == f
    assert loom.resolve(p, "main") == srt
    assert loom.resolve(p, None) == srt
    assert loom.resolve(p, strip[:8]) == strip
    with pytest.raises(KeyError):
        loom.resolve(p, "nope")
